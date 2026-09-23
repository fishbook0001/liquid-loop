"""RAR 关系增强检索测试 —— 此前 0 测试覆盖（基建审计 2026-08-08）。

中文支持：rar 的 token 正则原仅 `[a-z0-9_]+`（rar.py:23）导致中文 _tokens 恒
空、中文检索失效；2026-08-08 已对齐 selfspin 的字符级分词（中文单字 + 英数串 +
中文虚词停用过滤），中文召回现可生效（见 test_recall_chinese_content）。
"""

from liquid_loop.rar import RARIndex


def _cands():
    return [
        {"id": "m1", "type": "memory", "category": "C1",
         "content": "Liquid Loop forbids vectors for consistency checks",
         "timestamp": "2026-08-01", "owner": "vera", "scope": "consensus",
         "contributors": ["vera"]},
        {"id": "e1", "type": "evidence", "category": "C2",
         "content": "Quantum White Bone View model released",
         "timestamp": "2026-08-02", "owner": "vera", "scope": "", "contributors": []},
        {"id": "e2", "type": "evidence", "category": "C3",
         "content": "Memory state is an evolving object not managed data",
         "timestamp": "2026-08-03", "owner": "trae", "scope": "", "contributors": []},
    ]


def test_recall_literal_hit():
    idx = RARIndex.build(_cands(), version="v")
    hits = idx.recall("Liquid Loop vectors consistency", top_k=5)
    ids = [h[0] for h in hits]
    assert "m1" in ids


def test_recall_entity_bonus():
    idx = RARIndex.build(_cands(), version="v")
    # 查询命中专名实体 N:quantum / N:white → 实体加成召回 e1
    hits = idx.recall("Quantum White", top_k=5)
    ids = [h[0] for h in hits]
    assert "e1" in ids


def test_recall_1hop_propagation():
    cands = [
        {"id": "x", "type": "memory", "category": "C",
         "content": "Apple Neural Engine runs liquid neural networks",
         "timestamp": "2026-08-01", "owner": "vera", "scope": "consensus",
         "contributors": ["vera"]},
        {"id": "y", "type": "memory", "category": "C",
         "content": "Liquid AI LFM2 optimizes Neural Engine inference",
         "timestamp": "2026-08-02", "owner": "vera", "scope": "consensus",
         "contributors": ["vera"]},
    ]
    idx = RARIndex.build(cands, version="v")
    # 查询只命中 x 字面，y 应经共享实体 N:neural engine 的 1-hop 传播被召回
    hits = idx.recall("Apple Neural Engine runs", top_k=5)
    ids = [h[0] for h in hits]
    assert "x" in ids
    assert "y" in ids, "1-hop 传播应经共享实体召回 y"


def test_visible_candidates_isolation():
    # 轻量 fake state（duck-typed，仅需 evidences/memories/anchors 属性）
    class Ev:
        def __init__(self, id, agent_id, content, anchor_id=""):
            self.id = id
            self.agent_id = agent_id
            self.content = content
            self.anchor_id = anchor_id
            self.timestamp = ""

    class Mem:
        def __init__(self, id, content, scope, contributors):
            self.id = id
            self.content = content
            self.scope = scope
            self.contributors = contributors
            self.formed_at = ""

    class State:
        def __init__(self, evs, mems):
            self.evidences = evs
            self.memories = mems
            self.anchors = []

    st = State(
        evs=[Ev("e1", "vera", "liquid loop forbids vectors"),
             Ev("e2", "trae", "other content")],
        mems=[Mem("m1", "consensus memory", "consensus", ["vera"]),
              Mem("m2", "private memory", "private", ["trae"])],
    )
    cands = RARIndex.visible_candidates(st, "vera")
    cids = {c["id"] for c in cands}
    assert "e1" in cids            # 同 agent 的 evidence 可见
    assert "e2" not in cids        # 异 agent 的 evidence 隔离
    assert "m1" in cids            # consensus memory 对所有 agent 可见
    assert "m2" not in cids        # private memory 仅 contributor 可见


def test_recall_chinese_content():
    # 2026-08-08 修复后：中文按单字切分（与 selfspin 同源），中文检索现可生效
    cands = [{"id": "c1", "type": "memory", "category": "C",
              "content": "液环禁用向量做一致性判定", "timestamp": "2026-08-01",
              "owner": "vera", "scope": "consensus", "contributors": ["vera"]}]
    idx = RARIndex.build(cands, version="v")
    hits = idx.recall("液环 向量 一致性", top_k=5)
    assert "c1" in [h[0] for h in hits]


def test_recall_chinese_synonym_partial_overlap():
    # 同义不同表述：共享「液态神经网络」等字，部分重叠应仍能召回
    cands = [{"id": "d1", "type": "memory", "category": "C",
              "content": "液态神经网络有本地硬件基础", "timestamp": "2026-08-01",
              "owner": "vera", "scope": "consensus", "contributors": ["vera"]}]
    idx = RARIndex.build(cands, version="v")
    hits = idx.recall("液态神经网络 硬件", top_k=5)
    assert "d1" in [h[0] for h in hits]
