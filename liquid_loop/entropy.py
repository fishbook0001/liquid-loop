from datetime import datetime, timezone, timedelta
from .workspace import WorkspaceState


def anchor_drift(state: WorkspaceState) -> float:
    """所有 Anchor 的 stability 偏离 1.0 的均值。0=全稳, 1=全漂"""
    if not state.anchors:
        return 0.0
    return sum(1.0 - a.stability for a in state.anchors) / len(state.anchors)


def conflict_density(state: WorkspaceState) -> float:
    """冲突密度。0=无冲突, 1=每锚点一个冲突"""
    if not state.anchors:
        return 0.0
    return min(len(state.conflicts) / len(state.anchors), 1.0)


def evidence_fragmentation(state: WorkspaceState) -> float:
    """孤立 Evidence 占比。0=全部有同伴, 1=全部孤立"""
    if not state.evidences:
        return 0.0
    # 按 anchor_id 分组
    groups: dict[str, int] = {}
    for e in state.evidences:
        groups[e.anchor_id] = groups.get(e.anchor_id, 0) + 1
    orphaned = sum(1 for count in groups.values() if count == 1)
    return orphaned / len(groups) if groups else 0.0


def activity_gap(state: WorkspaceState) -> float:
    """距最新 Evidence 的天数。0=今天有活动, 1=超过7天无活动"""
    if not state.evidences:
        return 1.0
    latest = max(e.timestamp for e in state.evidences)
    try:
        latest_dt = datetime.fromisoformat(latest)
    except ValueError:
        return 1.0
    gap = datetime.now(timezone.utc) - latest_dt
    return min(gap / timedelta(days=7), 1.0)


def calculate(state: WorkspaceState,
              w_drift: float = 0.25, w_conflict: float = 0.25,
              w_frag: float = 0.25, w_gap: float = 0.25) -> float:
    """综合熵值 0.0-1.0"""
    return (
        w_drift * anchor_drift(state) +
        w_conflict * conflict_density(state) +
        w_frag * evidence_fragmentation(state) +
        w_gap * activity_gap(state)
    )

# 兼容别名
calculate_entropy = calculate
