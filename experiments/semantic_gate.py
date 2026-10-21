#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""semantic_gate — 结晶语义 gate（advisory·fail-open·轻量旁路）

在液环现有阈值规则（jaccard/keyword match_score≥0.35）之上叠加
AgentJev 语义二阶判断，输出 duplicate/conflict/distinct 三值裁决建议。
调用方（旁路审计/未来编排层接线）必须把 None 与 verdict=None 视为
「模型弃权 → 走原规则」，绝不阻断主路径（proprioception 同范式）。
"""
import os
import sys

_distill_dir = os.path.expanduser("~/output/agentjev_distill")
if _distill_dir not in sys.path:
    sys.path.insert(0, _distill_dir)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 2026-09-26 起改用蒸馏原语 system1_decision_gate（registry 双写落库，wire-gate 过门）；
# decision_advisor 保留供 poc_decision_advisor.py 历史脚本使用。
from system1_decision_gate import System1Gate as AgentJevAdvisor  # noqa: E402

__all__ = ["crystallize_gate", "governance_risk_gate", "get_advisor"]

_adv = None
# 裁决阈值：≥0.75 才出 duplicate/conflict（PoC 分布较尖，宁缺勿滥）
GATE_THRESHOLD = 0.75

# VAPO Clip-Higher 同构（v329_vapo_clip_higher · 军师调研v329）：
# 不可逆动作（merge/坍缩）用更紧双高阈值，可逆动作（keep/保留）走原判据。
# 调用方在「合并证据」语义下传 action_risk="merge" 即启用；
# env LIQUID_COLLAPSE_CLIPHIGHER=0 可强制回退原生判据（运维逃生口）。
_CLIPHIGH_SAME = float(os.environ.get("LIQUID_COLLAPSE_EPS_HIGH_SAME", "0.75"))
_CLIPHIGH_MATCH = float(os.environ.get("LIQUID_COLLAPSE_EPS_HIGH_MATCH", "0.70"))


def get_advisor(timeout=6.0):
    global _adv
    if _adv is None:
        _adv = AgentJevAdvisor(timeout=timeout)
    return _adv


def crystallize_gate(new_text, match_text, match_score=None, timeout=6.0,
                     action_risk="keep"):
    """坍缩质量裁决（advisory·fail-open）——病理性坍缩（错误聚合丢细节）防线。

    c≥1 实测校准（2026-09-26，隔离端到端真实分布）：
    - 结构分（compute_match_score）对「同义复述」与「近义异事实」**不可分**：
      8790vs8791 病理对 0.55 ≈ 真复述对 0.564——结构分不能当主判。
    - 语义分（same_prob）可分：真复述 0.74 ≥0.5 vs 病理对 0.465 <0.5。
    - 故判据：**语义为主 + 结构挡极强**——
      prevent = (same_prob < 0.5) and (match_score < 0.65)；
      match_score ≥ 0.65（token 近全重叠，如 60s 去重类）→ 结构证据足够，放行；
      match_score=None（独立调用/初步结晶）→ 仅语义附注，不裁决 prevent。

    state 格式与 question 措辞已锁定为 vera-agentjev dedup 实测最佳范式
    （[A]/[B] + 英文 redundant），勿改。

    A6 · VAPO Clip-Higher 同构（v329_vapo_clip_higher）：当 action_risk="merge"
    （调用方语境=新证据要不可逆合入已有记忆）时，启用双高阈值——
    merge_allowed = (same_prob >= _CLIPHIGH_SAME) and (match_score >= _CLIPHIGH_MATCH)；
    仅当两者皆满足才放行合并，否则阻止（保守保护细节）。keep/独立调用
    （action_risk 默认 "keep"）保持原生判据。env LIQUID_COLLAPSE_CLIPHIGHER=0 可全局回退。
    """
    adv = get_advisor(timeout=timeout)
    clip_higher = False
    state = (f"[A] {new_text}\n"
             f"[B] {match_text}")
    same = adv.boolean(
        state,
        "Are A and B semantically redundant (same point restated)?")
    if same is None:
        return None
    p = same.get("prob_true", 0.0)
    if match_score is not None:
        merge_mode = (action_risk == "merge")
        # VAPO Clip-Higher 同构（v329_vapo_clip_higher）：不可逆 merge 用更紧双高阈值；
        # 因已锁定的判据铁律，仅在 merge 语义且未被 env=0 显式关闭时启用；
        # keep/独立调用保持原生判据（fail-safe）。
        use_cliphigh = merge_mode and (os.environ.get("LIQUID_COLLAPSE_CLIPHIGHER", "1") != "0")
        if use_cliphigh:
            # 不可逆合并：须「双高」同时满足（更紧），否则阻止（保守保护细节）
            merge_allowed = (p >= _CLIPHIGH_SAME) and (match_score >= _CLIPHIGH_MATCH)
            prevent = not merge_allowed
            verdict = "same" if merge_allowed else "distinct"
            quality = "safe" if merge_allowed else "dangerous"
        else:
            # 语义为主（same_prob≥0.5 = 模型确认冗余 → same）；
            # 结构仅挡极强匹配（≥0.65，token 近全重叠，防模型漏判误拦）
            semantic_dup = p >= 0.5
            structurally_certain = match_score >= 0.65
            prevent = bool(not semantic_dup and not structurally_certain)
            if prevent:
                verdict, quality = "distinct", "dangerous"
            elif semantic_dup:
                verdict, quality = "same", "safe"
            else:
                verdict, quality = "ambiguous", "ambiguous"
        clip_higher = use_cliphigh
    else:
        # 无结构分：仅语义附注，保守不裁决
        if p >= 0.5:
            verdict, quality = "same", "safe"
        elif p < 0.35:
            verdict, quality = "distinct", "dangerous"
        else:
            verdict, quality = "ambiguous", "ambiguous"
        prevent = False
    return {"verdict": verdict,
            "same_prob": round(p, 4),
            "match_score": match_score,
            "prevent_collapse": prevent,
            "collapse_quality": quality,
            "clip_higher": clip_higher,
            "wall_ms": same.get("wall_ms") or 0}


def governance_risk_gate(memory_content, timeout=6.0):
    """删除/溶解风险 advisory：判断「删除该记忆是否会造成不可逆的唯一信息损失」。

    编排层接线（governance 主路径）应传 timeout=2.0；独立审计脚本可用默认。
    返回 {"verdict": "safe"|"risky", "risky_prob": float, "wall_ms": float}
    或 None（服务不可达/解析失败 = 模型弃权，调用方走原规则，绝不阻断）。
    """
    adv = get_advisor(timeout=timeout)
    a = adv.boolean(
        f"[MEMORY-CONTENT] {memory_content}",
        "Does this memory contain unique, non-redundant information that would be "
        "irreversibly lost if deleted, with no other record covering the same point?")
    if a is None:
        return None
    return {"verdict": "risky" if a["prob_true"] > 0.5 else "safe",
            "risky_prob": a["prob_true"],
            "wall_ms": a.get("wall_ms") or 0}


if __name__ == "__main__":
    g = crystallize_gate(
        "2026-09-26 液环 8790 /health 实测 9ms 往返，服务正常。",
        "2026-09-25 液环 8790 /health 实测 ok，服务正常。")
    print("smoke:", g)
