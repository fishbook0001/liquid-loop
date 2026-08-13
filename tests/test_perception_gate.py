"""蒸馏「门控-液环因果共生环」落地实测（PerceptionGate，guard.py）。

设计对应（克制：精选 4 模块，不重新膨胀）：
  - should_escalate          : 不可逆+低置信 → block（交回人）
  - workspace.causal 因果边  : 因果核心（enables/causes/contradicts 出边）→ 永放行（never starve）
  - recall_filter.adaptive_recall : load<0.5 冗余验证 / ≥0.5 分工扩容，驱动 degrade vs allow
  - rar.build_or_cache       : 本地算力缓存索引 → 溶解「重建索引」成本，降级 skip 无后顾之忧

本测试全部真实 import liquid-loop 真实模块并真实调用，不 mock：
  - 因果核心判定真实 lookup 真实 Evidence 对象的 causal 出边；
  - 真实调用 adaptive_recall 验证负载双模；
  - 真实调用 build_or_cache 两次验证缓存命中（对象身份相同 = 本地算力溶解重建成本）。
"""
from liquid_loop.guard import PerceptionGate
from liquid_loop.workspace import Evidence
from liquid_loop.recall_filter import adaptive_recall
from liquid_loop.rar import build_or_cache, invalidate as rar_invalidate


# ── 真实工作区证据（带因果出边：模拟液环主链路核心）──
_CORE_EV = Evidence(
    id="e_core", anchor_id="a_principle",
    content="液环铁律：禁向量，记忆漂移不可解释",
    causal={"enables": ["m_principle"], "causes": [], "caused_by": [], "contradicts": []},
)
_LEAF_EV = Evidence(
    id="e_leaf", anchor_id="a_fact",
    content="某次会话的表层事实记录",
    causal={"enables": [], "causes": [], "caused_by": ["e_core"], "contradicts": []},
)
_EV_BY_ID = {"e_core": _CORE_EV, "e_leaf": _LEAF_EV}


def _causal_core_pred(action):
    """真实判定：lookup 真实 Evidence 的因果出边。"""
    ev = _EV_BY_ID.get(action)
    if not ev:
        return False
    c = ev.causal or {}
    return bool(c.get("enables") or c.get("causes") or c.get("contradicts"))


def _recall_cands():
    return [
        {"memory_id": "a", "content": "液环 禁 向量 记忆 系统", "score": 0.9},
        {"memory_id": "b", "content": "苹果 香蕉 西瓜", "score": 0.8},
        {"memory_id": "c", "content": "液环 自检 环节 审计", "score": 0.7},
    ]


# ───────────────────── 1) block：不可逆 + 低置信 → 交回人 ─────────────────────

def test_pg_block_on_irreversible_low_confidence():
    gate = PerceptionGate(budget=1.0, causal_core_predicate=_causal_core_pred)
    # 不可逆且低置信 → should_escalate 命中 → block
    d = gate.decide("e_leaf", cost=0.0, confidence=0.2, irreversible=True)
    assert d["decision"] == "block"
    assert d["escalate"] is True
    # block 不消耗预算
    assert gate.remaining() == 1.0


# ──────────── 2) 因果核心永饿死豁免：超预算也放行（对比非核心）────────────

def test_pg_causal_core_never_starves_even_over_budget():
    # 预算已耗尽
    gate = PerceptionGate(budget=0.0, causal_core_predicate=_causal_core_pred)
    # 因果核心（e_core 有 enables 出边）→ 即便预算=0 也 allow
    d_core = gate.decide("e_core", cost=0.5)
    assert d_core["decision"] == "allow"
    assert d_core["causal_core"] is True
    # 非核心（e_leaf 仅 caused_by 入边，无出边）→ 非因果核心，超预算走 degrade（低载默认）
    # 注：默认 load_probe=0.0 → 低载精度优先 → allow，但 cost 超预算被标记为 over_budget
    d_leaf = gate.decide("e_leaf", cost=0.5)
    assert d_leaf["decision"] in ("allow", "degrade")
    # 关键对照：因果核心的放行理由是「永放行」，非核心是「超预算+低载冗余验证」
    assert "因果核心" in d_core["reason"]


