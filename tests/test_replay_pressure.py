"""#116 重放压力感知 + 反证轨局部阻尼测试 v1.6

对齐 Science 2026-06-04（雷博/钟毅，DOI:10.1126/science.aed8630）：记忆印迹细胞在 NREM 期
依据情绪极性双向调节睡眠——负面记忆(contradiction)病理性频繁重放→睡眠碎片化；
靶向单一印痕抑制即逆转病理睡眠。

映射到液环 v1.6：contradiction 证据被反复召回(recall_hits)超频→局部降温其降稳贡献
（防单一印痕超频重放碎片化整体稳定性），守零丢失(阻尼地板 0.3)、不破坏反证轨常态 s/(s+2c+1)。
"""
from liquid_loop.workspace import WorkspaceState, Anchor
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


def _add_contradiction(s, a, m, content="液环一致性走向量余弦",
                       agent_id="C", recall_hits=0):
    e = s.add_evidence(a.id, content, agent_id=agent_id,
                       relation="contradiction", target_memory_id=m.id)
    e.recall_hits = recall_hits
    return e


def test_normal_contradiction_no_damping():
    """常态 contradiction(recall_hits=0)→无阻尼、replay_pressure=0，等价原公式 s/(s+2c+1)。"""
    s, a, m = _state_with_consensus()
    _add_contradiction(s, a, m, recall_hits=0)
    s._update_memory_stability(a.id)
    # s=2,c=1 → 2/(2+2+1)=0.333
    assert m.replay_pressure == 0.0
    assert m.contradiction_count == 1
    assert m.stability == round(2 / (2 + 2 * 1 + 1), 3)  # 0.333


def test_overreplay_local_damping():
    """超频 contradiction(recall_hits=12)→局部降温(damp=0.9)，stability 高于未阻尼基线。"""
    s, a, m = _state_with_consensus()
    _add_contradiction(s, a, m, recall_hits=12)  # 超阈(10)→damp=1-0.05*2=0.9
    s._update_memory_stability(a.id)
    # 未阻尼基线: 2/(2+2+1)=0.333 ; 阻尼: c_eff=2*0.9=1.8 → 2/(2+1.8+1)=0.417
    assert m.replay_pressure == 2.0  # (12-10)
    assert m.stability > round(2 / (2 + 2 * 1 + 1), 3)
    assert m.stability == round(2 / (2 + 2 * 0.9 + 1), 3)  # 0.417


def test_perceive_replay_pressure():
    """perceive_replay_pressure 暴露 over-replay 的 contradiction memory。"""
    s, a, m = _state_with_consensus()
    _add_contradiction(s, a, m, recall_hits=15)
    s._update_memory_stability(a.id)
    perc = s.perceive_replay_pressure()
    assert m.id in perc
    assert perc[m.id]["replay_pressure"] == 5.0  # (15-10)
    assert perc[m.id]["stability"] == m.stability


def test_damping_floor_preserves_contradiction():
    """极端超频(recall_hits=100)→阻尼地板 0.3，contradiction 仍计数(不归零，守零丢失)。"""
    s, a, m = _state_with_consensus()
    _add_contradiction(s, a, m, recall_hits=100)
    s._update_memory_stability(a.id)
    # damp=floor 0.3 → c_eff=0.6 → 2/(2+0.6+1)=0.556
    assert m.replay_pressure == 90.0
    assert m.stability == round(2 / (2 + 2 * 0.3 + 1), 3)  # 0.556
    # 仍低于无矛盾基线(2/3=0.667)，证明矛盾印痕仍局部计数(未删)
    assert m.stability < round(2 / 3, 3)


def test_multiple_overreplay_accumulate_locally():
    """多条 over-replay contradiction 各自局部降温、压力累加，互不影响(局部非全局抑制)。"""
    s, a, m = _state_with_consensus()
    _add_contradiction(s, a, m, content="矛盾甲", recall_hits=12)  # pressure 2, damp 0.9
    _add_contradiction(s, a, m, content="矛盾乙", recall_hits=14)  # pressure 4, damp 0.8
    s._update_memory_stability(a.id)
    # c_eff = 2*0.9 + 2*0.8 = 3.4 → 2/(2+3.4+1)=0.313
    assert m.contradiction_count == 2
    assert m.replay_pressure == 6.0  # 2+4
    assert m.stability == round(2 / (2 + 2 * 0.9 + 2 * 0.8 + 1), 3)  # 0.313


def test_recall_path_bumps_contradiction_then_damps():
    """真实召回路径：recall 命中 contradiction 内容→bump recall_hits→触发局部阻尼。"""
    s, a, m = _state_with_consensus()
    # contradiction 内容与 support 零 token 重叠，确保 recall 仅 bump 该 contradiction
    e = _add_contradiction(s, a, m, content="反证轨采用嵌入向量")
    for _ in range(12):
        s.recall("反证轨 嵌入 向量")
    assert e.recall_hits == 12, "recall 应 bump contradiction 证据"
    s._update_memory_stability(a.id)
    # support 未召回→attn_bonus=0；c_eff=2*0.9=1.8 → 2/(2+1.8+1)=0.417
    assert m.attn_bonus == 0.0
    assert m.replay_pressure == 2.0
    assert m.stability == round(2 / (2 + 2 * 0.9 + 1), 3)  # 0.417


def test_persistence_damping(tmp_path):
    """recall_hits 持久化：save→load 后重算仍触发局部阻尼。"""
    s, a, m = _state_with_consensus()
    _add_contradiction(s, a, m, recall_hits=20)
    storage.save(s, tmp_path)
    s2 = storage.load(tmp_path)
    mem2 = [mm for mm in s2.memories if mm.content == "液环一致性走精确字符串匹配"][0]
    s2._update_memory_stability(a.id)
    # hits=20 → damp=max(0.3, 1-0.05*10)=0.5 → c_eff=2*0.5=1.0 → 2/(2+1+1)=0.5
    assert mem2.replay_pressure == 10.0
    assert mem2.stability == round(2 / (2 + 2 * 0.5 + 1), 3)  # 0.5
