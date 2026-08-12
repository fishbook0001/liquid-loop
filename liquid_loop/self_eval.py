"""液环自评估（蒸馏自 U-OPSD arXiv:2608.06296，落地液环自检环节）

U-OPSD 核心：无外部真值自评估——采样 G 条 rollout → 多数投票 → 最长同意 rollout
为伪解 → 沿不同意 rollout 前缀逐 token D_β 蒸馏。蒸馏到液环的「自检环节」：

  - consensus_self_check(answers, tau)：给定一组候选答案（如 Vera 对同一提议的
    多条推理路径 / 改写），算共识票比 frac + 是否达 τ 触发 + 伪解；无需外部真值，
    即给 AI 提议做一致性自检打分。vera-self-evolve 可经 8790 /selfcheck 调用。
  - diverg_beta(p, q, beta)：β-散度（β=0.5 JSD 对称 / β=1 reverse KL 非对称）。
  - u_opsd_step：原版三步骤（token 级 rollout 分布版），留给有真实策略分布的 caller。

与 vera-self-evolve v2「RSI 内化」强同构：U-OPSD 的「无外部真值自评估」正是
自进化评估器的确定性落地——不依赖外部标注即可给提议打一致性分（紧致度=可信度）。
零依赖：仅 Python 标准库（math, re, collections）。fail-open：异常由 caller 兜底。
"""
from __future__ import annotations

import math
import re
from collections import Counter

# 与 server 同源的 token 切分：中文单字 + 英文数字连续串
_TOKEN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def _tokens(s: str) -> list:
    return _TOKEN.findall(s or "")


def majority_vote(answers):
    """多数投票：返回 (伪答案, 胜出票比例)。

    对齐 U-OPSD：伪解来自多数票；frac 即共识强度（无需外部真值）。
    """
    c = Counter(answers)
    top, cnt = c.most_common(1)[0]
    return top, cnt / len(answers)


def diverg_beta(p, q, beta):
    """β-散度 D_β(p∥q)：
       β=1   → reverse KL: Σ q·log(q/p)
       β≈0   → forward KL: Σ p·log(p/q)（β→0 极限）
       β=0.5 → JSD（对称）：0.5·KL(P∥M)+0.5·KL(Q∥M), M=(P+Q)/2
    论文 Table 5：β=0.5 标为 JSD，β=1 标为 reverse KL。
    零依赖：标准库 math；p/q 为 {token: prob} 分布（缺失键按 1e-9 平滑）。
    """
    tokens = set(p) | set(q)
    P = {t: p.get(t, 1e-9) for t in tokens}
    Q = {t: q.get(t, 1e-9) for t in tokens}

    def kl(a, b):
        return sum(a[t] * math.log(a[t] / b[t]) for t in tokens)

    if beta >= 1.0:
        return kl(Q, P)  # reverse KL
    if beta <= 0.0:
        return kl(P, Q)  # forward KL
    # β=0.5 → JSD
    M = {t: 0.5 * (P[t] + Q[t]) for t in tokens}
    return 0.5 * kl(P, M) + 0.5 * kl(Q, M)


def _dist_of(s: str) -> dict:
    """字符串 → 词频分布（token 级）。空串返回 {}。"""
    toks = _tokens(s)
    if not toks:
        return {}
    n = len(toks)
    c = Counter(toks)
    return {t: v / n for t, v in c.items()}


def string_divergence(a: str, b: str, beta: float = 0.5) -> float:
    """两条答案字符串间的 token 分布散度（U-OPSD D_β 的字符串级落地）。"""
    return diverg_beta(_dist_of(a), _dist_of(b), beta)


