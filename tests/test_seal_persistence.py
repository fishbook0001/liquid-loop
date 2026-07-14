"""E8/v0.6.4 — 验证 SEAL 双优化不再被 _recalc_anchor 覆盖（解 v0.6.3 假落地）。

修复核心：stability = base(证据权重均值) + seal_adjust(SEAL 自评层增量)；
apply_strategy 改 seal_adjust 而非直接动 stability，_recalc 合成时保留。

本测试直接构造诊断（绕过 SelfRefineEngine.verify 的脆弱归因），专注验证
apply_strategy → seal_adjust → _recalc 合成的持久性。
"""
from liquid_loop.workspace import WorkspaceState, SelfRefineEngine


def _boost(a, eng):
    diag = {"fail_type": "retrieval", "target_anchor": a.id,
            "root_cause": "召回失败", "tune": "boost_stability"}
    return eng.apply_strategy(diag)


def _down(a, eng):
    diag = {"fail_type": "reason", "target_anchor": a.id,
            "root_cause": "推理失败", "tune": "downweight_noise"}
    return eng.apply_strategy(diag)


def test_seal_boost_persists_through_recalc():
    """SEAL boost 后即便再 add_evidence 触发 _recalc，stability 仍含 seal_adjust。"""
    st = WorkspaceState()
    eng = SelfRefineEngine(st)
    a = st.add_anchor("probe_seal")
    st.add_evidence(a, "初始证据")
    _boost(a, eng)
    assert a.seal_adjust == 0.1
    st.add_evidence(a, "再来一条证据")  # 触发 _recalc_anchor
    assert a.seal_adjust == 0.1  # 增量持久
    expected = round(min(1.0, max(0.1, a.base_stability + a.seal_adjust)), 3)
    assert a.stability == expected
    assert a.stability >= a.base_stability  # 正增量应 >= base


def test_seal_downweight():
    st = WorkspaceState()
    eng = SelfRefineEngine(st)
    a = st.add_anchor("noise")
    st.add_evidence(a, "x")
    acts = _down(a, eng)
    assert a.seal_adjust == -0.1
    assert acts[0]["op"] == "downweight_noise"


def test_seal_adjust_applied_in_recalc():
    """开 SEAL 的锚点 stability = base + seal_adjust；对照无 SEAL 锚点 adjust=0。"""
    st = WorkspaceState()
    eng = SelfRefineEngine(st)
    a = st.add_anchor("x")
    st.add_evidence(a, "e1")
    _boost(a, eng)
    st.add_evidence(a, "e2")  # 触发 _recalc
    # 开 SEAL：stability 合成 seal_adjust（含 clamp，与期望值精确匹配）
    assert a.stability == round(min(1.0, max(0.1, a.base_stability + a.seal_adjust)), 3)
    # 对照：无 SEAL 的锚点 seal_adjust=0，stability=base
    b = st.add_anchor("y")
    st.add_evidence(b, "f1")
    assert b.seal_adjust == 0.0
    assert abs(b.stability - b.base_stability) < 1e-6
