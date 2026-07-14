"""共识结晶 contributors 随新一致方动态扩展（三方+ CCI 计量）。"""
from liquid_loop import WorkspaceState
from liquid_loop.workspace import Anchor, Evidence, Memory


def _add(st, name, content, agent_id):
    a = next((x for x in st.anchors if x.name == name), None)
    if a is None:
        a = st.add_anchor(name)
    e = Evidence(content=content, anchor_id=a.id, agent_id=agent_id)
    st.add_evidence(a, content, agent_id=agent_id)
    return a


def test_consensus_expands_with_third_agent():
    st = WorkspaceState()
    # qwenpaw + vera 先达成共识
    _add(st, "液环", "液环是零向量记忆", "qwenpaw")
    _add(st, "液环", "液环是零向量记忆", "qwenpaw")  # qwenpaw >=2 -> private
    _add(st, "液环", "液环是零向量记忆", "vera")      # +vera -> consensus[qwenpaw,vera]
    cons = next(m for m in st.memories if m.scope == "consensus")
    assert cons.contributors == ["qwenpaw", "vera"], cons.contributors
    # marvis 也写同内容 -> 应并入三方共识
    _add(st, "液环", "液环是零向量记忆", "marvis")
    cons = next(m for m in st.memories if m.scope == "consensus")
    assert cons.contributors == ["marvis", "qwenpaw", "vera"], cons.contributors


def test_consensus_no_duplicate_on_repeat():
    st = WorkspaceState()
    _add(st, "fact", "X", "a")
    _add(st, "fact", "X", "a")          # private[a]
    _add(st, "fact", "X", "b")          # consensus[a,b]
    _add(st, "fact", "X", "b")          # b 重复，不应扩
    cons = next(m for m in st.memories if m.scope == "consensus")
    assert cons.contributors == ["a", "b"], cons.contributors


def test_private_untouched_by_consensus_expansion():
    st = WorkspaceState()
    _add(st, "fact", "Y", "qwenpaw")
    _add(st, "fact", "Y", "qwenpaw")    # private[qwenpaw]
    _add(st, "fact", "Y", "vera")       # consensus
    priv = [m for m in st.memories if m.scope == "private"]
    assert priv and priv[0].contributors == ["qwenpaw"], priv
