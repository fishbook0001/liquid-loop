import fcntl
import json
import os
import shutil
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory,
    Conflict, StateSnapshot, AnchorRelation, AuditChain, now,
)


LIQUID_DIR = ".liquid"
STATE_FILE = "state.json"
AUDIT_FILE = "audit.log"
ARCHIVE_FILE = "archive.jsonl"
UNDO_DIR = "undo"
UNDO_KEEP = 5  # 写前快照轮转保留份数（state.json 2.5M×5 ≈ 12.5M）
UNDO_MIN_INTERVAL = 60  # 最小快照间隔(秒)：60秒内多次save只生成1次快照，减少写入风暴


def _ensure_dir(workspace_root: Path) -> Path:
    root = Path(workspace_root)
    # 标本三根因修复（2026-09-06）：防 .liquid 套娃。
    # cli.py 的 WORKSPACE = Path.cwd()，若在已存在的 .liquid 目录下跑 CLI，
    # 会把 cwd 当 root 再拼一层 .liquid → 生 .liquid/.liquid 嵌套幽灵。
    # 若 root 自身已命名为液环目录，则直接用 root 本身，不再二次拼接。
    if root.name == LIQUID_DIR:
        root.mkdir(exist_ok=True)
        return root
    d = root / LIQUID_DIR
    d.mkdir(exist_ok=True)
    return d


def get_audit_chain(workspace_root: Path) -> AuditChain:
    """获取审计链实例"""
    return AuditChain(str(_ensure_dir(workspace_root) / AUDIT_FILE))


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
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            s.add(json.loads(line)["id"])
                        except Exception as _e:
                            sys.stderr.write(f"[storage:warn] archive行解析失败，跳过: {_e}\n")
        _ARCHIVE_IDS = s
    return _ARCHIVE_IDS


def load(workspace_root: Path) -> WorkspaceState:
    """纯读取，无锁。并发安全由上层 locked_state 上下文保证。"""
    path = _ensure_dir(workspace_root) / STATE_FILE
    if not path.exists():
        return WorkspaceState()
    # 编码容错：state.json 可能因异常写入产生非 UTF-8 字节，
    # 首次严格解码失败时退回 replace 模式并记录审计，避免整文件不可读。
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except UnicodeDecodeError:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        with suppress(Exception):
            get_audit_chain(workspace_root).append(
                "state_load_encoding_recovery",
                f"unicode_decode_error_recovered_with_replace_at_{path}")
    state = _from_dict(data)
    # 版本同步：state.json 可能因版本升级而滞后，加载时对齐当前代码版本
    try:
        from . import __version__ as _cur_ver
        if getattr(state, 'version', None) != _cur_ver:
            state.version = _cur_ver
    except Exception:
        pass  # 版本同步失败不阻断加载
    return state


# ── 灾难性回退护栏（2026-08-29 根治 · 飞哥"先治本"）────────
# 静默失效族根治：部分内存态(近似空)一旦传进 save() 会原子覆盖全量磁盘态，
# 且因原子写"文件不半截"而难以察觉。本护栏在落盘前正对照磁盘全量，
# 若传入态相对磁盘全量灾难性缩水则拒绝落盘(保留磁盘 + CRITICAL 审计)，
# 使"全量被部分态覆盖"成为不可能事件。必须在 evict_expired/归档压实 之前调用，
# 使 archived 项仍计入传入态, 避免误拦合法归档。
GUARD_MIN = 10        # 磁盘全量低于此值不拦(冷启动/小工作区) — 坑37修复：从50降到10，保护小工作区
GUARD_RATIO = 0.1     # 传入态 < 磁盘*此比例 → 判定灾难性回退

class StateRegressionGuardError(Exception):
    """save() 拒绝灾难性状态回退时抛出；磁盘态保持不变。"""

def _state_total(state) -> int:
    return len(state.anchors) + len(state.evidences) + len(state.memories)


def _critical_anchor_ids(state) -> set:
    """提取关键锚点ID：stability >= 0.5 的高稳定性锚点（第二判据用）。"""
    return {a.id for a in getattr(state, 'anchors', [])
            if getattr(a, 'stability', 0) >= 0.5}


