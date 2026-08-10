"""#115 注意力增益（多看几眼=价值升）机制测试 v1.5

对齐 Nature Communications 实证（Cecchi/Gluth/Palminteri 2026）：人类面对多选项系统性
低估中间项，但注意力（多看几眼）像增益旋钮因果重塑主观价值，关键窗口在奖励揭晓前。

映射到液环 v1.5：被反复召回的证据黏滞升稳（gaze 增益↔τ(x) 黏滞吸收），在
_update_memory_stability 中作为 support 加成（只增 s 不削 c → 守反证轨 stability 公式）。
"""
import pytest
from liquid_loop.workspace import WorkspaceState, Anchor, Evidence, Memory
from liquid_loop import storage


def _state_with_consensus():
    """两条同 content、不同 agent_id 的 evidence → consensus 轨成核。"""
    s = WorkspaceState()
    a = Anchor(name="液环一致性")
    s.anchors.append(a)
    s.add_evidence(a.id, "液环一致性走精确字符串匹配", agent_id="A")
    s.add_evidence(a.id, "液环一致性走精确字符串匹配", agent_id="B")
    s._nucleate(a.id)
    mem = [m for m in s.memories if m.content == "液环一致性走精确字符串匹配"]
    assert mem, "expect nucleated consensus memory"
    return s, a, mem[0]


def test_recall_triggers_attn_bonus():
    """recall 命中一次 → 两条 evidence 各 recall_hits=1 → attn_bonus=0.30，stability 升。"""
    s, a, m = _state_with_consensus()
    base = m.stability
    assert base == round(2 / 3, 3)  # s=2,c=0 → 0.667
    hits = s.recall("液环 一致性 字符串匹配")
    assert hits, "recall 应返回命中"
    s._update_memory_stability(a.id)
    # 两条同 content evidence 各 bump 一次 → 0.15*1 + 0.15*1 = 0.30
    assert m.attn_bonus == round(0.30, 3)
    assert m.stability > base
    assert m.stability == round((2 + 0.30) / (2 + 0.30 + 1), 3)  # 2.30/3.30=0.697


def test_multiple_recall_capped():
    """多次 recall → recall_hits 累积但封顶 10 → attn_bonus 封顶 3.0。"""
    s, a, m = _state_with_consensus()
    for _ in range(12):
        s.recall("液环 一致性 字符串匹配")
    s._update_memory_stability(a.id)
    # 2 evidence × min(12,10)=10 → 0.15*10*2 = 3.0
    assert m.attn_bonus == round(3.0, 3)
    assert m.stability == round((2 + 3.0) / (2 + 3.0 + 1), 3)  # 5/6=0.833


def test_contradiction_preserved():
    """注意力增益只增 s 不削 c → 反证轨仍显著降稳（不被注意力增益掩盖）。"""
    s, a, m = _state_with_consensus()
    s.recall("液环 一致性 字符串匹配")
    s._update_memory_stability(a.id)
    boosted = m.stability  # attn=0.30 → 2.30/3.30=0.697
    s.add_evidence(a.id, "液环一致性走向量余弦", agent_id="C",
                   relation="contradiction", target_memory_id=m.id)
    # s=2(+attn0.30), c=1 → 2.30/(2.30+2+1)=2.30/5.30=0.434
    assert m.contradiction_count == 1
    assert m.stability < boosted  # 反证降稳
    assert m.stability == round((2 + 0.30) / (2 + 0.30 + 2 * 1 + 1), 3)


def test_no_support_no_bonus():
    """无 support 证据的 memory（content 不匹配其 evidence）→ attn_bonus=0，不凭空升稳。"""
    s = WorkspaceState()
    a = Anchor(name="孤立锚点")
    s.anchors.append(a)
    # 加一条与本 memory content 无关的 evidence，使 memory 进入 anchor_memories 但 supports 为空
    e = s.add_evidence(a.id, "无关内容XYZ", agent_id="A")
    m = Memory(content="无支撑记忆", evidence_ids=[e.id])
    s.memories.append(m)
    s._update_memory_stability(a.id)
    assert m.attn_bonus == 0.0
    assert m.stability == 0.0  # s=0,c=0 → 0/(0+1)=0


def test_register_recall_archived_no_boost():
    """守关键窗口（证据吸收须在 crystallization 前）：已归档证据召回不升权。"""
    s, a, m = _state_with_consensus()
    for e in s.evidences:
        if e.anchor_id == a.id:
            e.archived = True
    s.recall("液环 一致性 字符串匹配")
    for e in s.evidences:
        if e.anchor_id == a.id:
            assert e.recall_hits == 0, "archived 证据不应被 bump"


def test_persistence_recall_hits(tmp_path):
    """recall_hits 持久化：save→load 后仍保留，重算后产生 attn_bonus。"""
    s, a, m = _state_with_consensus()
    s.recall("液环 一致性 字符串匹配")
    storage.save(s, tmp_path)
    s2 = storage.load(tmp_path)
    evs = [e for e in s2.evidences if e.anchor_id == a.id]
    assert all(e.recall_hits == 1 for e in evs), "recall_hits 应持久化"
    mem2 = [mm for mm in s2.memories if mm.content == "液环一致性走精确字符串匹配"][0]
    s2._update_memory_stability(a.id)
    assert mem2.attn_bonus > 0  # 重算后恢复注意力增益
