"""PEEK 预算稳态 + SEAL 双优化 落地测试（液环仓库内）"""
import pytest
from liquid_loop.workspace import WorkspaceState, SelfRefineEngine
from liquid_loop.cognitive_budget import CognitiveBudgetStabilizer


# ───────────── PEEK 落地：认知预算稳态器 ─────────────

def test_budget_stabilizer_evicts_low_value(monkeypatch):
    monkeypatch.setenv("LIQUID_EVIDENCE_BUDGET", "3")
    st = WorkspaceState()
    a_hot = st.add_anchor("hot")
    a_hot.value_score = 0.9
    a_hot.access_count = 20
    a_hot.liquidity = "hot"
    a_cold = st.add_anchor("cold")
    a_cold.value_score = 0.2
    a_cold.access_count = 0
    a_cold.liquidity = "frozen"
    for i in range(3):
        st.add_evidence("hot", f"重要证据{i} 液环锚点")
    for i in range(3):
        st.add_evidence("cold", f"陈旧证据{i} 旧记忆")
    # 6 条证据，预算 3 → 应冷归档 3 条最低价值（cold 锚点）
    arch = [e for e in st.evidences if e.archived]
    assert len(arch) == 3
    assert all(e.anchor_id == a_cold.id for e in arch)


def test_budget_no_limit_when_unset(monkeypatch):
    monkeypatch.delenv("LIQUID_EVIDENCE_BUDGET", raising=False)
    st = WorkspaceState()
    a = st.add_anchor("x")
    for i in range(10):
        st.add_evidence("x", f"证据{i}")
    arch = [e for e in st.evidences if e.archived]
    assert arch == []  # 无预算不驱逐


def test_budget_stabilizer_direct_call(monkeypatch):
    monkeypatch.setenv("LIQUID_EVIDENCE_BUDGET", "2")
    st = WorkspaceState()
    a = st.add_anchor("x")
    a.value_score = 0.5
    for i in range(5):
        st.add_evidence("x", f"证据{i}")
    res = CognitiveBudgetStabilizer(st).stabilize()
    assert res["evicted"] == 3
    assert res["total"] == 5
    assert res["budget"] == 2


# ───────────── SEAL 落地：失败诊断双优化 ─────────────

def test_seal_diagnose_retrieval():
    st = WorkspaceState()
    a = st.add_anchor("液环", description="液环记忆理论")
    st.add_evidence("液环", "液环锚点结晶")
    eng = SelfRefineEngine(st)
    failure = {"source": a.id, "reason": "检索失败，无相关记忆", "question": "?", "gold": "?"}
    diag = eng.diagnose(failure)
    assert diag["fail_type"] == "retrieval"
    assert diag["target_anchor"] == a.id
    assert diag["tune"] == "boost_stability"


def test_seal_diagnose_reason_downweights():
    st = WorkspaceState()
    a = st.add_anchor("稳态")
    eng = SelfRefineEngine(st)
    failure = {"source": a.id, "reason": "关键词不匹配，gold=稳态"}
    diag = eng.diagnose(failure)
    assert diag["fail_type"] == "reason"
    before = a.stability
    eng.apply_strategy(diag)
    assert a.stability == max(0.1, round(before - 0.1, 3))


def test_seal_apply_strategy_boost():
    st = WorkspaceState()
    a = st.add_anchor("液环")
    eng = SelfRefineEngine(st)
    diag = {"fail_type": "retrieval", "target_anchor": a.id, "tune": "boost_stability"}
    before = a.stability
    acts = eng.apply_strategy(diag)
    assert acts[0]["op"] == "boost_stability"
    assert a.stability == min(1.0, round(before + 0.1, 3))


def test_seal_run_integration_dual_optimize(monkeypatch):
    st = WorkspaceState()
    a = st.add_anchor("液环", description="液环")
    st.add_evidence("液环", "液环锚点")
    eng = SelfRefineEngine(st)

    def fake_verify(p):
        if p.get("type") == "fact":
            # 模拟"检索失败但记忆应存在"：source 指向锚点，gold 为新内容（需补证据）
            return {"passed": False, "reason": "检索失败，无相关记忆",
                    "question": p.get("question", ""), "gold": "全新记忆内容需补回",
                    "source": a.id}
        return {"passed": True, "reason": "ok"}

    monkeypatch.setattr(eng, "verify", fake_verify)
    out = eng.run()
    assert out["failed"] >= 1
    assert len(out["strategy_actions"]) >= 1
    assert out["strategy_actions"][0]["op"] == "boost_stability"
    # 双优化：memory 侧（repairs）与 strategy 侧（strategy_actions）都应存在
    assert len(out["repairs"]) >= 1
