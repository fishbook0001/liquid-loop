import json
import os
from pathlib import Path
from dataclasses import asdict
from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory,
    Conflict, StateSnapshot, AnchorRelation, AuditChain, now,
)


LIQUID_DIR = ".liquid"
STATE_FILE = "state.json"
AUDIT_FILE = "audit.log"
ARCHIVE_FILE = "archive.jsonl"


def _ensure_dir(workspace_root: Path) -> Path:
    d = workspace_root / LIQUID_DIR
    d.mkdir(exist_ok=True)
    return d


def get_audit_chain(workspace_root: Path) -> AuditChain:
    """获取审计链实例"""
    return AuditChain(str(_ensure_dir(workspace_root) / AUDIT_FILE))

import fcntl
from contextlib import contextmanager

@contextmanager
def _file_lock(filepath: str, mode: str = "w"):
    """跨平台文件锁（fcntl，Linux/macOS）"""
    with open(filepath, mode) as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def _lock_path(workspace_root: Path) -> Path:
    return _ensure_dir(workspace_root) / "state.lock"


def _append_archive(workspace_root: Path, evidences) -> None:
    """08-18 工程化修复：归档证据追加到 archive.jsonl（历史档案，不参与活跃 load）。
    幂等：已存在于 archive 的 evidence 跳过（防崩溃窗口重复行）。"""
    d = _ensure_dir(workspace_root)
    ids = _archive_id_set(workspace_root)
    with open(d / ARCHIVE_FILE, "a", encoding="utf-8") as f:
        for e in evidences:
            if e.id in ids:
                continue
            f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
            ids.add(e.id)


_ARCHIVE_IDS: set | None = None  # 进程内缓存，避免每次 save 重读 archive


def _archive_id_set(workspace_root: Path) -> set:
    """archive.jsonl 现有 id 集合（跨进程各自首次读一次，随 append 增量更新）。"""
    global _ARCHIVE_IDS
    if _ARCHIVE_IDS is None:
        s: set = set()
        p = _ensure_dir(workspace_root) / ARCHIVE_FILE
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            s.add(json.loads(line)["id"])
                        except Exception:
                            pass
        _ARCHIVE_IDS = s
    return _ARCHIVE_IDS


def load(workspace_root: Path) -> WorkspaceState:
    """纯读取，无锁。并发安全由上层 locked_state 上下文保证。"""
    path = _ensure_dir(workspace_root) / STATE_FILE
    if not path.exists():
        return WorkspaceState()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _from_dict(data)


def save(state: WorkspaceState, workspace_root: Path):
    """原子写（temp+rename）：崩溃永不留下半截文件。锁由 locked_state 持有。"""
    state.updated_at = now()
    path = _ensure_dir(workspace_root) / STATE_FILE
    # 审计链：记录并回写哈希
    audit = get_audit_chain(workspace_root)
    audit_hash = audit.append("state_save",
        f"anchors={len(state.anchors)}_evidences={len(state.evidences)}"
        f"_memories={len(state.memories)}_relations={len(state.relations)}"
    )
    state.audit_prev_hash = state.audit_chain_hash
    state.audit_chain_hash = audit_hash
    state.evict_expired()  # 蒸馏 #202：落盘前回收过期临时记忆（集中清理点，向后兼容）
    # 08-18 工程化修复：归档压实——archived 证据迁出主 state.json 至 archive.jsonl，
    # 活跃集只保留非归档证据（缓解全量 load/save 膨胀；recall 侧 A1 已排除 archived）。
    _active, _archived = [], []
    for e in state.evidences:
        (_archived if getattr(e, "archived", False) else _active).append(e)
    if _archived:
        _append_archive(workspace_root, _archived)
        state.evidences = _active
    data = asdict(state)
    # overlap_cache 仅为运行时熵计算缓存，键为 tuple，不可 JSON 序列化，且不具持久价值
    data.pop("overlap_cache", None)
    # canon_fn 是运行时注入的可替换投影层（可能为不可序列化的函数），不持久化；
    # 需用时由调用方重新注入。不 pop 会导致 json.dump 对函数崩溃（P0 隐患）。
    data.pop("canon_fn", None)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # POSIX 原子替换，读端永不看到半截文件


@contextmanager
def locked_state(workspace_root: Path):
    """单一锁层：排他锁跨 load→modify→save 整段，根除并发丢写(P0)。
    跨进程安全（fcntl 建议锁）；server 与批量喂入脚本共用同一把锁。"""
    d = _ensure_dir(workspace_root)
    lk = open(d / "state.lock", "w")
    fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        st = load(workspace_root)
        yield st
        save(st, workspace_root)
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()


def _from_dict(data: dict) -> WorkspaceState:
    return WorkspaceState(
        anchors=[Anchor(**a) for a in data.get("anchors", [])],
        evidences=[Evidence(**e) for e in data.get("evidences", [])],
        memories=[Memory(**m) for m in data.get("memories", [])],
        conflicts=[Conflict(**c) for c in data.get("conflicts", [])],
        relations=[AnchorRelation(**r) for r in data.get("relations", [])],
        snapshots=[StateSnapshot(**s) for s in data.get("snapshots", [])],
        version=data.get("version", "0.4.0"),
        updated_at=data.get("updated_at", ""),
        audit_chain_hash=data.get("audit_chain_hash", "genesis"),
        audit_prev_hash=data.get("audit_prev_hash", ""),
        self_refine_probes=data.get("self_refine_probes", []),
        self_refine_results=data.get("self_refine_results", []),
        self_refine_repair_count=data.get("self_refine_repair_count", 0),
        _iteration=data.get("_iteration", 0),  # Layer-1 修复：τ=有效迭代计数必须持久化，否则重启后归零→强化门控恒真→时间衰减失效
    )