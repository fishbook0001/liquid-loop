#!/usr/bin/env python3
"""
cost_meter.py — per-agent 成本/令牌计量 + 会话缓存亲和（magpie 式本地 AI 网关机制内化）
==========================================================================================
蒸馏自 yetone/magpie（军师调研 v392，2026-10-21 一手核验：GitHub yetone/magpie ★18.4k/MIT/Go）。
2026-10-21 接活进液环运行时（v392_candidate05/06 落地原语）。

magpie 的核心机制（本地 AI 网关聚合多订阅额度）：
  - per-agent 维度计量每个 agent / 账户的 token 与成本（Usage & Cost），可导出
  - 会话缓存亲和（conversation affinity）：同一会话钉在同一 provider/account，
    避免提示缓存失效重计；failover 时才打破亲和

本原语把它极简成可被液环/vera 脚本 import 的零依赖旁路计量 + 亲和记账底座：
  - record() 按 agent_id / account_id / session_id 维度旁路计量 token / cost（不阻塞主推理）
  - bind_affinity() / affinity_for() / break_affinity() 做会话缓存亲和记账（failover 打破）
  - export_otlp() 导出 OTLP 兼容 gauge 骨架（旁路，fail-open，真实上报需 OTLP exporter）

与液环同构：
  - 评估域成本建模（本机缺口，magpie 补）↔ 本原语 per-agent 计量
  - 上下文缓存（affinity_key=account_id，failover 时打破）↔ 本原语 break_affinity
与 Vera 观测层同构：proprioception 可观测（per-agent 成本/亲和状态可读）

依赖：仅 Python 标准库。零外部依赖。
接活范式（零侵入）：本文件 + __init__ 暴露 + 自测 → server / agent loop 经 liquid_loop 包 import。
设计约束（蒸馏铁律）：只做旁路计量 + 亲和记账，不侵入推理主链路；fail-open（导出失败不阻断）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Usage:
    """一次推理调用的用量快照（magpie Usage & Cost 的最小实现）。"""
    agent_id: str
    account_id: str
    session_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    ts: float = field(default_factory=time.time)


@dataclass
class AffinityRecord:
    """会话缓存亲和记账（magpie conversation affinity 的最小实现）。"""
    session_id: str
    account_id: str
    bound_at: float = field(default_factory=time.time)
    broken: bool = False


class CostMeter:
    """
    per-agent 成本/令牌计量 + 会话缓存亲和记账（magpie 网关机制的液环内化）。

    语义：
      - record(usage) 旁路追加一条用量记录（不阻塞主推理，fail-open）
      - agent_cost(agent_id) / account_cost(account_id) 聚合查询
      - bind_affinity(session_id, account_id) 钉住会话亲和
      - affinity_for(session_id) 返回当前亲和 account（None=无钉住）
      - break_affinity(session_id) failover 时打破（让路由可切到新 account）
      - export_otlp() 导出 OTLP 兼容 gauge 骨架（旁路，失败返回空列表不抛）

    设计约束（蒸馏铁律）：
      - 只允许旁路只读聚合 + 记账；不修改推理链路、不持有连接
      - fail-open：record / export 异常内部吞掉，不向上传播阻断业务
    """

    def __init__(self, name: str = "cost_meter") -> None:
        self.name = name
        self._records: list[Usage] = []
        self._affinity: dict[str, AffinityRecord] = {}

    # ---- 计量 ----

    def record(self, usage: Usage) -> None:
        """旁路追加一条用量记录。fail-open：非 Usage / 异常内部吞掉并丢弃，
        坏记录不进入 _records，避免污染后续聚合。"""
        try:
            if isinstance(usage, Usage):
                self._records.append(usage)
        except Exception:
            pass  # fail-open：计量失败不影响主推理

    def agent_cost(self, agent_id: str) -> dict:
        """按 agent 维度聚合（token 总量 + 成本 + 调用次数）。"""
        calls = 0
        pt = ct = 0
        cost = 0.0
        for u in self._records:
            if u.agent_id == agent_id:
                calls += 1
                pt += u.prompt_tokens
                ct += u.completion_tokens
                cost += u.cost
        return {
            "agent_id": agent_id,
            "calls": calls,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "cost": round(cost, 6),
        }

    def account_cost(self, account_id: str) -> dict:
        """按 account 维度聚合（多 agent 共享同一订阅额度时看总消耗）。"""
        calls = 0
        pt = ct = 0
        cost = 0.0
        for u in self._records:
            if u.account_id == account_id:
                calls += 1
                pt += u.prompt_tokens
                ct += u.completion_tokens
                cost += u.cost
        return {
            "account_id": account_id,
            "calls": calls,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "cost": round(cost, 6),
        }

    # ---- 会话缓存亲和 ----

    def bind_affinity(self, session_id: str, account_id: str) -> None:
        """钉住会话亲和（同一会话走同一 account，利用提示缓存）。"""
        self._affinity[session_id] = AffinityRecord(
            session_id=session_id, account_id=account_id)

    def affinity_for(self, session_id: str) -> Optional[str]:
        """返回当前会话亲和 account_id；未钉住或已打破返回 None。"""
        rec = self._affinity.get(session_id)
        if rec is None or rec.broken:
            return None
        return rec.account_id

    def break_affinity(self, session_id: str) -> None:
        """failover 时打破亲和（允许路由切到新 account）。幂等。"""
        rec = self._affinity.get(session_id)
        if rec is not None:
            rec.broken = True

    # ---- 导出 ----

    def export_otlp(self) -> list:
        """
        导出 OTLP 兼容 gauge 骨架（旁路，fail-open）。
        真实上报需 OTLP exporter（opentelemetry 包），此处只产出结构，
        由调用方决定 sink（日志 / OTLP HTTP / 文件）。失败返回空列表不抛。
        """
        try:
            metrics: list = []
            # per-agent 成本 gauge
            seen_agents: set = set()
            for u in self._records:
                if u.agent_id in seen_agents:
                    continue
                seen_agents.add(u.agent_id)
                agg = self.agent_cost(u.agent_id)
                metrics.append({
                    "name": "ll.agent.cost",
                    "gauge": {"value": agg["cost"]},
                    "attributes": {"agent_id": u.agent_id},
                })
                metrics.append({
                    "name": "ll.agent.total_tokens",
                    "gauge": {"value": agg["total_tokens"]},
                    "attributes": {"agent_id": u.agent_id},
                })
            # per-session 亲和 state gauge（1=钉住, 0=打破/无）
            for sid, rec in self._affinity.items():
                metrics.append({
                    "name": "ll.session.affinity_bound",
                    "gauge": {"value": 0 if rec.broken else 1},
                    "attributes": {
                        "session_id": sid,
                        "account_id": rec.account_id,
                    },
                })
            return metrics
        except Exception:
            return []  # fail-open


# ---------------------------------------------------------------------------
# 自测（运行时验证）
# ---------------------------------------------------------------------------
def _selftest() -> None:
    m = CostMeter("selftest")

    # 1. 计量 + per-agent 聚合
    m.record(Usage(agent_id="vera", account_id="deepseek-a",
                   session_id="s1", prompt_tokens=100, completion_tokens=50, cost=0.001))
    m.record(Usage(agent_id="vera", account_id="deepseek-a",
                   session_id="s1", prompt_tokens=200, completion_tokens=80, cost=0.002))
    m.record(Usage(agent_id="marvis", account_id="deepseek-b",
                   session_id="s2", prompt_tokens=300, completion_tokens=0, cost=0.003))
    agg = m.agent_cost("vera")
    assert agg["calls"] == 2 and agg["total_tokens"] == 430 and abs(agg["cost"] - 0.003) < 1e-9, f"per-agent 聚合失败: {agg}"
    acc = m.account_cost("deepseek-a")
    assert acc["calls"] == 2 and acc["total_tokens"] == 430, f"per-account 聚合失败: {acc}"
    print("✓ 计量 + per-agent/account 聚合")

    # 2. 会话缓存亲和：钉住 → 读取 → failover 打破
    m.bind_affinity("s1", "deepseek-a")
    assert m.affinity_for("s1") == "deepseek-a", "亲和钉住失败"
    m.break_affinity("s1")
    assert m.affinity_for("s1") is None, "亲和打破失败"
    assert m.affinity_for("s2") is None, "未钉住应返回 None"
    print("✓ 会话缓存亲和 钉住/读取/打破")

    # 3. OTLP 导出（旁路，含 agent cost + affinity state）
    otlp = m.export_otlp()
    names = {x["name"] for x in otlp}
    assert "ll.agent.cost" in names and "ll.session.affinity_bound" in names, f"OTLP 导出缺字段: {names}"
    print("✓ OTLP 导出骨架")

    # 4. fail-open：坏 record 不抛
    try:
        m.record(None)  # type: ignore[arg-type]
    except Exception:
        raise AssertionError("record 应 fail-open 不抛")
    print("✓ fail-open（坏 record 不阻断）")

    print("SELFTEST ALL GREEN")


if __name__ == "__main__":
    _selftest()
