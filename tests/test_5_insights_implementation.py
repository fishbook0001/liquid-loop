"""
5大洞察工程落地模块测试用例
测试模块：generative_recall, hebbian_association, regeneration_metrics, dual_engine_monitor
"""
import sys
import os
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquid_loop.generative_recall import GenerativeRecall, generative_recall
from liquid_loop.hebbian_association import HebbianAssociation
from liquid_loop.regeneration_metrics import RegenerationMetrics
from liquid_loop.dual_engine_monitor import DualEngineMonitor


# ========== 测试数据 ==========

TEST_MEMORIES = [
    {"id": "D539", "title": "加州理工破解大脑想象力编码", "content": "想象时40%神经元重新激活，想象=感知的神经再次触发", "tags": "认知神经科学 想象力"},
    {"id": "D541", "title": "大脑计算神经回路激活次数", "content": "赫布法则：一起激活的神经元连在一起，反复激活强化连接", "tags": "认知神经科学 赫布法则"},
    {"id": "D543", "title": "大脑永不衰老", "content": "海马体神经元再生，激活再生记忆评分提升45%", "tags": "认知神经科学 抗衰"},
    {"id": "D527", "title": "大脑可塑性", "content": "大脑可塑性让你悄悄变聪明，化学性改变vs结构性改变", "tags": "认知神经科学 可塑性"},
    {"id": "D542", "title": "猕猴前瞻性元认知", "content": "双引擎45a+47/12o，主动选择正确率90% vs 被迫45%", "tags": "认知神经科学 元认知"},
]


# ========== 1. 生成式记忆检索测试 ==========

class TestGenerativeRecall:

    def test_encode_cue_basic(self):
        """测试线索编码基本功能"""
        engine = GenerativeRecall()
        weights = engine.encode_cue("记忆检索 赫布法则")
        assert isinstance(weights, dict)
        assert len(weights) > 0
        # 权重和应为1（归一化）
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_encode_cue_empty(self):
        """测试空线索编码"""
        engine = GenerativeRecall()
        weights = engine.encode_cue("")
        assert isinstance(weights, dict)

    def test_calculate_activation(self):
        """测试激活分数计算"""
        engine = GenerativeRecall()
        cue_weights = {"记忆": 0.5, "检索": 0.5}
        memory = {"id": "test", "title": "记忆检索测试", "content": "这是一个关于记忆检索的测试内容", "tags": "测试"}
        score = engine.calculate_activation(memory, cue_weights)
        assert score > 0  # 匹配的记忆应有正激活分数

    def test_sparse_activate_ratio(self):
        """测试稀疏激活比例"""
        engine = GenerativeRecall(activation_ratio=0.4)
        cue_weights = engine.encode_cue("认知神经科学")
        activated = engine.sparse_activate(TEST_MEMORIES, cue_weights)
        # 5条记忆 * 0.4 = 2条激活
        assert len(activated) == 2
        # 激活分数应降序排列
        scores = [s for _, s in activated]
        assert scores == sorted(scores, reverse=True)

    def test_sparse_activate_min_one(self):
        """测试稀疏激活至少激活1条"""
        engine = GenerativeRecall(activation_ratio=0.01)  # 极小比例
        cue_weights = engine.encode_cue("测试")
        activated = engine.sparse_activate(TEST_MEMORIES, cue_weights)
        assert len(activated) >= 1

    def test_generate_integration(self):
        """测试生成整合内容"""
        engine = GenerativeRecall()
        activated = [(TEST_MEMORIES[0], 0.8), (TEST_MEMORIES[1], 0.6)]
        result = engine.generate_integration(activated, "测试线索")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "测试线索" in result

    def test_recall_full_pipeline(self):
        """测试完整检索流程"""
        result = generative_recall("赫布法则 记忆", TEST_MEMORIES)
        assert "cue" in result
        assert "activated_memories" in result
        assert "generated_content" in result
        assert "activation_map" in result
        assert result["activation_ratio"] == 0.4
        assert len(result["activated_memories"]) > 0

    def test_recall_no_match(self):
        """测试无匹配记忆的检索"""
        result = generative_recall("完全不相关的关键词xyz123", TEST_MEMORIES)
        # 即使无匹配，也应至少激活1条（稀疏激活最小1条）
        assert len(result["activated_memories"]) >= 1


# ========== 2. 赫布关联引擎测试 ==========

