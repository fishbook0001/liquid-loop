#!/usr/bin/env python3
"""
bucket_brigade_credit.py — Bucket-Brigade 信用分配原语（蒸馏 DeepMind arXiv:2610.22201 + Harvard/MIT MPT 2610.22322）
=========================================================================================================================
集体结果 -> 局部训练信用，替代中央 credit assignment。同构液环 distill 归因 / 价值记账。

论文核心（已一手核验）：
  - 2610.22201：decentralized workflows 中，信用沿"成功链路"向后传递，每个 agent 的
    局部训练信用由其促成的下游成功部分决定（bucket-brigade：信用像桶一样沿链路传递）。
  - 2610.22322 (Yilun Du, Harvard/MIT MPT)：self-orchestration in decentralized agent
    swarms，学习"谁该为集体结果负多少责"——即去中心化 credit assignment。

本原语把它极简成零依赖、可被 liquid_loop / vera 编排 import 的通用信用分配底座：
  assign_credit(collective_outcome, contributions, links=None) -> dict[agent_id, weight]

与液环同构：
  - 信用沿链路向后传递 ↔ 液环 distill 归因（某条蒸馏资产的"价值"由下游消费成效反推上游）。
  - 集体结果 -> 局部权重 ↔ 液环价值记账（记忆资产的 importance / weight 由 recall 成效更新）。
  - 去中心化 credit assignment ↔ 权限网关：信用分配不依赖中央评分，而是沿贡献链路计算。

设计约束（蒸馏铁律）：
  - 保留信用**绝对值**（不强制归一化到 sum=1）：正 outcome 整体信用高、负 outcome 整体低，
    各 agent 相对比例由 activation 决定 —— 这样集体结果的好坏能反映到信用总量上。
  - fail-open：空贡献返回 {}；负激活截断为 0；floor 保底避免全零梯度消失。

依赖：仅 Python 标准库。零外部依赖。生产路径 3.9.6 兼容。
接活范式：落 liquid_loop 包 + __init__ 导出 + bridge 消费方正对照。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 链式传导系数：上游贡献的信用沿链路向后传递时的衰减（bucket-brigade 衰减）。
CHAIN_DECAY = 0.5
# 信用 floor：保证最低信用，避免全零惩罚导致下游无法学习（fail-open）。
MIN_CREDIT_FLOOR = 0.01


@dataclass
class Contribution:
    """一个 agent 对集体任务的贡献。"""
    agent_id: str
    activation: float                 # 该 agent 的"激活强度" / 投入量（>=0）
    role: str = "worker"             # 角色（worker / coordinator / evaluator）
    upstream_of: Optional[str] = None  # 若指定，则本贡献在链路中位于该 agent 上游


def assign_credit(collective_outcome: float, contributions: list,
                  links: Optional[list] = None) -> dict:
    """
    按贡献占比 + 集体结果分配局部信用权重（保留绝对值，不归一化）。

    collective_outcome: 集体结果质量分数（可正可负；负则整体降权但保底 floor）。
    contributions: list[Contribution]
    links: 可选，list[(upstream_agent_id, downstream_agent_id)] 声明链路，
           用于 bucket-brigade 链式传导（上游信用沿链路向后传递并衰减）。

    返回 {agent_id: weight}，weight 越大表示该 agent 对集体结果贡献越大；
    正 outcome 时所有权重整体抬升、负 outcome 时整体压低。
    fail-open：空贡献返回 {}；负激活截断为 0；不抛异常。
    """
    if not contributions:
        return {}

    # 1. 基础权重 = 激活强度占比（负激活截断为 0）
    acts = {c.agent_id: max(0.0, float(c.activation)) for c in contributions}
    total_act = sum(acts.values())
    if total_act <= 0:
        # 全是零/负激活：均匀分配（fail-open，避免全零导致下游无法学习）
        n = len(contributions)
        return {c.agent_id: MIN_CREDIT_FLOOR for c in contributions}

    base = {aid: a / total_act for aid, a in acts.items()}

    # 2. 集体结果调节（保留绝对值，不归一化）：
    #    正 outcome 放大（0..2 倍）、负 outcome 缩小（下限 0）。
    if collective_outcome >= 0:
        scale = 1.0 + min(collective_outcome, 1.0)
    else:
        scale = max(0.0, 1.0 + collective_outcome)
    scaled = {aid: w * scale for aid, w in base.items()}

    # 3. bucket-brigade 链式传导：上游信用沿链路向后传递并衰减（累加到下游）
    if links:
        for up_id, down_id in links:
            if up_id in scaled and down_id in scaled:
                carry = scaled[up_id] * CHAIN_DECAY
                scaled[down_id] = scaled.get(down_id, 0.0) + carry

    # 4. floor 保底（保留 outcome 绝对值，不强制归一化到 sum=1）
    out = {aid: max(MIN_CREDIT_FLOOR, w) for aid, w in scaled.items()}
    return out


# ---------------------------------------------------------------------------
# 自测（运行时验证，零依赖）
# ---------------------------------------------------------------------------
def _selftest() -> None:
    # 1. 占比保持（信用绝对值随 outcome 浮动，相对比例由 activation 决定）
    contribs = [
        Contribution("a", 3.0), Contribution("b", 1.0), Contribution("c", 1.0),
    ]
    w = assign_credit(0.8, contribs)
    assert abs(w["a"] - 3.0 * w["b"]) < 1e-6, f"占比 3:1 失败: {w}"
    assert abs(w["b"] - w["c"]) < 1e-6, f"b/c 等比失败: {w}"
    print("✓ 按 activation 占比分配（绝对值随 outcome 浮动）")

    # 2. 正 outcome 放大绝对值（scale 随集体结果提升）
    w2 = assign_credit(1.0, contribs)
    assert w2["a"] > w["a"], f"正 outcome 未放大(a): {w2['a']} vs {w['a']}"
    assert w2["b"] > w["b"], f"正 outcome 未放大(b): {w2['b']} vs {w['b']}"
    print("✓ 正 outcome 抬升整体信用绝对值")

    # 3. bucket-brigade 链式传导：下游获上游衰减传递，权重最高
    contribs3 = [
        Contribution("up", 2.0, upstream_of="mid"),
        Contribution("mid", 2.0, upstream_of="down"),
        Contribution("down", 2.0),
    ]
    links = [("up", "mid"), ("mid", "down")]
    w3 = assign_credit(0.5, contribs3, links=links)
    assert w3["down"] >= w3["up"], f"链式传导失败: {w3}"
    assert w3["down"] >= w3["mid"], f"链式传导(下游>中游)失败: {w3}"
    print("✓ bucket-brigade 链式信用传导（下游获上游衰减传递）")

    # 4. 空贡献 fail-open
    assert assign_credit(0.5, []) == {}, f"空贡献未返回空: {assign_credit(0.5, [])}"
    print("✓ 空贡献 fail-open 返回 {}")

    # 5. 负激活截断
    contribs5 = [Contribution("a", -1.0), Contribution("b", 4.0)]
    w5 = assign_credit(0.5, contribs5)
    assert w5["a"] < w5["b"], f"负激活未截断: {w5}"
    print("✓ 负激活截断（不传播负值）")

    print("SELFTEST ALL GREEN")


if __name__ == "__main__":
    _selftest()
