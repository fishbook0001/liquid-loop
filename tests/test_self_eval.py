"""液环自检（蒸馏自 U-OPSD）落地测试"""
from liquid_loop.self_eval import (
    majority_vote,
    diverg_beta,
    consensus_self_check,
    string_divergence,
)


def test_majority_vote():
    ans = ["A", "A", "B", "A", "C"]
    top, frac = majority_vote(ans)
    assert top == "A"
    assert abs(frac - 0.6) < 1e-9


def test_diverg_beta_jsd_symmetric():
    pa = {1: 0.9, 2: 0.1}
    pb = {1: 0.5, 2: 0.5}
    js_ab = diverg_beta(pa, pb, beta=0.5)
    js_ba = diverg_beta(pb, pa, beta=0.5)
    assert abs(js_ab - js_ba) < 1e-9  # JSD 对称


def test_diverg_beta_reverse_kl_asymmetric():
    pa = {1: 0.9, 2: 0.1}
    pb = {1: 0.5, 2: 0.5}
    rk_ab = diverg_beta(pa, pb, beta=1.0)
    rk_ba = diverg_beta(pb, pa, beta=1.0)
    assert abs(rk_ab - rk_ba) > 1e-6  # reverse KL 非对称


def test_consensus_triggered_with_disagreement():
    # 3/5 同意 → frac=0.6≥0.5 且存在分歧 → 触发
    ans = ["液环禁向量", "液环禁向量", "液环禁向量", "用embedding", "别禁向量"]
    rep = consensus_self_check(ans, tau=0.5)
    assert rep["triggered"] is True
    assert abs(rep["frac"] - 0.6) < 1e-9
    assert rep["pseudo_answer"] == "液环禁向量"
    assert rep["n"] == 5
    assert rep["mean_divergence"] >= 0.0


def test_consensus_not_triggered_all_same():
    # 全同意 → 𝒴⁻=∅ → 不触发
    ans = ["same"] * 6
    rep = consensus_self_check(ans, tau=0.5)
    assert rep["triggered"] is False
    assert rep["frac"] == 1.0
    assert rep["mean_divergence"] == 0.0


def test_consensus_not_triggered_low_frac():
    # 票比 < τ → 不触发
    ans = ["x", "y", "z", "w"]
    rep = consensus_self_check(ans, tau=0.5)
    assert rep["triggered"] is False


def test_consensus_empty():
    rep = consensus_self_check([], tau=0.5)
    assert rep["triggered"] is False
    assert rep["n"] == 0
    assert rep["pseudo_answer"] is None


def test_string_divergence_identical_zero():
    assert string_divergence("液环 禁 向量", "液环 禁 向量") == 0.0


def test_consensus_tight_pseudo_low_divergence():
    # 同意集高度一致 → mean_divergence 应远小于分歧大的情况
    ans_tight = ["液环禁向量", "液环禁向量", "液环禁向量", "不同答案"]
    rep_tight = consensus_self_check(ans_tight, tau=0.5)
    assert rep_tight["triggered"] is True
    assert rep_tight["mean_divergence"] < 1e-6  # 同意集完全相同 → 散度为0
