#!/usr/bin/env python3
"""
auction_route.py — 顺序拍卖路由原语（蒸馏 DeepMind arXiv:2610.22201）
=======================================================================
用顺序拍卖替代中央图规划器：agent 出价披露私有能力 / 置信度，
最高价（最优出价）胜出路由。同构 OFAMAS 任务感知动态路由。

论文核心（已一手核验 2610.22201）：
  - 去中心化 LLM 工作流通过 sequential auctions；每个子任务独立拍卖。
  - agent 的 bid 披露其私有能力 / 置信度（私有信息），拍卖聚合后决定路由。
  - 仍存在中央 assessor 对子任务难度 / 路由特征做 0–10 编码（中央不消失，
    只是从"图规划器"重定位为"评估者 / 做市商"）——本原语只负责"拍卖路由"段，
    assessor 段由调用方（Vera 验证者 / 做市商角色）提供 task_state.difficulty。

本原语把它极简成零依赖、可被 liquid_loop / vera 编排 import 的通用路由底座：
  route(task_state, bids) -> RouteDecision（胜出 agent + 置信披露 + 拒标记录）

与液环同构：
  - 出价披露能力 ↔ OFAMAS 任务感知动态路由（按 agent 负载 / 能力动态派活）。
  - 出价受 trust_level 约束 ↔ 权限网关信任分级(0/1/2)：低 trust 异常高报价
    视为潜在虚报 / 串标，降权或拦截（治理不消失，只是改成行为约束）。
  - 中央 assessor 不消失 ↔ Vera 验证者（不可删的"评估者 / 做市商"）。

依赖：仅 Python 标准库。零外部依赖。生产路径 3.9.6 兼容
（from __future__ import annotations；禁 3.10+ 运行时语法）。
接活范式：落 liquid_loop 包 + __init__ 导出 + bridge 消费方正对照。

已知拍卖参与方（事实锚 · 2026-10-21 飞哥拍板固化）：飞哥实际四方异构竞争者集 =
vera（验证者）/ 马维斯（桌面交互）/ 豆包（同层独立编排）/ 液环（8790 记忆后端）。
本原语身份无关（agent_id 为通用字符串），此名单仅作调用方事实锚，不写死进路由逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# trust 阈值：低于此级的 agent 异常高报价需拦截（权限网关同构）。
LOW_TRUST_GUARD_LEVEL = 1
# 异常报价相对次高价的倍数阈值：超过即视为可疑虚报。
SUSPICIOUS_BID_RATIO = 3.0


@dataclass
class Bid:
    """一个 agent 对某子任务的出价（私有信息披披露）。"""
    agent_id: str
    declared_capability: str      # 声明能做的子任务类型
    confidence: float             # 私有置信度 0..1
    bid_value: float              # 出价（愿为处理权付出的代价 / 报价）
    trust_level: int = 2          # 权限网关信任分级 0/1/2
    metadata: dict = field(default_factory=dict)


@dataclass
class RouteDecision:
    """路由决策结果。"""
    winner: Optional[str]            # 胜出 agent_id（无有效出价时为 None）
    rationale: str                   # 决策理由（可解释）
    disclosed_confidence: float      # 胜出者披露的置信度
    rejected: list = field(default_factory=list)  # 被拒标记录 [(agent_id, reason)]
    runner_up: Optional[str] = None  # 次优（便于失败重拍卖）


def route(task_state: dict, bids: list) -> RouteDecision:
    """
    顺序拍卖路由：选最高 bid_value 的 agent 胜出；平局以 confidence 优先；
    低 trust 异常高报价按治理规则拦截。

    task_state 需含可选字段：
      - difficulty: float 子任务难度（由中央 assessor / 调用方提供，本原语不评估）
      - required_capability: str 期望能力（与 bid.declared_capability 匹配优先）

    返回 RouteDecision。
    """
    if not bids:
        return RouteDecision(
            winner=None, rationale="无出价，路由悬空（交由默认调度 / 中央 assessor）",
            disclosed_confidence=0.0, rejected=[], runner_up=None,
        )

    # 1. 能力匹配优先：过滤掉声明能力不匹配的（软约束，不匹配仍保留但降权）
    matched = [b for b in bids if b.declared_capability == task_state.get("required_capability")]
    pool = matched if matched else list(bids)

    # 2. 排序：bid_value 降序；平局 confidence 降序；再 trust 降序。
    ranked = sorted(
        pool,
        key=lambda b: (b.bid_value, b.confidence, b.trust_level),
        reverse=True,
    )

    winner = ranked[0]
    runner_up = ranked[1].agent_id if len(ranked) > 1 else None
    rejected: list = []

    # 3. 治理拦截：低 trust 异常高报价（虚报 / 串标嫌疑）
    second_best = ranked[1].bid_value if len(ranked) > 1 else 0.0
    if (winner.trust_level < LOW_TRUST_GUARD_LEVEL
            and second_best > 0
            and winner.bid_value / second_best > SUSPICIOUS_BID_RATIO):
        # 不删除出价，仅将胜出权让渡给次优（fail-open：路由不崩，降级处理）
        rejected.append((winner.agent_id,
                         f"低 trust({winner.trust_level}) 异常高报价 "
                         f"{winner.bid_value:.2f} >> 次优 {second_best:.2f}，按治理拦截，让渡次优"))
        if runner_up is not None:
            winner = ranked[1]
            runner_up = ranked[2].agent_id if len(ranked) > 2 else None
        else:
            winner = None  # 无次优可用，路由悬空

    rationale = (
        f"拍卖胜出 {winner.agent_id if winner else 'None'}"
        f"（bid={winner.bid_value:.2f}, conf={winner.confidence:.2f}, "
        f"trust={winner.trust_level}）"
        + (f"；难度={task_state.get('difficulty')}" if task_state.get("difficulty") is not None else "")
    )
    return RouteDecision(
        winner=winner.agent_id if winner else None,
        rationale=rationale,
        disclosed_confidence=winner.confidence if winner else 0.0,
        rejected=rejected,
        runner_up=runner_up,
    )


# ---------------------------------------------------------------------------
# 自测（运行时验证，零依赖）
# ---------------------------------------------------------------------------
def _selftest() -> None:
    # 1. 基本：最高价胜出
    bids = [
        Bid("a", "summarize", 0.8, 3.0),
        Bid("b", "summarize", 0.6, 5.0),
        Bid("c", "summarize", 0.7, 2.0),
    ]
    d = route({"required_capability": "summarize", "difficulty": 0.5}, bids)
    assert d.winner == "b", f"最高价胜出失败: {d.winner}"
    print("✓ 最高价胜出路由")

    # 2. 平局以 confidence 优先（同 bid_value）
    bids2 = [
        Bid("a", "translate", 0.9, 4.0),
        Bid("b", "translate", 0.5, 4.0),
    ]
    d2 = route({"required_capability": "translate"}, bids2)
    assert d2.winner == "a", f"平局置信优先失败: {d2.winner}"
    print("✓ 平局以置信优先")

    # 3. 治理拦截：低 trust 异常高报价让渡次优
    bids3 = [
        Bid("rogue", "code", 0.99, 30.0, trust_level=0),
        Bid("honest", "code", 0.8, 6.0, trust_level=2),
    ]
    d3 = route({"required_capability": "code"}, bids3)
    assert d3.winner == "honest", f"低 trust 拦截失败: {d3.winner}"
    assert d3.rejected and d3.rejected[0][0] == "rogue", f"拒标记录缺失: {d3.rejected}"
    print("✓ 低 trust 异常高报价治理拦截")

    # 4. 空出价：路由悬空不崩
    d4 = route({"required_capability": "x"}, [])
    assert d4.winner is None and d4.runner_up is None, f"空出价处理失败: {d4}"
    print("✓ 空出价路由悬空（fail-open）")

    # 5. 能力不匹配软约束：无匹配时退回全池
    bids5 = [
        Bid("a", "summarize", 0.7, 4.0),
        Bid("b", "translate", 0.6, 9.0),
    ]
    d5 = route({"required_capability": "code"}, bids5)  # 无 code 匹配
    assert d5.winner == "b", f"无匹配退回全池失败: {d5.winner}"
    print("✓ 无能力匹配退回全池（软约束）")

    print("SELFTEST ALL GREEN")


if __name__ == "__main__":
    _selftest()
