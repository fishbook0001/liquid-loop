"""认知预算稳态器（落地 PEEK arXiv:2604.09932 固定预算驱逐）

把 PEEK 三模块蒸馏并适配液环：
  Distiller     evidence.weight + anchor.value_score + recency → 蒸馏价值
  Cartographer  价值 × (0.5+0.5*访问热度) × 流动性 → 重要性
  Evictor       超额时驱逐最低重要性证据，标记 archived（冷归档，零丢失）

设计哲学（呼应液环零向量/零丢失）：
  驱逐 ≠ 删除。超预算证据标记 archived=True，从活跃检索集移出（recall 跳过），
  但数据与审计链完整保留，可随时解冻。记忆系统因此维持"稳态预算"而非无限膨胀。

预算来源：环境变量 LIQUID_EVIDENCE_BUDGET（默认 0 = 不限制）。
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

LIQUIDITY_HEAT = {"hot": 1.0, "warm": 0.7, "cold": 0.4, "frozen": 0.1}


@dataclass
class _BudgetItem:
    id: str
    weight: float = 1.0
    anchor_value: float = 1.0
    access_count: int = 0
    liquidity: str = "warm"
    gap_hours: float = 0.0
    distilled_value: float = 0.0
    importance: float = 0.0


def _distill(it: _BudgetItem) -> float:
    recency = 1.0 / (1.0 + it.gap_hours / 168.0)
    v = 0.4 * it.weight + 0.3 * it.anchor_value + 0.3 * recency
    it.distilled_value = max(0.0, min(1.0, v))
    return it.distilled_value


def _rank(items: list[_BudgetItem]) -> list[_BudgetItem]:
    for it in items:
        if it.distilled_value == 0.0:
            _distill(it)
        access_heat = min(it.access_count / 10.0, 1.0)
        liquidity_heat = LIQUIDITY_HEAT.get(it.liquidity, 0.4)
        it.importance = it.distilled_value * (0.5 + 0.5 * access_heat) * liquidity_heat
    return sorted(items, key=lambda x: x.importance, reverse=True)


def _evict(items: list[_BudgetItem], budget: int) -> list[_BudgetItem]:
    if len(items) <= budget:
        return []
    ranked = _rank(items)
    return ranked[budget:]  # 尾部最低重要性


class CognitiveBudgetStabilizer:
    """接 WorkspaceState，超预算时冷归档低价值证据"""

    def __init__(self, state, budget: Optional[int] = None):
        self.state = state
        self.budget = budget if budget is not None else int(os.environ.get("LIQUID_EVIDENCE_BUDGET", "0"))

    @staticmethod
    def _gap_hours(anchor) -> float:
        if not anchor or not anchor.last_accessed:
            return 9999.0
        try:
            last = datetime.fromisoformat(anchor.last_accessed)
            return (datetime.now(timezone.utc) - last).total_seconds() / 3600
        except ValueError:
            return 9999.0

    def stabilize(self) -> dict:
        if self.budget <= 0:
            return {"evicted": 0, "budget": 0, "total": len(self.state.evidences),
                    "note": "预算未设置（LIQUID_EVIDENCE_BUDGET=0），跳过"}
        evs = self.state.evidences
        if len(evs) <= self.budget:
            return {"evicted": 0, "budget": self.budget, "total": len(evs)}
        anchor_map = {a.id: a for a in self.state.anchors}
        items: list[_BudgetItem] = []
        for e in evs:
            a = anchor_map.get(e.anchor_id)
            items.append(_BudgetItem(
                id=e.id,
                weight=e.weight,
                anchor_value=a.value_score if a else 0.5,
                access_count=a.access_count if a else 0,
                liquidity=a.liquidity if a else "warm",
                gap_hours=self._gap_hours(a),
            ))
        evicted = _evict(items, self.budget)
        evicted_ids = {it.id for it in evicted}
        for e in evs:
            if e.id in evicted_ids:
                e.archived = True  # 冷归档，零丢失
        return {
            "evicted": len(evicted_ids),
            "evicted_ids": list(evicted_ids),
            "budget": self.budget,
            "total": len(evs),
        }
