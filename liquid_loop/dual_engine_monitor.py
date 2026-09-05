"""
双引擎监控架构（Dual-Engine Monitoring Architecture）
基于洞察四：液环的"元认知"=自我状态监控引擎（45a区）+ 外部环境评估引擎（47/12o区）。
参考D542猕猴前瞻性元认知/双引擎架构：主动选择正确率90% vs 被迫45%。
扩展参考D567三大脑区协同决策地图：自我监控+外部评估+决策融合。
"""
import json
import math
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple


class DualEngineMonitor:
    """双引擎监控架构"""
    
    def __init__(self, monitor_store_path: str = None):
        """
        初始化双引擎监控
        :param monitor_store_path: 监控数据存储路径
        """
        self.monitor_store_path = Path(monitor_store_path) if monitor_store_path else None
        self.audit_log = []  # 决策审计日志
        self._load()
    
    def _load(self):
        """从文件加载审计日志"""
        if self.monitor_store_path and self.monitor_store_path.exists():
            try:
                with open(self.monitor_store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.audit_log = data.get("audit_log", [])
            except:
                self.audit_log = []
    
    def _save(self):
        """保存审计日志"""
        if self.monitor_store_path:
            self.monitor_store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.monitor_store_path, "w", encoding="utf-8") as f:
                json.dump({"audit_log": self.audit_log}, f, ensure_ascii=False, indent=2)
    
    # ========== 引擎一：自我状态监控（Internal Engine，对应45a区）==========
    
    def monitor_internal_health(self, 
                                 memory_total: int,
                                 recall_success_rate: float,
                                 association_density: float,
                                 regeneration_score: float,
                                 task_completion_rate: float,
                                 error_rate: float,
                                 cpu_usage: float,
                                 memory_usage: float,
                                 disk_usage: float,
                                 service_status: str = "ok") -> Dict:
        """
        引擎一：自我状态监控
        监控维度：记忆健康、再生状态、执行质量、资源状态
        """
        # 记忆健康评分（0-1）
        memory_health = (
            0.3 * min(1.0, memory_total / 500.0) +  # 记忆总量
            0.3 * recall_success_rate +  # 检索成功率
            0.2 * min(1.0, association_density / 0.5) +  # 关联密度
            0.2 * regeneration_score  # 再生评分
        )
        
        # 执行质量评分
        execution_quality = (
            0.5 * task_completion_rate +
            0.5 * (1.0 - error_rate)
        )
        
        # 资源状态评分
        resource_health = (
            0.3 * (1.0 - cpu_usage) +
            0.3 * (1.0 - memory_usage) +
            0.2 * (1.0 - disk_usage) +
            0.2 * (1.0 if service_status == "ok" else 0.0)
        )
        
        # 综合自我健康评分
        internal_health = (
            0.4 * memory_health +
            0.3 * execution_quality +
            0.3 * resource_health
        )
        
        return {
            "engine": "internal",
            "timestamp": datetime.now().isoformat(),
            "internal_health": round(internal_health, 3),
            "memory_health": round(memory_health, 3),
            "execution_quality": round(execution_quality, 3),
            "resource_health": round(resource_health, 3),
            "details": {
                "memory_total": memory_total,
                "recall_success_rate": recall_success_rate,
                "association_density": association_density,
                "regeneration_score": regeneration_score,
                "task_completion_rate": task_completion_rate,
                "error_rate": error_rate,
                "cpu_usage": cpu_usage,
                "memory_usage": memory_usage,
                "disk_usage": disk_usage,
                "service_status": service_status
            }
        }
    
    # ========== 引擎二：外部环境评估（External Engine，对应47/12o区）==========
    
    def monitor_external_quality(self,
                                   input_relevance: float,
                                   information_density: float,
                                   noise_level: float,
                                   domain_match: float,
                                   distribution_shift: float,
                                   user_intent_clarity: float,
                                   urgency: float,
                                   risk_level: float) -> Dict:
        """
        引擎二：外部环境评估
        评估维度：输入质量、领域匹配、分布偏移、用户意图
        """
        # 输入质量评分
        input_quality = (
            0.3 * input_relevance +
            0.3 * information_density +
            0.2 * (1.0 - noise_level) +
            0.2 * domain_match
        )
        
        # 分布偏移适应性评分（偏移越大，适应性挑战越大）
        adaptability = 1.0 - distribution_shift * 0.5  # 偏移1.0时适应性0.5
        
        # 用户意图评分
        intent_quality = (
            0.4 * user_intent_clarity +
            0.3 * (1.0 - urgency) +  # 越紧急，决策质量要求越高
            0.3 * (1.0 - risk_level)  # 风险越高，越需要谨慎
        )
        
        # 综合外部质量评分
        external_quality = (
            0.4 * input_quality +
            0.3 * adaptability +
            0.3 * intent_quality
        )
        
        return {
            "engine": "external",
            "timestamp": datetime.now().isoformat(),
            "external_quality": round(external_quality, 3),
            "input_quality": round(input_quality, 3),
            "adaptability": round(adaptability, 3),
            "intent_quality": round(intent_quality, 3),
            "details": {
                "input_relevance": input_relevance,
                "information_density": information_density,
                "noise_level": noise_level,
                "domain_match": domain_match,
                "distribution_shift": distribution_shift,
                "user_intent_clarity": user_intent_clarity,
                "urgency": urgency,
                "risk_level": risk_level
            }
        }
    
    # ========== 决策融合层（对应D567三大脑区协同决策）==========
    
    def fuse_decision(self, internal_result: Dict, external_result: Dict,
                       w_internal: float = 0.5, w_external: float = 0.5) -> Dict:
        """
        决策融合层：双引擎评估结果融合为自主决策
        决策模式：
        - 高置信自主执行：internal≥0.7 且 external≥0.7
        - 优先自我修复：internal<0.5
        - 请求用户澄清：external<0.5
        - 保守执行（带验证）：其他
        """
        internal_health = internal_result.get("internal_health", 0.5)
        external_quality = external_result.get("external_quality", 0.5)
        
        decision_score = w_internal * internal_health + w_external * external_quality
        
        # 决策模式判定
        if internal_health >= 0.7 and external_quality >= 0.7:
            mode = "autonomous_high_confidence"
            action = "自主决策执行（高置信）"
            need_verification = False
        elif internal_health < 0.5:
            mode = "self_repair_priority"
            action = "优先自我修复（不执行新任务）"
            need_verification = True
        elif external_quality < 0.5:
            mode = "request_clarification"
            action = "请求用户澄清（不自主执行）"
            need_verification = True
        else:
            mode = "conservative_with_verification"
            action = "保守执行（带验证环节）"
            need_verification = True
        
        decision = {
            "timestamp": datetime.now().isoformat(),
            "decision_score": round(decision_score, 3),
            "internal_health": internal_health,
            "external_quality": external_quality,
            "mode": mode,
            "action": action,
            "need_verification": need_verification,
            "weights": {"internal": w_internal, "external": w_external}
        }
        
        # 记录审计日志
        self.audit_log.append({
            "decision": decision,
            "internal_result": internal_result,
            "external_result": external_result
        })
        # 只保留最近100条
        self.audit_log = self.audit_log[-100:]
        self._save()
        
        return decision
    
    def get_audit_log(self, limit: int = 20) -> List[Dict]:
        """获取决策审计日志"""
        return self.audit_log[-limit:]
    
    def get_monitor_stats(self) -> Dict:
        """获取监控统计"""
        if not self.audit_log:
            return {"total_decisions": 0}
        modes = {}
        for entry in self.audit_log:
            mode = entry["decision"]["mode"]
            modes[mode] = modes.get(mode, 0) + 1
        avg_score = sum(e["decision"]["decision_score"] for e in self.audit_log) / len(self.audit_log)
        return {
            "total_decisions": len(self.audit_log),
            "mode_distribution": modes,
            "average_decision_score": round(avg_score, 3)
        }


# 便捷函数
def dual_engine_monitor_decision(internal_params: Dict, external_params: Dict,
                                   store_path: str = None) -> Dict:
    """便捷函数：执行双引擎监控决策"""
    monitor = DualEngineMonitor(monitor_store_path=store_path)
    internal = monitor.monitor_internal_health(**internal_params)
    external = monitor.monitor_external_quality(**external_params)
    decision = monitor.fuse_decision(internal, external)
    return {
        "internal": internal,
        "external": external,
        "decision": decision
    }


if __name__ == "__main__":
    # 简单测试
    result = dual_engine_monitor_decision(
        internal_params={
            "memory_total": 493, "recall_success_rate": 0.85,
            "association_density": 0.4, "regeneration_score": 0.82,
            "task_completion_rate": 0.9, "error_rate": 0.05,
            "cpu_usage": 0.3, "memory_usage": 0.4, "disk_usage": 0.5
        },
        external_params={
            "input_relevance": 0.8, "information_density": 0.7,
            "noise_level": 0.1, "domain_match": 0.9,
            "distribution_shift": 0.2, "user_intent_clarity": 0.85,
            "urgency": 0.3, "risk_level": 0.2
        }
    )
    print(json.dumps(result["decision"], ensure_ascii=False, indent=2))
