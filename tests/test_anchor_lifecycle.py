"""锚点生命周期机制补强单元测试（2026-09-22 飞哥拍板·全量深做）。

覆盖：缺1 TTL/寿命、缺2 主动探活、缺3 版本快照(old-logits)、
缺4 陈旧度常驻评分、缺5 合并/分裂；以及零丢失 / 向后兼容 / dry-run 安全。
"""
import pytest
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from liquid_loop.workspace import (
    WorkspaceState, Anchor, Evidence, now,
    ANCHOR_STALE_FLOOR, ANCHOR_DEAD_GRACE_DAYS, ANCHOR_SPLIT_CAP,
)
from liquid_loop.storage import save, load


def _old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_anchor_ttl_default_never_expires():
    a = Anchor(name="x")
    assert a.ttl_days == 0.0
    assert a.expires_at == ""
    assert a.is_expired() is False


def test_anchor_ttl_expiry():
    a = Anchor(name="x", ttl_days=1.0)
    a.expires_at = _old(5)  # 5 天前到期
    assert a.is_expired() is True
    a.expires_at = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    assert a.is_expired() is False


def test_add_anchor_sets_version_and_staleness():
    s = WorkspaceState()
    a = s.add_anchor("c≥1")
    assert a.version_hash  # 非空哈希
    assert 0.0 <= a.staleness_score <= 1.0
    assert a.expires_at == ""  # 默认永不过期（向后兼容）


def test_version_snapshot_and_drift():
    s = WorkspaceState()
    a = s.add_anchor("c≥1")
    e1 = s.add_evidence(a.id, "证据1")   # 记录 anchor_version_hash
    assert e1.anchor_version_hash == a.version_hash
    # 模拟合并/分裂改写：更新锚点状态哈希（old-logits 漂移）
    a.evidence_ids.append("ghost")
    a.version_hash = a.snapshot_version()
    drift = s.anchor_version_drift()
    assert e1.id in drift


def test_staleness_recompute_fresh_vs_dead():
    fresh = Anchor(name="f", last_accessed=now(), access_count=10,
                   evidence_ids=["e1", "e2", "e3", "e4", "e5"])
    fresh.recalc_staleness()
    assert fresh.staleness_score < 0.2  # 鲜活
    dead = Anchor(name="d")  # 无访问/无证据
    dead.recalc_staleness()
    assert dead.staleness_score >= 0.99  # 死


def test_probe_liveness_levels():
    dead = Anchor(name="d")
    dead.recalc_staleness()
    assert dead.probe() == "dead"
    assert dead.last_probed  # 已刷新探活时间戳
    fresh = Anchor(name="f", last_accessed=now(), access_count=10,
                   evidence_ids=["e1", "e2", "e3", "e4", "e5"])
    fresh.recalc_staleness()
    assert fresh.probe() == "active"


def test_lifecycle_sweep_dry_run_then_apply():
    s = WorkspaceState()
    a = s.add_anchor("old_dead")
    a.created_at = _old(ANCHOR_DEAD_GRACE_DAYS + 10)  # 超年龄门槛
    a.access_count = 0
    a.evidence_ids = []
    a.recalc_staleness()  # -> 1.0
    r1 = s.anchor_lifecycle_sweep()  # 默认 dry_run
    assert a.id in r1["would_archive"]
    assert r1["applied"] == 0
    assert a.archived is False
    r2 = s.anchor_lifecycle_sweep(dry_run=False)
    assert r2["applied"] == 1
    assert a.archived is True
    assert a.archived_at
    assert a.liquidity == "archived"
    r3 = s.anchor_lifecycle_sweep(dry_run=False)  # 幂等
    assert r3["applied"] == 0


def test_lifecycle_sweep_skips_fresh_anchor():
    s = WorkspaceState()
    a = s.add_anchor("fresh")  # 新建未挂证据，但年龄=0 < 门槛
    r = s.anchor_lifecycle_sweep(dry_run=False)
    assert a.id not in r["would_archive"]
    assert a.archived is False


def test_merge_similar_anchors_dry_and_apply():
    s = WorkspaceState()
    a1 = s.add_anchor("液环核心", description="记忆引擎")
    a2 = s.add_anchor("液环核心", description="记忆引擎")  # 同名相似
    e1 = s.add_evidence(a1.id, "e-content-1")
    e2 = s.add_evidence(a2.id, "e-content-2")
    r1 = s.merge_similar_anchors()
    assert len(r1["merged"]) >= 1
    assert r1["applied"] == 0  # dry
    assert a2.archived is False
    r2 = s.merge_similar_anchors(dry_run=False)
    assert r2["applied"] == 1
    assert a2.archived is True
    assert a2.superseded_by == a1.id
    assert set(a1.evidence_ids) >= {e1.id, e2.id}  # 证据并集
    # Evidence.anchor_id 已重指派到存活锚点
    assert all(e.anchor_id == a1.id for e in s.evidences if e.id in a1.evidence_ids)


def test_split_oversized_anchors():
    s = WorkspaceState()
    a = s.add_anchor("巨型锚点")
    ev_ids = []
    for i in range(ANCHOR_SPLIT_CAP + 20):
        e = s.add_evidence(a.id, f"fact-{i}")
        ev_ids.append(e.id)
    r1 = s.split_oversized_anchors()
    assert r1["applied"] == 0  # dry
    r2 = s.split_oversized_anchors(dry_run=False)
    assert r2["applied"] >= 1
    children = [x for x in s.anchors if x.split_from == a.id]
    assert children
    # 证据不重复分配：父(parts[0]) + 各子(parts[1:]) 拼接 == 全集
    all_assigned = set(a.evidence_ids)
    for c in children:
        all_assigned |= set(c.evidence_ids)
    assert all_assigned == set(ev_ids)
    # Evidence.anchor_id 与持有它的锚点一致
    owner = {eid: aid for aid, an in [(a.id, a)] + [(c.id, c) for c in children]
             for eid in an.evidence_ids}
    for e in s.evidences:
        if e.id in ev_ids:
            assert e.anchor_id == owner[e.id]


def test_active_anchors_filters_archived():
    s = WorkspaceState()
    a = s.add_anchor("liquid")
    assert a in s.active_anchors()
    a.archived = True
    assert a not in s.active_anchors()


def test_backward_compat_roundtrip():
    """生产安全：save/load 后新字段有合理默认/值，旧 state.json 不崩。"""
    s = WorkspaceState()
    a = s.add_anchor("兼容锚点")
    s.add_evidence(a.id, "内容")
    root = Path(tempfile.mkdtemp())
    save(s, root)
    s2 = load(root)
    la = next(x for x in s2.anchors if x.id == a.id)
    assert la.ttl_days == 0.0
    assert la.expires_at == ""
    assert la.version_hash
    assert la.staleness_score >= 0.0
    assert la.archived is False
    assert la.liveness in ("unknown", "active", "dormant", "dead")
    # 新字段缺省的反序列化（模拟旧 state.json 无这些 key）
    legacy = {"id": "x", "name": "legacy", "evidence_ids": []}
    a3 = Anchor(**legacy)
    assert a3.ttl_days == 0.0 and a3.version_hash == "" and a3.staleness_score == 0.0


def test_constants_sane():
    assert 0 < ANCHOR_STALE_FLOOR < 1.0
    assert ANCHOR_DEAD_GRACE_DAYS > 0
    assert ANCHOR_SPLIT_CAP > 0
