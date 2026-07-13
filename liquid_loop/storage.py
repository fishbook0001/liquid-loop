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

def load(workspace_root: Path) -> WorkspaceState:
    path = _ensure_dir(workspace_root) / STATE_FILE
    if not path.exists():
        return WorkspaceState()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _from_dict(data)


def save(state: WorkspaceState, workspace_root: Path):
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
    data = asdict(state)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
    )