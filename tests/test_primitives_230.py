"""2.3.0 新原语专属单测：auction_route / bucket_brigade_credit / cost_meter
+ entropy.effective_level 结构降级。pytest 风格，零额外依赖。

覆盖关键路径（非仅调用模块自带 selftest）：
- 拍卖：最高价胜出 / 平局置信优先 / 低 trust 拦截让渡 / 空出价悬空 / 能力匹配优先
- 信用：activation 占比 / 正 outcome 放大 / 链式传导 / 空贡献 / 负激活截断
- 计量：per-agent / per-account 聚合 / 亲和钉住打破 / OTLP / fail-open
- 熵：单项越线强制降级，不被综合绿灯掩盖
"""
from liquid_loop.auction_route import route, Bid, RouteDecision
from liquid_loop.bucket_brigade_credit import assign_credit, Contribution
from liquid_loop.cost_meter import CostMeter, Usage
from liquid_loop.entropy import effective_level, structural_alerts


# ── auction_route ────────────────────────────────────────────────────────

def test_auction_highest_bid_wins():
    bids = [
        Bid("a", "summarize", 0.8, 3.0),
        Bid("b", "summarize", 0.6, 5.0),
        Bid("c", "summarize", 0.7, 2.0),
    ]
    d = route({"required_capability": "summarize", "difficulty": 0.5}, bids)
    assert isinstance(d, RouteDecision)
    assert d.winner == "b"
    assert d.runner_up == "a"


def test_auction_tie_breaks_by_confidence():
    bids = [Bid("a", "translate", 0.9, 4.0), Bid("b", "translate", 0.5, 4.0)]
    d = route({"required_capability": "translate"}, bids)
    assert d.winner == "a"


def test_auction_low_trust_suspicious_bid_handed_over():
    bids = [
        Bid("rogue", "code", 0.99, 30.0, trust_level=0),
        Bid("honest", "code", 0.8, 6.0, trust_level=2),
    ]
    d = route({"required_capability": "code"}, bids)
    assert d.winner == "honest"
    assert d.rejected and d.rejected[0][0] == "rogue"


def test_auction_empty_bids_dangling():
    d = route({"required_capability": "x"}, [])
    assert d.winner is None and d.runner_up is None


def test_auction_capability_match_pool_priority():
    # 匹配池存在时只在匹配池内选（即使非匹配者出价更高）
    bids = [
        Bid("matched", "code", 0.7, 4.0, trust_level=2),
        Bid("stranger", "design", 0.9, 9.0, trust_level=2),
    ]
    d = route({"required_capability": "code"}, bids)
    assert d.winner == "matched"


# ── bucket_brigade_credit ────────────────────────────────────────────────

def test_credit_activation_proportion():
    contribs = [Contribution("a", 3.0), Contribution("b", 1.0), Contribution("c", 1.0)]
    w = assign_credit(0.8, contribs)
    assert abs(w["a"] - 3.0 * w["b"]) < 1e-6
    assert abs(w["b"] - w["c"]) < 1e-6


def test_credit_positive_outcome_scales_absolute():
    contribs = [Contribution("a", 3.0), Contribution("b", 1.0)]
    low = assign_credit(0.0, contribs)
    high = assign_credit(1.0, contribs)
    assert high["a"] > low["a"] and high["b"] > low["b"]


def test_credit_chain_propagation_downstream():
    contribs = [
        Contribution("up", 2.0, upstream_of="mid"),
        Contribution("mid", 2.0, upstream_of="down"),
        Contribution("down", 2.0),
    ]
    w = assign_credit(0.5, contribs, links=[("up", "mid"), ("mid", "down")])
    assert w["down"] >= w["up"]
    assert w["down"] >= w["mid"]


def test_credit_empty_fail_open():
    assert assign_credit(0.5, []) == {}


def test_credit_negative_activation_clipped():
    w = assign_credit(0.5, [Contribution("a", -1.0), Contribution("b", 4.0)])
    assert w["a"] < w["b"]


# ── cost_meter ───────────────────────────────────────────────────────────

def test_costmeter_per_agent_aggregation():
    m = CostMeter()
    m.record(Usage("vera", "acct-a", "s1", prompt_tokens=100, completion_tokens=50, cost=0.001))
    m.record(Usage("vera", "acct-a", "s1", prompt_tokens=200, completion_tokens=80, cost=0.002))
    agg = m.agent_cost("vera")
    assert agg["calls"] == 2
    assert agg["total_tokens"] == 430
    assert abs(agg["cost"] - 0.003) < 1e-9


def test_costmeter_per_account_aggregation():
    m = CostMeter()
    m.record(Usage("vera", "acct-a", "s1", prompt_tokens=100, completion_tokens=50, cost=0.001))
    m.record(Usage("marvis", "acct-a", "s2", prompt_tokens=300, completion_tokens=0, cost=0.003))
    acc = m.account_cost("acct-a")
    assert acc["calls"] == 2 and acc["total_tokens"] == 450


def test_costmeter_affinity_bind_break():
    m = CostMeter()
    m.bind_affinity("s1", "acct-a")
    assert m.affinity_for("s1") == "acct-a"
    m.break_affinity("s1")
    assert m.affinity_for("s1") is None
    assert m.affinity_for("s2") is None


def test_costmeter_otlp_exports_agent_and_affinity():
    m = CostMeter()
    m.record(Usage("doubao", "acct", "s1", prompt_tokens=10, completion_tokens=2, cost=0.001))
    m.bind_affinity("s1", "acct")
    names = {x["name"] for x in m.export_otlp()}
    assert "ll.agent.cost" in names
    assert "ll.session.affinity_bound" in names


def test_costmeter_fail_open_bad_record():
    m = CostMeter()
    m.record(None)  # 不抛即通过
    assert m.agent_cost("x")["calls"] == 0


# ── entropy.effective_level 结构降级 ─────────────────────────────────────

def test_effective_level_structural_override_green_to_red():
    # 综合 0.2 本应 GREEN，但单项 anchor_drift=0.85 ≥0.8 强制 RED
    level, note = effective_level(0.2, {"anchor_drift": 0.85})
    assert level == "RED" and note


def test_effective_level_mild_alert_drops_green_to_yellow():
    level, note = effective_level(0.2, {"anchor_drift": 0.55})
    assert level == "YELLOW" and note


def test_effective_level_no_alert_keeps_combined():
    level, note = effective_level(0.2, {"anchor_drift": 0.1})
    assert level == "GREEN" and note == ""


def test_structural_alerts_lists_only_over_threshold():
    alerts = structural_alerts({"anchor_drift": 0.9, "conflict_density": 0.2})
    assert [a["dimension"] for a in alerts] == ["anchor_drift"]
