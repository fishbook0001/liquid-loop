"""
记忆再生抗衰指标（Memory Regeneration Anti-Aging Metrics）
基于洞察三：液环的"抗衰"=持续生成新记忆单元，而非只清理旧记忆。
参考D543大脑永不衰老/海马体再生：只清理垃圾只能小幅延缓，激活再生记忆评分提升45%。
"""
import json
import math
from pathlib import Path
from datetime import datetime, timedelta


class RegenerationMetrics:
    """记忆再生抗衰指标引擎"""

    def __init__(self, metrics_store_path: str = None,
                 history_days: int = 7):
        """
        初始化再生指标引擎
        :param metrics_store_path: 指标存储路径
        :param history_days: 历史统计天数（默认7天）
        """
        self.metrics_store_path = Path(metrics_store_path) if metrics_store_path else None
        self.history_days = history_days
        self.history = []  # 历史指标记录
        self._load()

    def _load(self):
        """从文件加载历史指标"""
        if self.metrics_store_path and self.metrics_store_path.exists():
            try:
                with open(self.metrics_store_path, encoding="utf-8") as f:
                    data = json.load(f)
                    self.history = data.get("history", [])
            except Exception:
                self.history = []

    def _save(self):
        """保存指标到文件"""
        if self.metrics_store_path:
            self.metrics_store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.metrics_store_path, "w", encoding="utf-8") as f:
                json.dump({"history": self.history}, f, ensure_ascii=False, indent=2)

    def calculate_diversity(self, activation_counts: dict[str, int]) -> float:
        """
        计算记忆激活多样性（香农熵归一化）
        :param activation_counts: 记忆ID→激活次数的映射
        :return: 多样性分数（0-1，越高越好）
        """
        if not activation_counts:
            return 0.0
        total = sum(activation_counts.values())
        if total == 0:
            return 0.0
        # 香农熵
        entropy = 0.0
        for count in activation_counts.values():
            if count > 0:
                p = count / total
                entropy -= p * math.log2(p)
        # 归一化（最大熵为log2(n)）
        max_entropy = math.log2(len(activation_counts)) if len(activation_counts) > 1 else 1.0
        return min(1.0, entropy / max_entropy) if max_entropy > 0 else 0.0

    def collect_metrics(self,
                        new_distillations: int,
                        new_associations: int,
                        new_domains: int,
                        activation_counts: dict[str, int],
                        total_memories: int) -> dict:
        """
        采集再生指标
        :param new_distillations: 周期内新蒸馏数量
        :param new_associations: 周期内新关联数量
        :param new_domains: 周期内新覆盖领域数量
        :param activation_counts: 记忆激活次数映射
        :param total_memories: 总记忆数量
        :return: 再生指标
        """
        diversity = self.calculate_diversity(activation_counts)

        metrics = {
            "timestamp": datetime.now().isoformat(),
            "new_distillations": new_distillations,
            "new_associations": new_associations,
            "new_domains": new_domains,
            "activation_diversity": round(diversity, 3),
            "total_memories": total_memories,
            "regeneration_score": 0.0,
            "anti_aging_status": "unknown"
        }

        # 计算综合再生评分（加权平均）
        # 新蒸馏30% + 新关联30% + 新领域20% + 多样性20%
        distill_score = min(1.0, new_distillations / 10.0)  # 目标10条/周期
        assoc_score = min(1.0, new_associations / 30.0)  # 目标30条/周期
        domain_score = min(1.0, new_domains / 1.0)  # 目标1个/周期
        diversity_score = diversity

        regeneration_score = (
            0.3 * distill_score +
            0.3 * assoc_score +
            0.2 * domain_score +
            0.2 * diversity_score
        )
        metrics["regeneration_score"] = round(regeneration_score, 3)

        # 判定抗衰状态
        if regeneration_score >= 0.7:
            metrics["anti_aging_status"] = "healthy"
        elif regeneration_score >= 0.4:
            metrics["anti_aging_status"] = "aging"
        else:
            metrics["anti_aging_status"] = "critical"

        # 保存历史
        self.history.append(metrics)
        # 只保留最近history_days天的记录
        cutoff = datetime.now() - timedelta(days=self.history_days)
        self.history = [h for h in self.history
                       if datetime.fromisoformat(h["timestamp"]) > cutoff]
        self._save()

        return metrics

    def get_status(self) -> dict:
        """获取当前抗衰状态（最新指标）"""
        if not self.history:
            return {"anti_aging_status": "unknown", "regeneration_score": 0.0}
        return self.history[-1]

    def get_trend(self) -> dict:
        """获取再生评分趋势（最近7天）"""
        if len(self.history) < 2:
            return {"trend": "insufficient_data", "change": 0.0}
        recent = self.history[-7:] if len(self.history) >= 7 else self.history
        scores = [h["regeneration_score"] for h in recent]
        avg_recent = sum(scores[-3:]) / min(3, len(scores))
        avg_earlier = sum(scores[:3]) / min(3, len(scores))
        change = avg_recent - avg_earlier
        trend = "improving" if change > 0.05 else ("declining" if change < -0.05 else "stable")
        return {
            "trend": trend,
            "change": round(change, 3),
            "recent_avg": round(avg_recent, 3),
            "earlier_avg": round(avg_earlier, 3),
            "data_points": len(scores)
        }

    def should_trigger_regeneration(self) -> bool:
        """是否应该触发再生任务（aging或critical状态）"""
        status = self.get_status()
        return status["anti_aging_status"] in ("aging", "critical")

    def get_regeneration_recommendation(self) -> dict:
        """获取再生建议"""
        status = self.get_status()
        if status["anti_aging_status"] == "healthy":
            return {
                "action": "maintain",
                "message": "系统健康，维持当前学习节奏",
                "suggested_learning_rounds": 5
            }
        elif status["anti_aging_status"] == "aging":
            return {
                "action": "boost",
                "message": "系统进入aging状态，建议增加自主学习频率",
                "suggested_learning_rounds": 10
            }
        else:  # critical
            return {
                "action": "emergency",
                "message": "系统进入critical状态，强制触发再生任务",
                "suggested_learning_rounds": 20
            }


# 便捷函数
def check_regeneration_health(new_distillations: int = 0,
                               new_associations: int = 0,
                               new_domains: int = 0,
                               activation_counts: dict = None,
                               total_memories: int = 0,
                               store_path: str = None) -> dict:
    """便捷函数：检查再生健康状态"""
    engine = RegenerationMetrics(metrics_store_path=store_path)
    if activation_counts is None:
        activation_counts = {}
    metrics = engine.collect_metrics(
        new_distillations, new_associations, new_domains,
        activation_counts, total_memories
    )
    recommendation = engine.get_regeneration_recommendation()
    return {
        "metrics": metrics,
        "recommendation": recommendation,
        "should_trigger": engine.should_trigger_regeneration()
    }


if __name__ == "__main__":
    # 简单测试
    result = check_regeneration_health(
        new_distillations=15,
        new_associations=45,
        new_domains=2,
        activation_counts={"D539": 10, "D541": 8, "D543": 5, "D527": 3},
        total_memories=493
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