def _guard_regression(state, workspace_root):
    """落盘前置护栏: 拒绝把全量磁盘态覆盖成近似空态或丢失关键锚点。返回 None=通过。

    双判据：
    1. 总量判据：传入态 < 磁盘*GUARD_RATIO → 灾难性回退
    2. 关键锚点判据：磁盘中高stability(>=0.5)锚点在传入态中丢失 → 灾难性回退
    任一判据触发即拦截。仅在磁盘全量 >= GUARD_MIN 时启用。"""
    try:
        disk = load(workspace_root)
    except Exception:
        return  # 读不到磁盘(如首次写) → 不拦
    disk_total = _state_total(disk)
    if disk_total == 0:
        # 全新空工作区（首次写/冷启动）：无可覆盖全量，放行但留痕，
        # 使"在何处新建了库"可观测（标本三原先的无痕落盘通道 → 有审计痕迹）。
        with suppress(Exception):
            get_audit_chain(workspace_root).append(
                "state_save_fresh_workspace",
                f"disk_total=0 first_write at {workspace_root}")
        return
    if disk_total < GUARD_MIN:
        return  # 小工作区(<GUARD_MIN) → 不拦，保护小库误拦
    incoming_total = _state_total(state)
    # 判据1：总量灾难性缩水
    ratio_triggered = incoming_total < disk_total * GUARD_RATIO
    # 判据2：关键高stability锚点丢失
    disk_critical = _critical_anchor_ids(disk)
    incoming_ids = {a.id for a in getattr(state, 'anchors', [])}
    lost_critical = disk_critical - incoming_ids
    anchor_triggered = len(disk_critical) >= 3 and len(lost_critical) >= len(disk_critical) * 0.5
    if ratio_triggered or anchor_triggered:
        reason_parts = []
        if ratio_triggered:
            reason_parts.append(f"ratio incoming={incoming_total} < {GUARD_RATIO}*disk={disk_total}")
        if anchor_triggered:
            reason_parts.append(f"lost {len(lost_critical)}/{len(disk_critical)} critical anchors")
        reason = "; ".join(reason_parts)
        try:
            get_audit_chain(workspace_root).append(
                "state_save_guard_BLOCKED",
                f"{reason}")
        except Exception as _e:
            sys.stderr.write(f"[storage:warn] 审计链写入失败(不阻断主错误): {_e}\n")
        raise StateRegressionGuardError(
            f"refuse catastrophic state regression: {reason} (disk preserved; see undo snapshot)")


# ── undo checkpoint（m60 建议3 落地 · 2026-08-29）──
# DeepTutor 的 undo checkpoint 借鉴：每次写前把当前 state.json 快照到
# .liquid/undo/undo-<ts>.json（轮转保留 UNDO_KEEP 份），配合 list_undo /
# restore_undo 提供「后悔药」。与审计链（audit.log 哈希链）互补：
# 审计链证明"发生过什么"，undo 栈允许"回到某次写之前"。


def _undo_dir(workspace_root: Path) -> Path:
    d = _ensure_dir(workspace_root) / UNDO_DIR
    d.mkdir(exist_ok=True)
    return d


def _snapshot_before_save(workspace_root: Path) -> str | None:
    """写前快照：把当前 state.json 复制为 undo-<ts>.json，轮转清理旧份。

    v2.0.6修复：加最小时间间隔(UNDO_MIN_INTERVAL=60s)，60秒内多次save只生成1次快照，
    根除"1分钟10次save=10个2.5MB快照=25MB写入"的写入风暴。
    返回快照时间戳；无现存 state.json（首次写）或复制失败返回 None。
    由 save() 在覆盖前调用（save 持有锁，快照与覆盖天然串行）。
    """
    src = _ensure_dir(workspace_root) / STATE_FILE
    if not src.exists():
        return None
    # 时间间隔控制：60秒内不重复生成快照
    undo_dir = _undo_dir(workspace_root)
    try:
        snaps = sorted(undo_dir.glob("undo-*.json"))
        if snaps and time.time() - snaps[-1].stat().st_mtime < UNDO_MIN_INTERVAL:
            return None
    except OSError:
        pass
    # 微秒级唯一（macOS strftime 不支持 %f，手工拼微秒），避免同秒多次写互相覆盖快照
    ts = time.strftime("%Y%m%d%H%M%S") + f"{int(time.time() * 1_000_000) % 1_000_000:06d}"
    dst = undo_dir / f"undo-{ts}.json"
    try:
        shutil.copy2(src, dst)
    except OSError:
        return None  # 快照失败不阻断写（fail-open，零爆破半径）
    # 轮转：仅保留最近 UNDO_KEEP 份
    snaps = sorted(undo_dir.glob("undo-*.json"))
    for old in snaps[:-UNDO_KEEP]:
        try:
            old.unlink()
        except OSError as _e:
            sys.stderr.write(f"[storage:warn] undo快照删除失败: {_e}\n")
    return ts


def list_undo(workspace_root: Path) -> list[dict]:
    """列出可用写前快照（按时间倒序），供上层展示/选择恢复点。"""
    out = []
    for p in sorted(_undo_dir(workspace_root).glob("undo-*.json"), reverse=True):
        try:
            sz = p.stat().st_size
        except OSError:
            sz = 0
        out.append({"ts": p.stem.replace("undo-", ""), "path": str(p), "bytes": sz})
    return out


