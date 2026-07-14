"""双轨成核 + 防污染回归测试（多智能体共用机制）。

锁住行为：
  - private 轨：同一 agent_id 内 >= 2 一致证据 -> scope=private
  - consensus 轨：同一 content 跨 >= 2 distinct agent_id -> scope=consensus
  - 防污染：不同 agent 不互相成私有晶；跨锚点同 content 不合并；复合键 (anchor,content,scope)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from liquid_loop.workspace import WorkspaceState


def _add(st, name, content, agent_id=""):
    a = next((x for x in st.anchors if x.name == name), None)
    if a is None:
        a = st.add_anchor(name)
    return st.add_evidence(a, content, agent_id=agent_id)


def test_private_nucleation_same_agent():
    st = WorkspaceState()
    _add(st, "topic", "液环是零向量记忆", agent_id="qwenpaw")
    _add(st, "topic", "液环是零向量记忆", agent_id="qwenpaw")
    priv = [m for m in st.memories if m.scope == "private"]
    cons = [m for m in st.memories if m.scope == "consensus"]
    assert len(priv) == 1
    assert priv[0].contributors == ["qwenpaw"]
    assert len(cons) == 0


def test_private_isolation_diff_agents():
    st = WorkspaceState()
    _add(st, "topic", "同一结论", agent_id="qwenpaw")
    _add(st, "topic", "同一结论", agent_id="vera")
    # 各 agent 内仅 1 条 -> 不 privately 成核
    assert all(m.scope != "private" for m in st.memories)
    # 但跨 2 distinct owner -> consensus 成核
    cons = [m for m in st.memories if m.scope == "consensus"]
    assert len(cons) == 1
    assert set(cons[0].contributors) == {"qwenpaw", "vera"}


def test_consensus_requires_distinct_owner():
    st = WorkspaceState()
    _add(st, "topic", "共识结论", agent_id="qwenpaw")
    _add(st, "topic", "共识结论", agent_id="qwenpaw")  # 同 owner 重复
    # 同 owner 2 条 -> private，但不该有 consensus（只有 1 个 owner）
    cons = [m for m in st.memories if m.scope == "consensus"]
    assert len(cons) == 0
    priv = [m for m in st.memories if m.scope == "private"]
    assert len(priv) == 1


def test_cross_anchor_no_pollution():
    st = WorkspaceState()
    _add(st, "A", "X", agent_id="qwenpaw")
    _add(st, "A", "X", agent_id="qwenpaw")
    _add(st, "B", "X", agent_id="qwenpaw")
    _add(st, "B", "X", agent_id="qwenpaw")
    # 两个锚点各成 1 个 private 晶，不合并
    priv = [m for m in st.memories if m.scope == "private"]
    assert len(priv) == 2


def test_legacy_compat_no_agent_id():
    st = WorkspaceState()
    _add(st, "topic", "legacy 内容", agent_id="")
    _add(st, "topic", "legacy 内容", agent_id="")
    priv = [m for m in st.memories if m.scope == "private"]
    assert len(priv) == 1
    assert priv[0].contributors == ["legacy"]


def test_dual_track_coexist():
    st = WorkspaceState()
    # 私有：qwenpaw 内 2 条独有结论
    _add(st, "T", "qwenpaw 私有结论", agent_id="qwenpaw")
    _add(st, "T", "qwenpaw 私有结论", agent_id="qwenpaw")
    # 共识：qwenpaw + vera 各 1 条共享结论
    _add(st, "T", "共享结论", agent_id="qwenpaw")
    _add(st, "T", "共享结论", agent_id="vera")
    priv = [m for m in st.memories if m.scope == "private"]
    cons = [m for m in st.memories if m.scope == "consensus"]
    assert len(priv) == 1 and priv[0].contributors == ["qwenpaw"]
    assert len(cons) == 1 and set(cons[0].contributors) == {"qwenpaw", "vera"}


def test_no_pollution_between_agents_private():
    st = WorkspaceState()
    _add(st, "T", "结论A", agent_id="qwenpaw")
    _add(st, "T", "结论A", agent_id="qwenpaw")
    _add(st, "T", "结论A", agent_id="vera")  # vera 只有 1 条 -> 不成私有
    priv = [m for m in st.memories if m.scope == "private"]
    cons = [m for m in st.memories if m.scope == "consensus"]
    # qwenpaw 私有 1 晶；vera 仅 1 条不足私有；二者合计 2 distinct owner -> consensus 1 晶
    assert len(priv) == 1 and priv[0].contributors == ["qwenpaw"]
    assert len(cons) == 1 and set(cons[0].contributors) == {"qwenpaw", "vera"}


def test_storage_roundtrip_agent_id_scope(tmp_path):
    """持久化往返：agent_id / scope / contributors 必须无损保存加载。"""
    from liquid_loop import storage
    st = WorkspaceState()
    _add(st, "T", "X", agent_id="qwenpaw")
    _add(st, "T", "X", agent_id="qwenpaw")
    storage.save(st, tmp_path)
    st2 = storage.load(tmp_path)
    assert len(st2.memories) == 1
    m = st2.memories[0]
    assert m.scope == "private"
    assert m.contributors == ["qwenpaw"]
    assert st2.evidences[0].agent_id == "qwenpaw"
