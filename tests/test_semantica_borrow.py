"""v1.8.2 治理/问责查询（Semantica 借鉴）：state_at / analyze_impact / find_duplicates"""
from liquid_loop.workspace import WorkspaceState, Evidence, Memory


def _base():
    st = WorkspaceState()
    st.evidences = [
        Evidence(id="e1", content="液环成核门槛需要两个一致证据",
                 timestamp="2026-08-01T00:00:00+00:00", agent_id="vera"),
        Evidence(id="e2", content="液环稳定性公式 s/(s+2c+1)",
                 timestamp="2026-08-10T00:00:00+00:00", agent_id="vera"),
        Evidence(id="e3", content="液环成核门槛需要两个一致证据",
                 timestamp="2026-08-05T00:00:00+00:00", agent_id="vera",
                 superseded_by="e1"),
    ]
    st.memories = [
        Memory(id="m1", content="液环成核", formed_at="2026-08-03T00:00:00+00:00",
               evidence_ids=["e1"]),
        Memory(id="m2", content="液环稳定性", formed_at="2026-08-11T00:00:00+00:00",
               evidence_ids=["e2"]),
    ]
    st.memories[0].causal = {"caused_by": ["e1"], "causes": ["m2"],
                             "enables": [], "contradicts": []}
    return st


def test_state_at_filters_by_timestamp():
    st = _base()
    r = st.state_at("2026-08-02T00:00:00+00:00")
    assert r["evidence_count"] == 1 and r["memory_count"] == 0
    assert r["evidences"][0]["id"] == "e1"


def test_state_at_excludes_superseded():
    st = _base()
    r = st.state_at("2026-08-11T00:00:00+00:00")
    ids = {e["id"] for e in r["evidences"]}
    assert ids == {"e1", "e2"}  # e3 被 superseded 排除
    assert r["memory_count"] == 2


def test_state_at_invalid_datetime():
    st = _base()
    assert st.state_at("bad-date")["ok"] is False


def test_analyze_impact_bfs():
    st = _base()
    r = st.analyze_impact("e1", depth=2)
    assert r["total_affected"] == 2
    rels = [(x["node_id"], x["relation"], x["level"])
            for lv in r["levels"] for x in lv]
    assert ("m1", "used_in", 1) in rels
    assert ("m2", "causes", 2) in rels


def test_analyze_impact_node_not_found():
    st = _base()
    assert st.analyze_impact("nope", 2)["ok"] is False


def test_find_duplicates_excludes_superseded():
    st = _base()
    r = st.find_duplicates(threshold=0.8)
    assert r["candidates"] == []  # e3 已 superseded，e1/e2 不同


def test_find_duplicates_detects_real_duplicate():
    st = _base()
    st.evidences.append(Evidence(id="e4", content="液环稳定性公式 s/(s+2c+1)",
                                 timestamp="2026-08-11T00:00:01+00:00",
                                 agent_id="marvis"))
    r = st.find_duplicates(threshold=0.8)
    assert any(c["similarity"] >= 1.0 for c in r["candidates"])


# ── v1.8.3 consensus 保护（Palantir owned 细化）：consensus 结晶证据禁止单边 supersede ──
def test_supersede_consensus_protected():
    st = WorkspaceState()
    st.evidences = [
        Evidence(id="c1", content="共识事实", agent_id="vera"),
        Evidence(id="c2", content="共识事实", agent_id="marvis"),
        Evidence(id="w1", content="取代者", agent_id="vera"),
    ]
    # 构造 consensus 结晶：c1+c2 跨 agent 一致
    st.memories = [
        Memory(id="m1", content="共识事实", scope="consensus",
               evidence_ids=["c1", "c2"], contributors=["vera", "marvis"]),
    ]
    # loser 是 consensus 证据 → 拒绝
    r = st.supersede_evidence("c1", "w1")
    assert not r.get("ok")
    assert "consensus evidence requires unanimous dissolution" in r.get("error", "")
    # 非 consensus 证据仍可 supersede
    st.evidences.append(Evidence(id="p1", content="普通证据", agent_id="vera"))
    r = st.supersede_evidence("p1", "w1")
    assert r.get("ok"), r