class TestHebbianAssociation:

    def test_calculate_strength(self):
        """测试关联强度计算"""
        engine = HebbianAssociation()
        # 共激活0次→强度0
        assert engine.calculate_strength(0) == 0.0
        # 共激活10次→约0.63
        s10 = engine.calculate_strength(10)
        assert 0.6 < s10 < 0.7
        # 共激活20次→约0.86
        s20 = engine.calculate_strength(20)
        assert 0.8 < s20 < 0.9
        # 强度应随共激活次数单调递增
        assert s20 > s10

    def test_update_association(self):
        """测试关联更新"""
        engine = HebbianAssociation()
        assoc = engine.update_association("A", "B", "测试")
        assert assoc["co_activation_count"] == 1
        assert assoc["association_strength"] > 0
        assert assoc["memory_a"] == "A"
        assert assoc["memory_b"] == "B"

    def test_update_association_symmetry(self):
        """测试关联对称性（A-B和B-A应是同一个关联）"""
        engine = HebbianAssociation()
        engine.update_association("A", "B")
        engine.update_association("B", "A")  # 反向更新
        # 应只有1个关联（pair_id排序后相同）
        assert len(engine.associations) == 1
        # 共激活次数应为2
        assoc = list(engine.associations.values())[0]
        assert assoc["co_activation_count"] == 2

    def test_update_from_activation(self):
        """测试从激活事件批量更新关联"""
        engine = HebbianAssociation()
        count = engine.update_from_activation(["A", "B", "C"], "测试")
        # 3个元素→3对关联
        assert count == 3
        assert len(engine.associations) == 3

    def test_get_associations(self):
        """测试获取某个记忆的关联"""
        engine = HebbianAssociation()
        engine.update_from_activation(["A", "B", "C"])
        assocs = engine.get_associations("A")
        # A应与B、C都有关联
        assert len(assocs) == 2
        # 按强度降序
        strengths = [a["strength"] for a in assocs]
        assert strengths == sorted(strengths, reverse=True)

    def test_shortcut_curing(self):
        """测试快捷通路固化"""
        engine = HebbianAssociation(shortcut_threshold=0.5)
        # 共激活10次→强度约0.63>0.5，应固化为快捷通路
        for _ in range(10):
            engine.update_association("A", "B")
        shortcuts = engine.get_shortcuts()
        assert len(shortcuts) >= 1

    def test_decay_weak_associations(self):
        """测试衰减弱关联"""
        engine = HebbianAssociation()
        engine.update_association("A", "B")  # 弱关联（强度约0.095）
        # 手动修改last_activated为31天前
        for assoc in engine.associations.values():
            assoc["last_activated"] = "2026-08-01T00:00:00"
        removed = engine.decay_weak_associations(days_threshold=30, min_strength=0.1)
        assert removed >= 1

    def test_get_stats(self):
        """测试获取统计信息"""
        engine = HebbianAssociation()
        engine.update_from_activation(["A", "B", "C"])
        stats = engine.get_stats()
        assert stats["total_associations"] == 3
        assert "average_strength" in stats
        assert "shortcut_count" in stats

    def test_persistence(self):
        """测试关联数据持久化"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            tmp_path = f.name
        try:
            # 写入关联
            engine1 = HebbianAssociation(association_store_path=tmp_path)
            engine1.update_association("A", "B")
            # 重新加载
            engine2 = HebbianAssociation(association_store_path=tmp_path)
            assert len(engine2.associations) == 1
        finally:
            os.unlink(tmp_path)


# ========== 3. 记忆再生抗衰指标测试 ==========

class TestRegenerationMetrics:

    def test_calculate_diversity(self):
        """测试激活多样性计算"""
        engine = RegenerationMetrics()
        # 均匀分布→多样性高
        uniform = {"A": 10, "B": 10, "C": 10, "D": 10}
        d_uniform = engine.calculate_diversity(uniform)
        # 集中分布→多样性低
        concentrated = {"A": 40, "B": 0, "C": 0, "D": 0}
        d_concentrated = engine.calculate_diversity(concentrated)
        assert d_uniform > d_concentrated
        assert 0 <= d_uniform <= 1
        assert 0 <= d_concentrated <= 1

    def test_calculate_diversity_empty(self):
        """测试空激活计数的多样性"""
        engine = RegenerationMetrics()
        d = engine.calculate_diversity({})
        assert d == 0.0

    def test_collect_metrics_healthy(self):
        """测试healthy状态采集"""
        engine = RegenerationMetrics()
        metrics = engine.collect_metrics(
            new_distillations=15,
            new_associations=45,
            new_domains=2,
            activation_counts={"A": 10, "B": 8, "C": 5},
            total_memories=493
        )
        assert metrics["anti_aging_status"] == "healthy"
        assert metrics["regeneration_score"] >= 0.7

    def test_collect_metrics_critical(self):
        """测试critical状态采集"""
        engine = RegenerationMetrics()
        metrics = engine.collect_metrics(
            new_distillations=0,
            new_associations=0,
            new_domains=0,
            activation_counts={"A": 1},
            total_memories=10
        )
        assert metrics["anti_aging_status"] == "critical"
        assert metrics["regeneration_score"] < 0.4

    def test_should_trigger_regeneration(self):
        """测试再生触发判断"""
        engine = RegenerationMetrics()
        # critical状态应触发
        engine.collect_metrics(0, 0, 0, {"A": 1}, 10)
        assert engine.should_trigger_regeneration()

    def test_get_regeneration_recommendation(self):
        """测试再生建议"""
        engine = RegenerationMetrics()
        engine.collect_metrics(15, 45, 2, {"A": 10, "B": 8}, 493)
        rec = engine.get_regeneration_recommendation()
        assert rec["action"] == "maintain"
        assert "suggested_learning_rounds" in rec

    def test_get_trend(self):
        """测试趋势分析"""
        engine = RegenerationMetrics()
        # 数据不足时应返回insufficient_data
        trend = engine.get_trend()
        assert trend["trend"] == "insufficient_data"


# ========== 4. 双引擎监控架构测试 ==========

class TestDualEngineMonitor:

    def test_monitor_internal_health(self):
        """测试自我状态监控"""
        monitor = DualEngineMonitor()
        result = monitor.monitor_internal_health(
            memory_total=493, recall_success_rate=0.85,
            association_density=0.4, regeneration_score=0.82,
            task_completion_rate=0.9, error_rate=0.05,
            cpu_usage=0.3, memory_usage=0.4, disk_usage=0.5
        )
        assert "internal_health" in result
        assert "memory_health" in result
        assert "execution_quality" in result
        assert "resource_health" in result
        assert 0 <= result["internal_health"] <= 1

    def test_monitor_external_quality(self):
        """测试外部环境评估"""
        monitor = DualEngineMonitor()
        result = monitor.monitor_external_quality(
            input_relevance=0.8, information_density=0.7,
            noise_level=0.1, domain_match=0.9,
            distribution_shift=0.2, user_intent_clarity=0.85,
            urgency=0.3, risk_level=0.2
        )
        assert "external_quality" in result
        assert "input_quality" in result
        assert "adaptability" in result
        assert "intent_quality" in result
        assert 0 <= result["external_quality"] <= 1

    def test_fuse_decision_high_confidence(self):
        """测试高置信自主决策"""
        monitor = DualEngineMonitor()
        internal = {"internal_health": 0.85}
        external = {"external_quality": 0.85}
        decision = monitor.fuse_decision(internal, external)
        assert decision["mode"] == "autonomous_high_confidence"
        assert not decision["need_verification"]

    def test_fuse_decision_self_repair(self):
        """测试自我修复优先决策"""
        monitor = DualEngineMonitor()
        internal = {"internal_health": 0.3}  # 低于0.5
        external = {"external_quality": 0.8}
        decision = monitor.fuse_decision(internal, external)
        assert decision["mode"] == "self_repair_priority"
        assert decision["need_verification"]

    def test_fuse_decision_request_clarification(self):
        """测试请求用户澄清决策"""
        monitor = DualEngineMonitor()
        internal = {"internal_health": 0.8}
        external = {"external_quality": 0.3}  # 低于0.5
        decision = monitor.fuse_decision(internal, external)
        assert decision["mode"] == "request_clarification"

    def test_fuse_decision_conservative(self):
        """测试保守执行决策"""
        monitor = DualEngineMonitor()
        internal = {"internal_health": 0.6}
        external = {"external_quality": 0.6}
        decision = monitor.fuse_decision(internal, external)
        assert decision["mode"] == "conservative_with_verification"
        assert decision["need_verification"]

    def test_audit_log(self):
        """测试决策审计日志"""
        monitor = DualEngineMonitor()
        internal = {"internal_health": 0.85}
        external = {"external_quality": 0.85}
        monitor.fuse_decision(internal, external)
        log = monitor.get_audit_log()
        assert len(log) >= 1
        assert "decision" in log[0]

    def test_get_monitor_stats(self):
        """测试监控统计"""
        monitor = DualEngineMonitor()
        stats = monitor.get_monitor_stats()
        assert "total_decisions" in stats


# ========== 运行测试 ==========

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
