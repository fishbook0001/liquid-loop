"""长会话/长上下文提取式压缩（蒸馏自 Octomind condense.rs，落地液环）

把外部 runtime 的「提取式上下文压缩」蒸馏为液环一等公民组件：
  - 超阈值触发 → 选行保留（非生成式摘要）
  - 单条 < MIN_CANDIDATE_TOKENS 绝不触碰
  - 压后 ≥ 压前则保留原文（no-gain 保护）
  - 任何异常保留原文（fail-open）

契合液环哲学：零向量、确定性、零丢失。
压缩仅裁剪噪声行，原文语义结构（决策/错误/文件路径/代码/结论）保留；
底层 storage 完整存档，「蒸馏」与「归档」互不干扰。

触发阈值：环境变量 LIQUID_CONTEXT_COMPRESS_TOKENS（默认 0 = 不压缩），
与 LIQUID_EVIDENCE_BUDGET 运维风格一致。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


def estimate_tokens(text: str) -> int:
    """字符数 / 4 启发式（对预算触发判断足够精确，零依赖 tiktoken）。"""
    if not text:
        return 0
    return max(1, len(text) // 4)


# 高价值行信号：保留含错误/决策/文件路径/代码定义/符号的行
_HIGH_VALUE = re.compile(
    r"(?i)("
    r"\b(error|failed|exception|traceback|panic)\b"  # 错误/异常
    r"|\b(decision|changed|fixed|result|todo|fix|note)\b"  # 决策/结论
    r"|[\w./\-]+\.(py|rs|ts|js|go|toml|json|md|yaml|yml)"  # 文件路径
    r"|^\s*(def|fn|func|class|impl|pub|import|export)\b"  # 代码定义
    r"|=>|==|!=|::|->"  # 代码符号
    r")"
)
# 噪声行信号：空/纯符号行、闲聊开场
_NOISE = re.compile(
    r"(?i)^[\s>*\-–·•]*$"
    r"|^(thinking|let me|i'll|sure|okay|great|here's)\b"
)


@dataclass
class CondenseReport:
    """压缩运行报告（对齐原版统计字段）。"""

    triggered: bool = False
    candidates: int = 0
    condensed: int = 0
    untouched: int = 0
    saved_tokens: int = 0
    notes: list = field(default_factory=list)


class ExtractiveCondenser:
    """超阈值触发 → 选行保留（非生成式摘要）→ fail-open 不崩。

    与原版 condense_round 不变量对齐：
      - 整轮 token 总和 > threshold 才触发（防小结果免费注入大轮）
      - 单条 < MIN_CANDIDATE_TOKENS 绝不触碰
      - 压后 ≥ 压前则保留原文 (no-gain)
      - 任何异常返回原样 (fail-open)
    """

    MIN_CANDIDATE_TOKENS = 512
    KEEP_RATIO = 0.5  # 候选结果最多保留的行比例上限

    def __init__(self, tokens_threshold: int = 0):
        # 0 = 不压缩（缺省关闭，与 LIQUID_EVIDENCE_BUDGET 风格一致）
        self.tokens_threshold = tokens_threshold

    def _score_line(self, line: str) -> float:
        """启发式重要性打分（替代原版 cheap-LLM 行选择，零 LLM 依赖）。"""
        s = 0.0
        if _HIGH_VALUE.search(line):
            s += 3.0
        if _NOISE.search(line):
            s -= 2.0
        stripped = line.strip()
        if len(stripped) >= 20:
            s += 1.0
        # 近似重复行惩罚
        if len(stripped) > 40 and stripped.count(stripped[:10]) > 2:
            s -= 1.5
        return s

    def condense_round(self, results: list) -> tuple:
        """results: list of dict {"tool": str, "content": str}

        返回 (压缩后 results, CondenseReport)。fail-open 时 triggered=False。
        """
        rep = CondenseReport()
        try:
            sizes = [estimate_tokens(r.get("content", "")) for r in results]
            if sum(sizes) <= self.tokens_threshold:
                return results, rep  # 未触发
            rep.triggered = True

            candidates = [i for i, t in enumerate(sizes)
                          if t >= self.MIN_CANDIDATE_TOKENS]
            rep.candidates = len(candidates)
            if not candidates:
                return results, rep

            out = list(results)
            for idx in candidates:
                r = results[idx]
                original = r.get("content", "")
                before = estimate_tokens(original)
                lines = original.splitlines()
                if len(lines) <= 2:
                    rep.untouched += 1
                    continue
                scored = [(self._score_line(ln), i, ln) for i, ln in enumerate(lines)]
                scored.sort(key=lambda x: x[0], reverse=True)
                keep_n = max(2, int(len(lines) * self.KEEP_RATIO))
                keep_idx = sorted(i for _, i, _ in scored[:keep_n])
                new_content = "\n".join(lines[i] for i in keep_idx)
                after = estimate_tokens(new_content)
                if after >= before:  # no-gain 保护
                    rep.untouched += 1
                    continue
                out[idx] = {"tool": r.get("tool", ""), "content": new_content}
                rep.condensed += 1
                rep.saved_tokens += (before - after)
            if rep.condensed:
                rep.notes.append("📎 CONDENSED by supervisor")
            return out, rep
        except Exception as e:
            rep.notes.append(f"fail-open: {e}")
            return results, rep


def compress_context(
    texts: list,
    threshold: Optional[int] = None,
) -> tuple:
    """液环便捷入口：把多条证据/上下文作为一轮压缩。

    texts: 待压缩文本列表（如 recall 返回的多条证据 content）
    threshold: 触发阈值 token；None 时读 LIQUID_CONTEXT_COMPRESS_TOKENS（默认 0=不压）
    返回 (压缩后文本列表, CondenseReport)
    """
    if threshold is None:
        threshold = int(os.environ.get("LIQUID_CONTEXT_COMPRESS_TOKENS", "0"))
    if threshold <= 0:
        return list(texts), CondenseReport()  # 未启用
    results = [{"tool": "", "content": t} for t in texts]
    out, rep = ExtractiveCondenser(threshold).condense_round(results)
    return [r["content"] for r in out], rep


# ---------------------------------------------------------------------------
# RE-TRAC 同构：结构化三组分笔记（提取式分桶，非生成式）
# 把压缩后的证据/上下文按语义分桶为 {answer, evidence, open}，
# 对应 RE-TRAC 的「当前最优答案 + 证据库 + 不确定项/待探索」。
# 零 LLM 依赖、fail-open，契合液环「提取式、确定性、零丢失」哲学。
# ---------------------------------------------------------------------------
_BUCKET_ANSWER = re.compile(
    r"(?i)("
    r"\b(result|fixed|changed|decision|conclusion|answer|resolved|done)\b"
    r"|结论|已修复|决定|已解决"
    r")"
)
_BUCKET_OPEN = re.compile(
    r"(?i)("
    r"\b(failed|exception|error|uncertain|unknown|pending|todo|wip)\b"
    r"|待探索|待定|不确定|失败|待办|未解决"
    r")"
)
_BUCKET_EVIDENCE = re.compile(
    r"(?i)("
    r"[\w./\-]+\.(py|rs|ts|js|go|toml|json|md|yaml|yml)"  # 文件路径
    r"|=>|==|!=|::|->"  # 代码符号
    r"|\b(path|evidence|data|source|依据|来源|数据)\b"
    r")"
)


def structured_note(
    texts: list,
    threshold: Optional[int] = None,
) -> dict:
    """RE-TRAC 同构：把多条证据/上下文压成结构化三组分笔记。

    返回 {answer, evidence, open} 三个文本列表。分桶为提取式
    （按行语义正则归类），不生成新内容，fail-open（异常返回空桶）。

    - answer   : 当前最优结论 / 已修复 / 决策
    - evidence : 路径 / 符号 / 数据 / 来源等支撑性内容（保底桶，不丢行）
    - open     : 失败 / 异常 / 不确定 / 待探索项
    """
    try:
        compressed, _ = compress_context(texts, threshold)
        buckets = {"answer": [], "evidence": [], "open": []}
        for t in compressed:
            for line in t.splitlines():
                s = line.strip()
                if not s:
                    continue
                if _BUCKET_OPEN.search(s):
                    buckets["open"].append(s)
                elif _BUCKET_ANSWER.search(s):
                    buckets["answer"].append(s)
                else:
                    buckets["evidence"].append(s)  # 保底不丢
        return buckets
    except Exception:
        return {"answer": [], "evidence": [], "open": []}
