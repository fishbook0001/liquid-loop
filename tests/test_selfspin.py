"""SelfSpin 双层自转测试 —— 此前 0 测试覆盖（基建审计 2026-08-08）。

覆盖：local_rotate 同义改写合并成核、词汇不重叠同义盲区（宁漏不污染）、
recall_local 命中、deposit dry_run 统计、去吞错后可观测持久化失败计数。
"""
import pytest

from liquid_loop.selfspin import LiquidSelfSpin

FACTS = {
    "report_1": ["液环禁止用向量或embedding做一致性判定与成核", "本地多agent协作架构已拆除"],
    "report_2": ["液环硬约束：禁用向量embedding做一致性判定", "记忆状态是演化对象而非被管理数据"],
    "report_3": ["液环North-Star公理：自我调节的记忆状态演化机制", "液环禁止向量做一致性判定"],
    "report_4": ["本地多agent协作架构已拆除", "今晚月色很好与液环无关"],
}


def _ss(liquid_persist=False):
    ss = LiquidSelfSpin("t", fast_jaccard=0.60)
    ss.liquid_persist = liquid_persist   # 隔离纯聚类逻辑，避免 ~/.liquidloop 副作用
    for rid, fs in FACTS.items():
        ss.ingest(rid, "", facts=fs)
    return ss


def test_local_rotate_finds_cross_report_nuclei():
    ss = _ss()
    nuclei = ss.local_rotate()
    # 禁向量(≥3篇) + 多agent拆除(2篇) → ≥2 个跨篇核
    assert len(nuclei) >= 2
    canonicals = [c["canonical"] for c in nuclei]
    assert any("向量" in c and "一致性" in c for c in canonicals)


def test_local_rotate_n_reports_counts_distinct_reports():
    ss = _ss()
    nuclei = ss.local_rotate()
    for c in nuclei:
        # n_reports = 支持该核的不同 report 数
        assert c["n_reports"] == len(c["reports"])
        assert c["n_reports"] >= 2   # 本地核门槛


def test_recall_local_hits():
    ss = _ss()
    ss.local_rotate()
    hit = ss.recall_local("液环 禁 向量 一致性")
    assert hit and hit[0]["score"] > 0


def test_blindspot_vocabulary_disjoint_not_merged():
    # 设计选择：语义同义但词汇几乎不重叠 → 不合并，各自孤立（宁漏不污染）
    ss = LiquidSelfSpin("blind")
    ss.liquid_persist = False
    ss.ingest("r1", "", facts=["液环禁用向量检索记忆"])
    ss.ingest("r2", "", facts=["液环排斥嵌入表示方式"])
    ss.local_rotate()
    # 两条语义同义但字面不重叠 → 不合并：2 个孤立簇，且都不是跨报告核
    assert len(ss._clusters) == 2
    assert ss._nuclei == []   # 各自仅 1 报告支持 → 无核，不误成核


def test_deposit_dry_run_counts():
    ss = _ss()
    ss.local_rotate()
    st = ss.deposit(dry_run=True)
    assert st["nuclei"] >= 2
    assert st["written"] == st["deposited"]
    assert st["nucleated"] == 0   # dry_run 不联网 → 无成核


def test_persist_failure_is_observable(monkeypatch):
    # 去吞错后：持久化失败应被记录且主流程不崩
    ss = LiquidSelfSpin("pf")
    ss.liquid_persist = True
    ss._persist_errors = 0

    def boom(*a, **k):
        raise RuntimeError("simulated persist failure")

    monkeypatch.setattr(ss, "_lr_instance", boom)
    ss.ingest("r", "", facts=["某事实"])   # 不应抛异常
    assert ss._persist_errors == 1