def consensus_self_check(answers, tau: float = 0.5, beta: float = 0.5) -> dict:
    """字符串级共识自检（U-OPSD 落地版）—— 无外部真值，给提议打一致性分。

    参数：
        answers — 同一提议的多条候选答案（如多条推理路径 / 改写 / 重采样结果）
        tau     — 触发阈值（默认 0.5，对齐论文 τ=1/2）
        beta    — 散度阶（默认 0.5 JSD）
    返回 dict：
        triggered        — 多数票比 ≥ τ 且存在分歧（与 U-OPSD 触发条件同构）
        frac             — 共识票比（共识强度）
        pseudo_index     — 伪解（最长同意答案）在原列表的索引
        pseudo_answer    — 伪解内容
        mean_divergence  — 同意集内平均两两散度（越小越紧致=越可信伪解）
        n                — 候选数
    不变量：
      ① 触发条件：胜出票比 ≥ τ(=1/2) 且 至少一条答案不同意（𝒴⁻ ≠ ∅）
      ② 伪解 = 最长「同意」答案（argmax_{y∈𝒴⁺}|y|）
    """
    if not answers:
        return {"triggered": False, "frac": 0.0, "pseudo_index": -1,
                "pseudo_answer": None, "mean_divergence": 0.0, "n": 0}
    pseudo, frac = majority_vote(answers)
    Y_plus_idx = [i for i, a in enumerate(answers) if a == pseudo]
    Y_minus = [a for a in answers if a != pseudo]

    triggered = (frac >= tau) and (len(Y_minus) > 0)

    # 伪解 = 最长同意答案
    pseudo_index = max(Y_plus_idx, key=lambda i: len(answers[i]))

    # 同意集内平均两两散度（紧致度）：越低→伪解越被多条路径共同支撑
    plus = [answers[i] for i in Y_plus_idx]
    if len(plus) >= 2:
        divs = []
        for i in range(len(plus)):
            for j in range(i + 1, len(plus)):
                divs.append(string_divergence(plus[i], plus[j], beta))
        mean_div = sum(divs) / len(divs) if divs else 0.0
    else:
        mean_div = 0.0

    return {"triggered": triggered, "frac": frac, "pseudo_index": pseudo_index,
            "pseudo_answer": pseudo, "mean_divergence": mean_div, "n": len(answers)}


# ---------- 策略分布版（进阶，留给有真实 π/π̄ 的 caller）----------
def u_opsd_step(rollouts, pseudo_pick, tau: float = 0.5, beta: float = 0.5,
                teacher_dist=None, student_dist=None) -> tuple:
    """token 级 U-OPSD 核心三步（蒸馏版，分布由 caller 提供）。

    参数：
        rollouts     — 每条 rollout 为 (answer, tokens_tuple)
        pseudo_pick  — 选伪解函数：给定同意 rollouts 返回伪解 tokens（默认最长）
        teacher_dist — fn(ctx_tokens) → {token: prob}（stop-grad 副本 π̄）
        student_dist — fn(ctx_tokens) → {token: prob}（可训练 π_θ）
    返回 (loss, info)。loss 仅在触发时为正（未触发=0.0）。
    不变量对齐论文 ℒ_U-OPSD：触发→选伪解→仅对 𝒴⁻ 前缀逐 token D_β。
    """
    if teacher_dist is None or student_dist is None:
        raise ValueError("u_opsd_step 需要 teacher_dist / student_dist 策略分布函数")
    answers = [r[0] for r in rollouts]
    pseudo, frac = majority_vote(answers)
    Y_plus = [r for r, a in zip(rollouts, answers) if a == pseudo]
    Y_minus = [r for r, a in zip(rollouts, answers) if a != pseudo]

    triggered = (frac >= tau) and (len(Y_minus) > 0)
    if not triggered:
        return 0.0, {"triggered": False, "frac": frac, "n_minus": len(Y_minus),
                     "pseudo": pseudo, "loss": 0.0}

    y_plus = pseudo_pick(Y_plus) if pseudo_pick else max(Y_plus, key=lambda r: len(r[1]))[1]

    total = 0.0
    n_terms = 0
    for (_, y_minus_toks) in Y_minus:
        for n in range(1, len(y_minus_toks)):
            teacher_ctx = list(y_plus) + list(y_minus_toks[:n])
            student_ctx = list(y_minus_toks[:n])
            p_t = teacher_dist(teacher_ctx)   # π̄(·|x, y⁺, y⁻<n)
            p_s = student_dist(student_ctx)   # π_θ(·|x, y⁻<n)
            total += diverg_beta(p_t, p_s, beta)
            n_terms += 1

    loss = total / n_terms if n_terms else 0.0
    return loss, {"triggered": True, "frac": frac, "pseudo": pseudo,
                  "n_minus": len(Y_minus), "n_terms": n_terms, "loss": loss}
