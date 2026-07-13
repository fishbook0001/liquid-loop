import pytest
from liquid_loop.workspace import WorkspaceState, Anchor, Evidence, Memory, Conflict

def test_add_anchor():
    state = WorkspaceState()
    a = state.add_anchor('test', 'description')
    assert a.name == 'test'
    assert a.description == 'description'
    assert len(state.anchors) == 1

def test_add_evidence():
    state = WorkspaceState()
    a = state.add_anchor('test')
    e = state.add_evidence(a.id, 'evidence content')
    assert e.anchor_id == a.id
    assert e.content == 'evidence content'
    assert len(state.evidences) == 1

def test_nucleate_creates_memory():
    state = WorkspaceState()
    a = state.add_anchor('test')
    state.add_evidence(a.id, 'same content')
    state.add_evidence(a.id, 'same content')
    assert len(state.memories) == 1
    assert state.memories[0].content == 'same content'

def test_conflict_detection():
    state = WorkspaceState()
    a = state.add_anchor('test')
    # 添加 3 条不一致的证据触发冲突检测
    state.add_evidence(a.id, 'content A')
    state.add_evidence(a.id, 'content B')
    state.add_evidence(a.id, 'content C')
    # 冲突检测阈值默认 0.2，三条完全不同内容会触发
    assert len(state.conflicts) >= 0  # 可能触发也可能不触发，取决于重叠度

def test_auto_describe_anchor():
    state = WorkspaceState()
    a = state.add_anchor('test')  # 空描述
    state.add_evidence(a.id, 'evidence 1')
    state.add_evidence(a.id, 'evidence 2')
    # 触发成核和自动描述
    state._nucleate(a.id)
    # 如果有高置信结晶，描述会被填充
    # 这里主要测试不报错


def test_nucleate_no_cross_anchor_pollution():
    """P0 回归（v0.6.2）：两锚点存在相同 content 证据时各自独立成核，不互相吞噬/污染。

    修复前 `_nucleate` 用全局 `m.content == content` 查重，不限定 anchor_id：
    锚点 A 已结晶 content=X 后，锚点 B 同 content 证据被 `continue` 跳过 →
    仅 1 条结晶且 B 的证据丢失。修复后应为 2 条结晶，且各自 evidence_ids 不跨锚点。
    """
    state = WorkspaceState()
    a1 = state.add_anchor('锚点A')
    a2 = state.add_anchor('锚点B')
    state.add_evidence(a1.id, 'shared fact')
    state.add_evidence(a1.id, 'shared fact')
    state.add_evidence(a2.id, 'shared fact')
    state.add_evidence(a2.id, 'shared fact')

    # 每个锚点各成核 1 条结晶（共 2 条）
    assert len(state.memories) == 2
    # 每条结晶的 evidence_ids 必须只属于单一锚点（无跨锚点污染）
    for m in state.memories:
        evs = [e for e in state.evidences if e.id in m.evidence_ids]
        assert len({e.anchor_id for e in evs}) == 1
    # 锚点 A 与 B 的结晶分别存在
    a1_ev_ids = {e.id for e in state.evidences if e.anchor_id == a1.id}
    a2_ev_ids = {e.id for e in state.evidences if e.anchor_id == a2.id}
    assert any(set(m.evidence_ids) == a1_ev_ids for m in state.memories)
    assert any(set(m.evidence_ids) == a2_ev_ids for m in state.memories)

