import json
import os
from pathlib import Path
from dataclasses import asdict
from .workspace import WorkspaceState, Anchor, Evidence, Memory, Conflict, StateSnapshot


LIQUID_DIR = ".liquid"
STATE_FILE = "state.json"


def _ensure_dir(workspace_root: Path) -> Path:
    d = workspace_root / LIQUID_DIR
    d.mkdir(exist_ok=True)
    return d


def load(workspace_root: Path) -> WorkspaceState:
    path = _ensure_dir(workspace_root) / STATE_FILE
    if not path.exists():
        return WorkspaceState()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _from_dict(data)


def save(state: WorkspaceState, workspace_root: Path):
    path = _ensure_dir(workspace_root) / STATE_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2, ensure_ascii=False)


def _from_dict(data: dict) -> WorkspaceState:
    return WorkspaceState(
        anchors=[Anchor(**a) for a in data.get("anchors", [])],
        evidences=[Evidence(**e) for e in data.get("evidences", [])],
        memories=[Memory(**m) for m in data.get("memories", [])],
        conflicts=[Conflict(**c) for c in data.get("conflicts", [])],
        snapshots=[StateSnapshot(**s) for s in data.get("snapshots", [])],
        version=data.get("version", "0.1.0"),
        updated_at=data.get("updated_at", ""),
    )
