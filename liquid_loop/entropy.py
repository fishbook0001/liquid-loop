from datetime import datetime, timezone, timedelta
from .workspace import WorkspaceState


from contextlib import contextmanager

@contextmanager
def temp_attr(obj, attr: str, new_val):
    """临时修改对象属性，退出时自动还原（异常安全）"""
    old_val = getattr(obj, attr)
    setattr(obj, attr, new_val)
    try:
        yield
    finally:
        setattr(obj, attr, old_val)

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


# 【借鉴KFG】四维熵值分量

def value_decay_entropy(state: WorkspaceState) -> float:
    """锚点价值衰减熵。0=全部高价值, 1=全部低价值 — 纯函数，不改 state"""
    if not state.anchors:
        return 0.0
    scores = []
    for a in state.anchors:
        evs = [e for e in state.evidences if e.anchor_id == a.id]
        # 快照+还原，保证不修改 state
        orig_score = a.value_score
        orig_strength = a.anchor_strength
        score = a.decay_value(evidence_count=len(evs))
        a.value_score = orig_score
        a.anchor_strength = orig_strength
        scores.append(score)
    return 1.0 - (sum(scores) / len(scores))


def strength_entropy(state: WorkspaceState) -> float:
    """锚定强度熵。0=全部强锚定, 1=全部弱锚定 — 纯函数，不改 state"""
    if not state.anchors:
        return 0.0
    strengths = [a.anchor_strength for a in state.anchors]
    return 1.0 - (sum(strengths) / len(strengths))


# ==============================================================================
# 【新增 v0.5.0】CPE 能力侵蚀检测 — 借鉴 UIUC CPE 论文 (arXiv:2605.09315)
#
# CPE 能力侵蚀三大表现（§1）：
#   1. 回顾性衰退（Retrospective decay）：新证据覆盖旧锚点 → value_score 断崖
#   2. 策略漂移（Behavioral drift）：锚点属性突变 → stability 在单次更新中大幅波动
#   3. 泛化崩塌（Generalization erosion）：证据之间一致性下降 → cognitive_stage 降级
# ==============================================================================

def retrospective_decay_entropy(state: WorkspaceState) -> float:
    """回顾性衰退熵。0=无衰退, 1=大规模衰退（CPE §1: Retrospective decay）

    语义对齐 CPE 定义：新知识（证据）加入后，旧锚点基线能力（value_score）的下降比例。
    维护每个锚点的 baseline_value_score（首次结晶/人工设定时的基线），对比当前值。
    """
    if not state.anchors or not state.evidences:
        return 0.0
    decay_scores = []
    for a in state.anchors:
        evs = [e for e in state.evidences if e.anchor_id == a.id]
        if len(evs) < 2:
            continue
        # 基线：锚点创建时或首次结晶时的 value_score（若无记录，用当前值的 1.1 倍估算）
        baseline = getattr(a, "baseline_value_score", None)
        if baseline is None:
            baseline = a.value_score * 1.1
            a.baseline_value_score = baseline
        # 当前衰减后的价值
        current = a.decay_value(evidence_count=len(evs))
        if baseline > 0:
            ratio = max(0, 1 - current / baseline)
            decay_scores.append(ratio)
    return sum(decay_scores) / len(decay_scores) if decay_scores else 0.0


def behavioral_drift_entropy(state: WorkspaceState) -> float:
    """策略漂移熵。0=无漂移, 1=剧烈漂移（CPE §1: Behavioral policy drift）

    语义：锚点锚定强度在最近一次证据更新前后的变化幅度。
    使用上下文管理器保证不修改 state。
    """
    if not state.anchors:
        return 0.0
    drifts = []
    for a in state.anchors:
        evs = [e for e in state.evidences if e.anchor_id == a.id]
        if len(evs) < 2:
            continue
        evs_sorted = sorted(evs, key=lambda x: x.timestamp, reverse=True)
        with temp_attr(a, "anchor_strength", a.anchor_strength):
            old_count = len(evs_sorted) - 1
            new_count = len(evs_sorted)
            old_strength = a.recalc_strength(old_count)
            new_strength = a.recalc_strength(new_count)
            drifts.append(abs(new_strength - old_strength))
    return sum(drifts) / len(drifts) if drifts else 0.0


def generalization_erosion_entropy(state: WorkspaceState) -> float:
    """泛化崩塌熵。0=一致性高, 1=完全不一致（CPE §1: Generalization erosion）

    测量逻辑：同锚点下证据之间的关键词重叠度越低→一致性越差→崩塌风险越高。
    """
    if not state.anchors:
        return 0.0
    erosion_scores = []
    for a in state.anchors:
        evs = [e for e in state.evidences if e.anchor_id == a.id]
        if len(evs) < 2:
            continue
        # 两两计算重叠度
        overlaps = []
        for i in range(len(evs)):
            for j in range(i + 1, len(evs)):
                if evs[i].content and evs[j].content:
                    from .workspace import _keyword_overlap
                    overlaps.append(_keyword_overlap(evs[i].content, evs[j].content, state.overlap_cache))
        if overlaps:
            avg_overlap = sum(overlaps) / len(overlaps)
            # 重叠度越低 → 崩塌熵越高
            erosion_scores.append(1.0 - avg_overlap)
    return sum(erosion_scores) / len(erosion_scores) if erosion_scores else 0.0


def cpe_dimension_entropy(state: WorkspaceState) -> dict:
    """CPE 三维能力侵蚀熵汇总。"""
    return {
        "retrospective_decay": retrospective_decay_entropy(state),
        "behavioral_drift": behavioral_drift_entropy(state),
        "generalization_erosion": generalization_erosion_entropy(state),
    }


# ==============================================================================
# 综合熵值（八维加权 — CPE三维 + 原六维去重后五维 = 八维）
# ==============================================================================

def calculate(state: WorkspaceState,
              w_drift: float = 0.15, w_conflict: float = 0.10,
              w_frag: float = 0.10, w_gap: float = 0.10,
              w_decay: float = 0.10, w_strength: float = 0.10,
              w_retro: float = 0.15, w_behavior: float = 0.10,
              w_generalize: float = 0.10) -> float:
    """综合熵值 0.0-1.0（八维加权 — 新增CPE三维）

    CPE 三维权重合计 0.35，与原五维（0.65）形成均衡。
    """
    return (
        w_drift * anchor_drift(state) +
        w_conflict * conflict_density(state) +
        w_frag * evidence_fragmentation(state) +
        w_gap * activity_gap(state) +
        w_decay * value_decay_entropy(state) +
        w_strength * strength_entropy(state) +
        w_retro * retrospective_decay_entropy(state) +
        w_behavior * behavioral_drift_entropy(state) +
        w_generalize * generalization_erosion_entropy(state)
    )


def calculate_detail(state: WorkspaceState) -> dict:
    """返回八维详细熵值（用于 display 和诊断）"""
    return {
        "anchor_drift": anchor_drift(state),
        "conflict_density": conflict_density(state),
        "evidence_fragmentation": evidence_fragmentation(state),
        "activity_gap": activity_gap(state),
        "value_decay": value_decay_entropy(state),
        "strength": strength_entropy(state),
        "cpe_retrospective_decay": retrospective_decay_entropy(state),
        "cpe_behavioral_drift": behavioral_drift_entropy(state),
        "cpe_generalization_erosion": generalization_erosion_entropy(state),
        "combined": calculate(state),
    }