"""v0.9.3 多智能体授权原语无死角测试。

覆盖：读隔离(list_for) / 删除授权(delete_as) / 一致解散(dissolve_as)。
复现并固化「共识误删事故」修复，确保授权逻辑是单一事实源（库核心），
而非各接入层 wrapper 重复实现。
"""
import tempfile
from pathlib import Path

import pytest

from liquid_loop import WorkspaceState


@pytest.fixture
def ws():
    s = WorkspaceState()
    a = s.add_anchor("fact")
    ev_vera = s.add_evidence(a, "vera 知道 X", agent_id="vera")
    ev_trae = s.add_evidence(a, "trae 知道 Y", agent_id="trae")
    # vera 私有结晶（由 vera 自己的 2 条证据成核）
    ev_vera2 = s.add_evidence(a, "vera 知道 X（佐证）", agent_id="vera")
    priv = s.add_memory("vera 的私有结论", [ev_vera.id, ev_vera2.id])
    priv.scope = "private"
    priv.contributors = ["vera"]
    # 跨主体共识结晶（vera × trae）
    cons = s.add_memory("vera 与 trae 共同结论", [ev_vera.id, ev_trae.id])
    cons.scope = "consensus"
    cons.contributors = ["vera", "trae"]
    return s, dict(a=a, ev_vera=ev_vera, ev_trae=ev_trae,
                   priv=priv, cons=cons)


# ── 1. 读隔离 list_for ──────────────────────────────────────────────
def test_list_empty_agent_sees_nothing(ws):
    s, _ = ws
    out = s.list_for("")
    assert out == [], "agent_id 为空时不得泄漏任何证据/结晶"


def test_list_vera_sees_own_and_consensus(ws):
    s, d = ws
    ids = {x["memory_id"] for x in s.list_for("vera")}
    assert d["ev_vera"].id in ids          # 自己的证据可见
    assert d["cons"].id in ids             # 作为贡献者，共识可见
    assert d["priv"].id in ids             # 自己的私有可见
    assert d["ev_trae"].id not in ids     # 他人证据隔离


def test_list_trae_sees_own_and_consensus_not_veras_private(ws):
    s, d = ws
    ids = {x["memory_id"] for x in s.list_for("trae")}
    assert d["ev_trae"].id in ids
    assert d["cons"].id in ids
    assert d["ev_vera"].id not in ids
    assert d["priv"].id not in ids         # vera 私有对 trae 隔离


def test_list_consensus_hidden_from_non_contributor(ws):
    s, d = ws
    # 伪造一个「非贡献者」agent
    ids = {x["memory_id"] for x in s.list_for("mallory")}
    assert d["cons"].id not in ids, "共识仅对贡献者可见"
    assert d["priv"].id not in ids


# ── 2. 删除授权 delete_as ──────────────────────────────────────────
def test_delete_requires_agent_id(ws):
    s, d = ws
    r = s.delete_as("", d["ev_vera"].id)
    assert r["ok"] is False and "agent_id required" in r["error"]


def test_delete_evidence_only_by_owner(ws):
    s, d = ws
    bad = s.delete_as("trae", d["ev_vera"].id)
    assert bad["ok"] is False and "not owner" in bad["error"]
    good = s.delete_as("vera", d["ev_vera"].id)
    assert good["ok"] is True and good["deleted"] == "evidence"


def test_delete_consensus_forbidden(ws):
    s, d = ws
    r = s.delete_as("vera", d["cons"].id)
    assert r["ok"] is False and "consensus" in r["error"]


def test_delete_private_only_by_contributor(ws):
    s, d = ws
    bad = s.delete_as("trae", d["priv"].id)
    assert bad["ok"] is False and "not a contributor" in bad["error"]
    good = s.delete_as("vera", d["priv"].id)
    assert good["ok"] is True and good["deleted"] == "memory"


def test_delete_by_content_owned_private(ws):
    s, d = ws
    # vera 的私有结晶按 content 兜底删除（仅删本人相关）
    r = s.delete_as("vera", "vera 的私有结论")
    assert r["ok"] is True and r["memories"] >= 1


def test_delete_by_content_hits_consensus_forbidden(ws):
    s, d = ws
    r = s.delete_as("vera", "vera 与 trae 共同结论")
    assert r["ok"] is False and "consensus" in r["error"]


def test_delete_not_found(ws):
    s, d = ws
    r = s.delete_as("vera", "nonexistent-id")
    assert r["ok"] is False and r["error"] == "not_found"


# ── 3. 一致解散 dissolve_as ────────────────────────────────────────
def test_dissolve_requires_agent_id(ws):
    s, d = ws
    root = Path(tempfile.mkdtemp())
    r = s.dissolve_as("", d["cons"].id, root)
    assert r["ok"] is False and "agent_id required" in r["error"]


def test_dissolve_rejects_private(ws):
    s, d = ws
    root = Path(tempfile.mkdtemp())
    r = s.dissolve_as("vera", d["priv"].id, root)
    assert r["ok"] is False and "not_consensus" in r["error"]


def test_dissolve_rejects_non_contributor(ws):
    s, d = ws
    root = Path(tempfile.mkdtemp())
    r = s.dissolve_as("mallory", d["cons"].id, root)
    assert r["ok"] is False and "not a contributor" in r["error"]


def test_dissolve_partial_then_unanimous(ws):
    s, d = ws
    root = Path(tempfile.mkdtemp())
    # 第一票：vera，未集齐
    r1 = s.dissolve_as("vera", d["cons"].id, root)
    assert r1["dissolved"] is False
    assert set(r1["remaining"]) == {"trae"}
    assert d["cons"].id in {m.id for m in s.memories}, "未集齐不应真删"
    # 第二票：trae，集齐 → 真删
    r2 = s.dissolve_as("trae", d["cons"].id, root)
    assert r2["dissolved"] is True
    assert d["cons"].id not in {m.id for m in s.memories}
    # 票已清
    from liquid_loop.workspace import _load_dissolve_votes
    assert d["cons"].id not in _load_dissolve_votes(root)


def test_dissolve_vote_persists_across_instances(ws):
    """投票状态独立持久化，不依赖 WorkspaceState 内存（重启不丢票）"""
    from liquid_loop.workspace import _load_dissolve_votes
    s, d = ws
    root = Path(tempfile.mkdtemp())
    s.dissolve_as("vera", d["cons"].id, root)
    # 重新从磁盘读票（模拟重启）
    votes = _load_dissolve_votes(root)
    assert d["cons"].id in votes and "vera" in votes[d["cons"].id]
