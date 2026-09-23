"""v1.7 证据老化回收（lifecycle sweep）单元测试。

督办项警示①落地：append-only 长期 horizon 退化 → 主动 lifecycle 冷归档。
守铁律：零丢失(archived≠删) / 可审计(archived_at) / 保留时序 / 禁向量。
"""
from datetime import datetime, timezone, timedelta

from liquid_loop.workspace import (
    WorkspaceState, Evidence, LIFECYCLE_TTL_EPS, LIFECYCLE_FLOOR_WEIGHT,
)


def _old_ts(days: int) -> str:
    """构造 days 天前的带 offset isoformat 时间戳（与 now() 格式一致）。"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _aging_evidence(state, anchor_id, eid, *, weight=0.1, recall_days=None, created_days=None, relation="support"):
    """构造一条处于老化候选态的证据并挂到 state.evidences。

    recall_days: 设置 last_recall_at（最后召回时间）为 N 天前；None=从未召回。
    created_days: 设置 timestamp（创建时间）为 N 天前；None=近期创建。
    """
    e = Evidence(
        id=eid, anchor_id=anchor_id, content=f"aging-{eid}",
        weight=weight, relation=relation,
        last_recall_at="" if recall_days is None else _old_ts(recall_days),
        timestamp=_old_ts(created_days) if created_days is not None else (datetime.now(timezone.utc)).isoformat(),
    )
    state.evidences.append(e)
    return e


def test_fresh_high_weight_kept():
    """新写入、高权重证据不应被归档。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    state.add_evidence(a.id, "fresh active evidence")  # weight=1.0
    res = state.lifecycle_sweep()
    assert res["swept"] == 0
    assert all(not e.archived for e in state.evidences)


def test_aging_unrecalled_swept():
    """长期未召回 + 权重地板 → 冷归档。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    _aging_evidence(state, a.id, "e1", weight=0.1, recall_days=200)  # 超 TTL
    res = state.lifecycle_sweep()
    assert res["swept"] == 1
    assert state.evidences[0].archived is True
    assert state.evidences[0].archived_at  # 留痕可审计


def test_never_recalled_old_created_swept():
    """从未召回 且 创建时间已超 TTL（真·陈年冷却证据）→ 冷归档。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    # 从没召回(last_recall_at 空)，但创建于 200 天前 → 用 timestamp 作老化判据
    _aging_evidence(state, a.id, "e2", weight=0.1, recall_days=None, created_days=200)
    res = state.lifecycle_sweep()
    assert res["swept"] == 1
    assert state.evidences[0].archived is True


def test_never_recalled_fresh_created_kept():
    """从未召回 但 近期创建（新记忆）→ 必须保留（修复 v1.7 误冻 bug）。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    # 从没召回(last_recall_at 空) + 默认权重 0.1 + 近期创建 → 不应被归档
    _aging_evidence(state, a.id, "e2b", weight=0.1, recall_days=None, created_days=1)
    res = state.lifecycle_sweep()
    assert res["swept"] == 0
    assert state.evidences[0].archived is False


def test_within_ttl_kept():
    """TTL 内（近期召回过）即便权重地板也保留。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    _aging_evidence(state, a.id, "e3", weight=0.1, recall_days=3)  # 远小于 TTL
    res = state.lifecycle_sweep()
    assert res["swept"] == 0


def test_active_weight_kept():
    """权重仍高（>floor）即便从未召回也保留（未退化）。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    _aging_evidence(state, a.id, "e4", weight=0.9, recall_days=None)
    res = state.lifecycle_sweep()
    assert res["swept"] == 0


def test_crystallized_evidence_protected():
    """被结晶引用的证据即便老化也不归档（保结晶血缘）。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    # 触发 private 结晶（同 content ≥2）
    state.add_evidence(a.id, "shared fact")
    state.add_evidence(a.id, "shared fact")
    assert len(state.memories) == 1
    protected_id = state.memories[0].evidence_ids[0]
    # 手动把该证据改到老化态
    pe = next(e for e in state.evidences if e.id == protected_id)
    pe.weight = 0.1
    pe.last_recall_at = _old_ts(200)
    res = state.lifecycle_sweep()
    assert protected_id not in res["swept_ids"]
    assert pe.archived is False


def test_contradiction_kept():
    """冲突证据零丢失优先，不参与老化归档。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    _aging_evidence(state, a.id, "c1", weight=0.1, recall_days=200, relation="contradiction")
    res = state.lifecycle_sweep()
    assert res["swept"] == 0


def test_register_recall_records_timestamp():
    """register_recall 命中证据应写入 last_recall_at。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    e = state.add_evidence(a.id, "will be recalled")
    assert e.last_recall_at == ""
    state.register_recall([e.id])
    assert e.last_recall_at  # 已被写入
    # 写入后即便权重被外部压低，因近期召回 → 不归档
    e.weight = 0.1
    res = state.lifecycle_sweep()
    assert e.id not in res["swept_ids"]


def test_sweep_idempotent():
    """已归档证据不被重复处理。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    _aging_evidence(state, a.id, "e5", weight=0.1, recall_days=200)
    r1 = state.lifecycle_sweep()
    r2 = state.lifecycle_sweep()
    assert r1["swept"] == 1
    assert r2["swept"] == 0  # 第二次无新归档


def test_add_evidence_default_weight_not_swept_on_write():
    """回归：add_evidence 写入默认权重(0.1)新证据，_on_evidence_added 触发 sweep 不应立即归档（v1.7 误冻 bug 复测）。"""
    state = WorkspaceState()
    a = state.add_anchor("k")
    e = state.add_evidence(a.id, "brand new default-weight evidence")  # weight=1.0 默认
    # 压低到地板以下模拟「从未召回的冷却证据」，但创建时间=now
    e.weight = 0.1
    # 手动再触发一次 sweep（模拟后续写入）
    res = state.lifecycle_sweep()
    assert e.id not in res["swept_ids"]
    assert e.archived is False


def test_constants_sane():
    """常量合理性自检（防误改导致永不动或永远动）。"""
    assert LIFECYCLE_TTL_EPS > 0
    assert 0 < LIFECYCLE_FLOOR_WEIGHT < 1.0
