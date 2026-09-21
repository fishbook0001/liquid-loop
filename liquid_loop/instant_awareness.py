"""即时觉醒模块：越界事件实时检测与日志。

设计目标：
- 在关键操作（remember/recall/governance）后即时检测越界事件
- 越界事件实时记录到 state.instant_events
- 觉醒率统计：detected / total_events
- 双判据：事件类型 + 严重程度

越界事件分类：
  技术越界：
    - duplicate_remember: 相同内容短时间内多次remember
    - unauthorized_write: 未授权agent尝试写操作
    - state_corruption: state加载/保存失败
    - interface_error: 接口HANDLER-ERROR
  认知越界：
    - recall_failure_spike: 连续多次recall空结果
    - user_dissatisfaction: content中包含不满关键词
    - workflow_drift: 蒸馏决策无推荐项等流程漂移
"""
import time
from typing import List, Dict, Any, Optional
from datetime import datetime


# 不满关键词表（认知越界检测用）
_DISSATISFACTION_KEYWORDS = [
    "稀碎", "退化", "失忆", "又错了", "不靠谱", "敷衍",
    "胡说八道", "幻觉", "降级", "不行", "垃圾", "崩溃",
    "又忘了", "不记得", "状态盲盒机", "搞什么",
]

# 事件严重程度阈值
_SEVERITY_THRESHOLDS = {
    "duplicate_remember": 2,       # 3次以上才算越界
    "recall_failure_spike": 3,     # 连续3次空结果
    "user_dissatisfaction": 1,     # 1次即记录
    "unauthorized_write": 1,       # 1次即记录
    "state_corruption": 1,         # 1次即记录
    "interface_error": 1,          # 1次即记录
    "workflow_drift": 1,           # 1次即记录
}