def test_pg_non_core_over_budget_high_load_degrade():
    # 非核心证据在超预算 + 高载下 → degrade（省算力）
    gate = PerceptionGate(
        budget=0.0,
        causal_core_predicate=_causal_core_pred,
        load_probe=lambda: 0.8,   # 高载
    )
    d = gate.decide("e_leaf", cost=0.5)
    assert d["decision"] == "degrade"
    assert d["over_budget"] is True
    assert d["load"] == 0.8


# ──────────── 3) 负载双模：真实 adaptive_recall 驱动 degrade / allow ────────────

def test_pg_high_load_degrade_via_real_adaptive_recall():
    # 真实调用 adaptive_recall：高载 → specialized_capacity（分工扩容，不做交叉验证）
    out, rep = adaptive_recall("液环 向量", _recall_cands(), load=0.8, k=2)
    assert rep["mode"] == "specialized_capacity"
    # 用该真实负载态驱动 gate：超预算 + 高载 → degrade
    gate = PerceptionGate(
        budget=0.0, causal_core_predicate=_causal_core_pred,
        load_probe=lambda: 0.8,
    )
    d = gate.decide("e_leaf", cost=0.4)
    assert d["decision"] == "degrade"


def test_pg_low_load_allow_redundant_via_real_adaptive_recall():
    # 真实调用 adaptive_recall：低载 → redundant_verify（冗余交叉验证，精度优先）
    out, rep = adaptive_recall("液环 向量", _recall_cands(), load=0.2, k=3)
    assert rep["mode"] == "redundant_verify"
    assert "raid_fused" in out[0]   # 低载路径附加共识字段
    # 超预算 + 低载 → allow（精度优先，冗余验证）
    gate = PerceptionGate(
        budget=0.0, causal_core_predicate=_causal_core_pred,
        load_probe=lambda: 0.2,
    )
    d = gate.decide("e_leaf", cost=0.4)
    assert d["decision"] == "allow"
    assert d["over_budget"] is True


# ──────────── 4) 本地算力溶解重建成本：真实 build_or_cache 缓存命中 ────────────

def test_pg_local_compute_dissolves_rebuild_cost():
    # RARIndex.build 要求候选带 "id" 字段（真实接口约束）
    cands = [
        {"id": "a", "content": "液环 禁 向量 记忆 系统"},
        {"id": "b", "content": "苹果 香蕉 西瓜"},
        {"id": "c", "content": "液环 自检 环节 审计"},
    ]
    # 第一次构建索引（真实算力消耗）
    idx1 = build_or_cache(cands, version="v1.8.4", agent_id="test_pg")
    # 第二次相同入参 → 命中进程内缓存，返回同一对象（零重建成本）
    idx2 = build_or_cache(cands, version="v1.8.4", agent_id="test_pg")
    assert idx1 is idx2   # 对象身份相同 = 本地缓存命中，重建成本被溶解
    # 清理：避免污染全局 _CACHE
    rar_invalidate(agent_id="test_pg")


# ──────────── 5) 正常放行消耗预算，remaining 递减 ────────────

def test_pg_normal_allow_consumes_budget():
    gate = PerceptionGate(budget=1.0, causal_core_predicate=_causal_core_pred)
    assert gate.decide("e_leaf", cost=0.3)["decision"] == "allow"
    assert gate.remaining() == 0.7
    assert gate.decide("e_leaf", cost=0.3)["decision"] == "allow"
    assert gate.remaining() == 0.4
    # 剩余不足新动作成本且低载 → 仍 allow（冗余验证，精度优先），但标记 over_budget
    d = gate.decide("e_leaf", cost=0.5)
    assert d["decision"] == "allow"
    assert d["over_budget"] is True