def restore_undo(workspace_root: Path, ts: str) -> dict:
    """把指定写前快照恢复为当前 state.json（覆盖式，自动先快照当前态）。

    安全：恢复前先对当前状态做一次写前快照（后悔药套后悔药）；全程持文件锁，
    与 locked_state 串行，杜绝并发丢写。ts 取 list_undo 返回的时间戳（容错 yyyyMMddHHmmss）。
    """
    snap = _undo_dir(workspace_root) / f"undo-{ts}.json"
    if not snap.exists():
        return {"ok": False, "error": f"快照不存在: {ts}（可用 list_undo 查看）"}
    with _file_lock(str(_lock_path(workspace_root)), "w"):
        # 1) 先快照当前态（若有）
        cur_ts = _snapshot_before_save(workspace_root)
        # 2) 覆盖恢复
        dst = _ensure_dir(workspace_root) / STATE_FILE
        shutil.copy2(snap, dst)
        return {"ok": True, "restored_ts": ts, "previous_snapshot": cur_ts}


def save(state: WorkspaceState, workspace_root: Path):
    """原子写（temp+rename）：崩溃永不留下半截文件。锁由 locked_state 持有。
    前置护栏 _guard_regression：拒绝灾难性回退(部分态覆盖全量)，见 storage 根治段。"""
    state.updated_at = now()
    _guard_regression(state, workspace_root)  # 根治：部分态静默覆盖全量 → 拒绝, 保留磁盘
    path = _ensure_dir(workspace_root) / STATE_FILE
    # m60 建议3 落地：写前快照（undo checkpoint），覆盖前保留当前态后悔药
    _snapshot_before_save(workspace_root)
    # 审计链：记录并回写哈希（d807 Temporal事件溯源落地：支持pending_audit_event记录具体操作类型）
    audit = get_audit_chain(workspace_root)
    _pending = getattr(state, 'pending_audit_event', None)
    if _pending and isinstance(_pending, dict) and _pending.get('type'):
        _evt_type = _pending['type']
        _evt_detail = _pending.get('detail', '')
        _counts = f"anchors={len(state.anchors)}_evidences={len(state.evidences)}_memories={len(state.memories)}_relations={len(state.relations)}"
        audit_hash = audit.append(_evt_type, f"{_evt_detail}_{_counts}" if _evt_detail else _counts)
        state.pending_audit_event = None  # 消费后清空
    else:
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
    # 锁句柄需跨整个 yield 段持有，由 finally 手动 LOCK_UN+close；
    # 用 with 会提前解锁 → 失去「load→modify→save 整段原子」语义。
    lk = open(d / "state.lock", "w")  # noqa: SIM115
    fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        st = load(workspace_root)
        yield st
        save(st, workspace_root)
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()


def _safe_kwargs(cls, data: dict) -> dict:
    """过滤掉 dataclass 不接受的字段，防止版本升级时字段不兼容导致加载失败。"""
    import inspect
    try:
        sig = inspect.signature(cls.__init__)
        accepted = set(sig.parameters.keys())
        return {k: v for k, v in data.items() if k in accepted}
    except Exception:
        return data


def _from_dict(data: dict) -> WorkspaceState:
    return WorkspaceState(
        anchors=[Anchor(**_safe_kwargs(Anchor, a)) for a in data.get("anchors", [])],
        evidences=[Evidence(**_safe_kwargs(Evidence, e)) for e in data.get("evidences", [])],
        memories=[Memory(**_safe_kwargs(Memory, m)) for m in data.get("memories", [])],
        conflicts=[Conflict(**_safe_kwargs(Conflict, c)) for c in data.get("conflicts", [])],
        relations=[AnchorRelation(**_safe_kwargs(AnchorRelation, r)) for r in data.get("relations", [])],
        snapshots=[StateSnapshot(**_safe_kwargs(StateSnapshot, s)) for s in data.get("snapshots", [])],
        version=data.get("version", "0.4.0"),
        updated_at=data.get("updated_at", ""),
        audit_chain_hash=data.get("audit_chain_hash", "genesis"),
        audit_prev_hash=data.get("audit_prev_hash", ""),
        self_refine_probes=data.get("self_refine_probes", []),
        self_refine_results=data.get("self_refine_results", []),
        self_refine_repair_count=data.get("self_refine_repair_count", 0),
        _iteration=data.get("_iteration", 0),  # Layer-1 修复：τ=有效迭代计数必须持久化，否则重启后归零→强化门控恒真→时间衰减失效
        # 【v0.5.0】CPE 正则化状态
        regularized_evidences=data.get("regularized_evidences", []),
        blocked_evidences=data.get("blocked_evidences", []),
        cpe_erosion_warnings=data.get("cpe_erosion_warnings", []),
        cpe_regularization_count=data.get("cpe_regularization_count", 0),
        # 【v2.0.5】即时觉醒模块
        instant_events=data.get("instant_events", []),
        instant_awareness_stats=data.get("instant_awareness_stats", {"total_events": 0, "detected": 0, "awareness_rate": 0.0}),
        instant_recent_ops=data.get("instant_recent_ops", []),
    )
