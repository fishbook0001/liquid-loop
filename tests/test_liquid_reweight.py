"""Liquid Reweight 液态核心层测试 —— 此前 0 测试覆盖（基建审计 2026-08-08）。

覆盖：τ(x) 黏滞窄带 + 方向性、拓扑连边、propagate 激活传播 + 黏滞封顶、
liquid_recall 唤醒字面不重叠但已激活的记忆、plain_recall 基线、因果边唤醒远端、
原理(why)通道召回、half_life TTL 黏滞冷却、save/load round-trip。
"""
import json
import os
import time
import tempfile

from liquid_loop.liquid_reweight import LiquidReweight, TAU_MIN, TAU_MAX

ANCHORS = [
    {"id": "A", "name": "液环禁向量一致性判定", "description": "液环禁用向量embedding做一致性判定与成核"},
    {"id": "B", "name": "液环稳态演化机制", "description": "液环记忆状态是演化对象而非被管理数据"},
    {"id": "C", "name": "液环双轨成核", "description": "液环private与consensus双轨成核机制"},
]


def _lr():
    lr = LiquidReweight(beta=0.6, topo_thresh=0.20)
    lr.load_anchors(ANCHORS)
    return lr


def test_tau_x_band_and_direction():
    lr = _lr()
    t_hi = lr.tau_x(0.9)   # 高重叠 → 小 τ（快吸收）
    t_lo = lr.tau_x(0.1)   # 低重叠 → 大 τ（慢渗透）
    assert t_hi < t_lo, "τ(x) 方向应：重叠高→τ小"
    assert TAU_MIN <= t_hi <= TAU_MAX, "τ 应被黏滞窄带封顶"
    assert TAU_MIN <= t_lo <= TAU_MAX


def test_tau_x_clamped_to_band():
    lr = _lr()
    assert lr.tau_x(1.0) == TAU_MIN   # 完全重叠 → 取窄带下限
    assert lr.tau_x(0.0) == TAU_MAX   # 零重叠 → 取窄带上限
    assert lr.tau_x(2.0) == TAU_MIN   # 越界输入被 clamp


def test_topology_connects_neighbors():
    lr = _lr()
    # A/B/C 共享「液环」+ 领域词，containment 应连边（≥ topo_thresh=0.20）
    assert lr.snapshot()["topo_edges"] >= 2


def test_propagate_activates_neighbors_and_caps():
    lr = _lr()
    deltas = lr.propagate("A", "液环禁止向量做一致性判定", steps=1)
    # 注入锚点自身拉满
    assert lr.activation["A"] == 1.0
    assert "A" in deltas
    # 邻居被唤醒
    assert lr.activation["B"] > 0
    assert lr.activation["C"] > 0
    # 黏滞封顶：单次 amp ≤ amp_cap，邻居不瞬间拉满
    assert lr.activation["B"] <= lr.amp_cap + 1e-9
    assert lr.activation["C"] <= lr.amp_cap + 1e-9


def test_propagate_unknown_anchor_returns_empty():
    lr = _lr()
    assert lr.propagate("ZZZ", "x") == {}


def test_liquid_recall_wakes_activated_without_literal_overlap():
    lr = _lr()
    lr.propagate("A", "液环禁止向量做一致性判定", steps=1)
    q = "演化机制的状态如何"   # 字面只重叠 B（稳态演化）
    plain = lr.plain_recall(q, top_k=3)
    liquid = lr.liquid_recall(q, top_k=3)
    plain_ids = [r["anchor_id"] for r in plain]
    liquid_ids = [r["anchor_id"] for r in liquid]
    # 纯字面基线不唤醒 A
    assert "A" not in plain_ids
    # 液态召回因 A 已激活把 A 拉入，且排首（记忆流动而非死存储）
    assert "A" in liquid_ids
    assert liquid_ids[0] == "A"


def test_causal_edge_wakes_remote_no_literal_overlap():
    lr = _lr()
    D = {"id": "D", "name": "量子白骨观", "description": "与液环无字面重叠的远端主题"}
    lr.load_anchors(ANCHORS + [D])
    lr.add_causal_edges([("A", "D", 0.6)])  # 绕过 keyword 噪声过滤
    lr.propagate("A", "液环禁止向量", steps=1)
    assert lr.activation["D"] > 0, "因果边应唤醒字面零重叠的远端 D"
    cr = lr.liquid_recall("量子白骨观是什么", top_k=5)
    assert "D" in [r["anchor_id"] for r in cr]


def test_principle_channel_recall():
    lr = _lr()
    E = {"id": "E", "name": "某远端无关主题", "description": "表面与液环无字面重叠",
         "principle": "一致性判定守禁向量，不依赖 embedding"}
    lr.load_anchors(ANCHORS + [E])
    pr = lr.liquid_recall("一致性判定守禁向量", top_k=5)
    e_row = next((r for r in pr if r["anchor_id"] == "E"), None)
    assert e_row is not None, "原理匹配应召回 E"
    assert e_row["literal"] == 0.0, "E 应纯靠 principle 通道（字面零重叠）"
    assert e_row["principle"] > 0


def test_half_life_cooling_to_half_after_one_period():
    lr = _lr()
    lr.activation["A"] = 1.0
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "act.json")
        lr.save(p)
        # 模拟经过一个 half_life 的时间
        data = json.load(open(p))
        data["ts"] = int(time.time()) - lr.half_life
        json.dump(data, open(p, "w"))
        lr2 = LiquidReweight(persist_path=p)
        lr2.load_anchors(ANCHORS)
        assert lr2.load(p) is True
        # cooled = raw * 0.5**(dt/half_life) = 1.0 * 0.5**1 = 0.5
        assert 0.49 <= lr2.activation["A"] <= 0.51


def test_save_load_roundtrip_no_decay_when_fresh():
    lr = _lr()
    lr.activation["A"] = 0.8
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "act.json")
        assert lr.save(p)
        lr2 = LiquidReweight(persist_path=p)
        lr2.load_anchors(ANCHORS)
        assert lr2.load(p) is True
        # dt≈0 → decay≈1 → 不衰减
        assert abs(lr2.activation["A"] - 0.8) < 1e-6


def test_load_missing_file_returns_false():
    lr = LiquidReweight()
    assert lr.load("/nonexistent/path/act.json") is False
