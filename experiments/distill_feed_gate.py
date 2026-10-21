#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""distill_feed_gate — 第四钩：蒸馏投喂守卫门（advisory·fail-open·轻量旁路）

针对穿透式打分 D5 揭示的「单文件守卫假阴性 / 低质蒸馏污染 registry」缺口：
在液环投喂入口（insight_write_side.feeder_run_once）调用本钩，对每条待投喂
蒸馏候选做 AgentJev 二阶评估——是否语义重复、是否值得接运行时。

铁律（与三钩同范式）：
  1. advisory-only：返回值只作「建议」，绝不阻断投喂主路径。
  2. fail-open：env LIQUID_FEED_GATE==0 或 服务不可达/超时/解析失败 → 返回 None，
     调用方必须把 None 视为「模型弃权，照常投喂」。
  3. 回环禁代理：System1Gate 内部已 ProxyHandler({})。

接线（2026-09-26 飞哥「全量执行」授权）：
  insight_write_side.feeder_run_once 投喂前调用 feed_gate(state)，
  结果附 advisory_log.jsonl（source=feed_gate），不写回、不阻断。

2026-09-26 实测：AgentJev 对 >1200 字事实 path 易失真（见穿透报告），
故本钩 state 强制 ≤400 字 + 单命题。
"""
import os
import sys
import json
from datetime import datetime as _dt

_EXP = os.path.expanduser("~/liquid-loop/experiments")
if _EXP not in sys.path:
    sys.path.insert(0, _EXP)
sys.path.insert(0, os.path.expanduser("~/output/agentjev_distill"))
from system1_decision_gate import System1Gate  # noqa: E402

__all__ = ["feed_gate", "FEED_GATE_LOG"]

FEED_GATE_LOG = os.path.expanduser("~/liquid-loop/experiments/advisory_log.jsonl")
_GATE = None
_DUP_THRESHOLD = 0.55   # 语义重复判定阈值（投喂场景宁宽勿滥，仅提示）
_WORTH_THRESHOLD = 0.40  # 值得接活阈值


def _gate():
    global _GATE
    if _GATE is None:
        _GATE = System1Gate(timeout=3.0)
    return _GATE


def _log(entry: dict):
    try:
        with open(FEED_GATE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def feed_gate(candidate_id: str, content_excerpt: str,
              existing_hint: str = "", timeout: float = 3.0) -> dict:
    """对一条蒸馏候选做投喂前守卫评估。

    candidate_id : 条目 id（用于日志关联）
    content_excerpt : 候选内容摘要（建议 ≤400 字）
    existing_hint : 既有同域条目提示（构造 boolean 重复判定的 B 方）

    返回 {"dup_prob","worth_prob","verdict","wall_ms"} 或 None（fail-open）。
    verdict in {duplicate, low_value, ok}。
    """
    if os.environ.get("LIQUID_FEED_GATE", "1") == "0":
        return None
    excerpt = content_excerpt[:400]
    state = ("液环蒸馏候选[%s]: %s" % (candidate_id, excerpt))
    if existing_hint:
        state += " | 既有同域: " + existing_hint[:200]
    try:
        g = _gate()
        dup = g.boolean(state, "这条蒸馏候选与既有同域条目是否语义重复(同一机制被重复登记)?",
                        qid="dup")
        worth = g.boolean(state, "这条蒸馏候选是否值得接入液环运行时(有可落地原语/机制价值)?",
                          qid="worth")
        if dup is None or worth is None:
            return None
        dup_p = float(dup.get("probability", 0.0))
        worth_p = float(worth.get("probability", 0.0))
        # 穿透式打分(2026-09-26)实证: AgentJev 对陈述/worth 命题 P(true) 恒偏低(偏置),
        # 故 worth 维度不据以告警(避免每条噪音); 第四钩仅对"语义重复"敏感——这正是
        # D5 守卫缺口核心(防重复/低质蒸馏污染 registry)。dup 维度为比较命题, 模型可靠。
        if dup_p >= _DUP_THRESHOLD:
            verdict = "duplicate"
        else:
            verdict = "ok"
        res = {"dup_prob": round(dup_p, 3), "worth_prob": round(worth_p, 3),
               "verdict": verdict, "wall_ms": dup.get("wall_ms")}
        _log({"ts": _dt.now().isoformat(timespec="seconds"),
              "source": "feed_gate", "candidate_id": candidate_id,
              "verdict": verdict, **res})
        return res
    except Exception:
        return None


if __name__ == "__main__":
    # 自测：构造一条明显重复 + 一条高价值
    print("dup test:", feed_gate("test_dup",
          "液环结晶机制: 同agent≥2证据成核，consensus≥2不同agent",
          existing_hint="既有: 液环双轨成核 private同agent≥2 consensus≥2不同agent"))
    print("ok test:", feed_gate("test_ok",
          "AgentJev System-1 决策门: 三原语 boolean/choice/score 接液环三钩"))