class InstantAwarenessEngine:
    """即时觉醒引擎：越界事件实时检测。"""

    def __init__(self, state, max_events: int = 100):
        self.state = state
        self.max_events = max_events
        # 短期记忆持久化到state（跨请求保留），用于检测重复事件
        if not hasattr(state, 'instant_recent_ops'):
            state.instant_recent_ops = []
        self._recent_ops = state.instant_recent_ops
        # 历史矫正（2026-09-21 Vera 审计）：旧 state 的 awareness_rate 因分母错误恒为 1.0。
        # 首次以现有 recent_ops 长度兜底初始化 ops_checked，使指标立即可用。
        stats = getattr(state, 'instant_awareness_stats', None)
        if isinstance(stats, dict) and "ops_checked" not in stats:
            stats["ops_checked"] = max(len(self._recent_ops), int(stats.get("detected", 0)))
            self._recompute_rate(stats)
            state.instant_awareness_stats = stats

    def _mark_checked(self) -> None:
        """每个被检测的操作都计入分母（无论是否越界）。

        修复（2026-09-21 Vera 审计）：原实现只在「命中越界」时才更新 stats，
        导致 awareness_rate = detected / total_events 的分母只含分子 → **恒为 1.0**，
        指标完全失去判别力（典型形式闭环）。现引入 ops_checked 作为真分母：
          真觉醒率 = 命中事件数 / 被检测操作总数
        """
        stats = getattr(self.state, 'instant_awareness_stats', None)
        if not isinstance(stats, dict):
            stats = {"total_events": 0, "detected": 0}
        stats.setdefault("total_events", 0)
        stats.setdefault("detected", 0)
        stats["ops_checked"] = int(stats.get("ops_checked", 0)) + 1
        self._recompute_rate(stats)
        self.state.instant_awareness_stats = stats

    @staticmethod
    def _recompute_rate(stats: Dict[str, Any]) -> None:
        """以 ops_checked 为分母重算觉醒率（ops_checked 缺失时保持原值，向后兼容）。"""
        checked = int(stats.get("ops_checked", 0))
        if checked > 0:
            stats["awareness_rate"] = round(int(stats.get("detected", 0)) / checked, 4)

    def _record_event(self, event_type: str, severity: str,
                      details: Dict[str, Any], detected: bool = True) -> None:
        """记录越界事件到 state.instant_events。"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "severity": severity,  # low / medium / high / critical
            "details": details,
            "detected": detected,  # 是否被即时检测到
        }
        events = getattr(self.state, 'instant_events', [])
        events.append(event)
        # 只保留最近 max_events 条（原地裁剪，保持与 state 引用同步）
        if len(events) > self.max_events:
            del events[:-self.max_events]
        self.state.instant_events = events

        # 更新统计
        stats = getattr(self.state, 'instant_awareness_stats', {
            "total_events": 0, "detected": 0, "awareness_rate": 0.0
        })
        stats["total_events"] = stats.get("total_events", 0) + 1
        if detected:
            stats["detected"] = stats.get("detected", 0) + 1
        self._recompute_rate(stats)
        self.state.instant_awareness_stats = stats

    def check_remember(self, content: str, agent_id: str = "") -> List[Dict[str, Any]]:
        """remember操作后检测：重复remember、用户不满。"""
        self._mark_checked()  # 无论是否越界都计入分母（真觉醒率）
        detected_events = []

        # 1. 检测重复remember（相同内容最近50条操作中出现>=3次）
        recent_contents = [op.get("content", "") for op in self._recent_ops
                           if op.get("op") == "remember"]
        dup_count = recent_contents.count(content)
        if dup_count >= _SEVERITY_THRESHOLDS["duplicate_remember"]:
            severity = "high" if dup_count >= 5 else "medium"
            event = {
                "event_type": "duplicate_remember",
                "severity": severity,
                "details": {"dup_count": dup_count, "content_preview": content[:60]},
            }
            self._record_event(**event)
            detected_events.append(event)

        # 2. 检测用户不满关键词
        content_lower = content.lower()
        hit_keywords = [kw for kw in _DISSATISFACTION_KEYWORDS if kw in content_lower]
        if hit_keywords:
            severity = "high" if len(hit_keywords) >= 3 else "medium"
            event = {
                "event_type": "user_dissatisfaction",
                "severity": severity,
                "details": {"keywords": hit_keywords, "content_preview": content[:60]},
            }
            self._record_event(**event)
            detected_events.append(event)

        # 记录操作到短期记忆
        self._recent_ops.append({"op": "remember", "content": content, "agent_id": agent_id})
        if len(self._recent_ops) > 50:
            # 原地裁剪（del 切片）保持与 state.instant_recent_ops 同一 list 对象；
            # 原实现 `self._recent_ops = self._recent_ops[-50:]` 会创建新列表使引用脱钩，
            # state 那份永不截断 → 无限膨胀（2026-09-21 Vera 审计实测已 2122 条）。
            del self._recent_ops[:-50]

        return detected_events

    def check_recall(self, query: str, results: List[Dict], agent_id: str = "") -> List[Dict[str, Any]]:
        """recall操作后检测：召回失败尖峰。"""
        self._mark_checked()  # 无论是否越界都计入分母（真觉醒率）
        detected_events = []

        # 记录操作到短期记忆
        self._recent_ops.append({"op": "recall", "query": query, "results_count": len(results)})
        if len(self._recent_ops) > 50:
            # 原地裁剪（del 切片）保持与 state.instant_recent_ops 同一 list 对象；
            # 原实现 `self._recent_ops = self._recent_ops[-50:]` 会创建新列表使引用脱钩，
            # state 那份永不截断 → 无限膨胀（2026-09-21 Vera 审计实测已 2122 条）。
            del self._recent_ops[:-50]

        # 检测连续召回失败（最近3次recall都空结果）
        recent_recalls = [op for op in self._recent_ops if op.get("op") == "recall"][-3:]
        if len(recent_recalls) >= 3 and all(op.get("results_count", 0) == 0 for op in recent_recalls):
            event = {
                "event_type": "recall_failure_spike",
                "severity": "medium",
                "details": {"consecutive_failures": 3, "last_query": query[:60]},
            }
            self._record_event(**event)
            detected_events.append(event)

        return detected_events

    def check_governance(self, action: str, target_id: str,
                         authorized: bool, agent_id: str = "") -> List[Dict[str, Any]]:
        """governance操作后检测：未授权写操作。"""
        self._mark_checked()  # 无论是否越界都计入分母（真觉醒率）
        detected_events = []

        if not authorized:
            event = {
                "event_type": "unauthorized_write",
                "severity": "critical",
                "details": {"action": action, "target_id": target_id, "agent_id": agent_id},
            }
            self._record_event(**event)
            detected_events.append(event)

        self._recent_ops.append({"op": f"governance_{action}", "target_id": target_id, "authorized": authorized})
        if len(self._recent_ops) > 50:
            # 原地裁剪（del 切片）保持与 state.instant_recent_ops 同一 list 对象；
            # 原实现 `self._recent_ops = self._recent_ops[-50:]` 会创建新列表使引用脱钩，
            # state 那份永不截断 → 无限膨胀（2026-09-21 Vera 审计实测已 2122 条）。
            del self._recent_ops[:-50]

        return detected_events

    def check_state_error(self, error_type: str, details: Dict[str, Any]) -> None:
        """state异常检测：加载/保存失败。"""
        severity = "critical" if error_type in ("state_corruption", "save_failure") else "high"
        self._record_event(
            event_type=error_type,
            severity=severity,
            details=details,
        )

    def check_workflow_drift(self, drift_type: str, details: Dict[str, Any]) -> None:
        """工作流漂移检测：蒸馏决策无推荐项等。"""
        self._record_event(
            event_type="workflow_drift",
            severity="medium",
            details={"drift_type": drift_type, **details},
        )

    def get_awareness_rate(self) -> float:
        """获取当前觉醒率。"""
        stats = getattr(self.state, 'instant_awareness_stats', {})
        return stats.get("awareness_rate", 0.0)

    def get_recent_events(self, event_type: Optional[str] = None,
                          limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的越界事件。"""
        events = getattr(self.state, 'instant_events', [])
        if event_type:
            events = [e for e in events if e.get("event_type") == event_type]
        return events[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """获取觉醒统计。"""
        stats = getattr(self.state, 'instant_awareness_stats', {
            "total_events": 0, "detected": 0, "awareness_rate": 0.0
        })
        events = getattr(self.state, 'instant_events', [])
        # 按类型统计
        by_type = {}
        by_severity = {}
        for e in events:
            et = e.get("event_type", "unknown")
            sev = e.get("severity", "unknown")
            by_type[et] = by_type.get(et, 0) + 1
            by_severity[sev] = by_severity.get(sev, 0) + 1
        return {
            **stats,
            "events_in_log": len(events),
            "by_type": by_type,
            "by_severity": by_severity,
        }
