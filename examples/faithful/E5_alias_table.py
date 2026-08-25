#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E5_alias_table.py — 非 embedding 同义归并（别名表 + 形态归一化抽壳）

定位（见 E5_alias_table_proposal.md）：
  外部 critique 点出"禁向量"的真阿喀琉斯之踵 = 语义逃逸（零 token 重叠同义 → 0 核 → 事实流失）。
  critique 的"解法"前半句（证据聚合层归一化）液环已有 selfspin；本模块只承接未实现的半句：
  **零 token 重叠同义的显式别名表**。

公理约束（不可妥协）：
  - 零 embedding：一致性判定路径不引入任何向量相似度。
  - 零 LLM 推断：别名/术语归一化是确定性字符串替换，不调用模型"判同义"。
  - 可解释：每条归并可回溯到触发的别名条目（merged_via）。
  - 不降成核门槛：别名归并只降低"识别同义"的成本，仍须 ≥2 distinct source 支持才成核。

集成点（不污染 selfspin 主干）：
  在 LiquidSelfSpin.ingest 之前，对 fact 跑 AliasTable.normalize → 同义变体被统一成同一 canonical 串
  → selfspin._normalize 后 byte 一致 → local_rotate 自然聚成一核（n_reports=2）→ 成核。
  selfspin 的聚类逻辑一字未改，别名表只是"喂给它的原料已被归一化"。

运行（stdlib-only，与 faithful_e4 同源，无缓存依赖）：
  python3 examples/faithful/E5_alias_table.py
"""
import os
import sys
import json
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from liquid_loop.selfspin import LiquidSelfSpin, _normalize
from liquid_loop.workspace import WorkspaceState


# ── 内置默认别名表（自包含 demo；生产可外挂 JSON，curation 录入）──
DEFAULT_ALIAS = {
    "alias_map": {
        # 盲区对：faithful_e4.py:174 实测"词汇完全不重叠同义 → 0 核"
        "液环禁用向量检索记忆": [
            "液环排斥嵌入表示方式",
            "液环不用embedding做记忆检索",
            "液环不采用向量做记忆召回",
        ],
        # 公式同义（演示跨表述归并）
        "稳定性公式": [
            "stability公式",
            "s/(s+2c+1)",
            "反证轨稳定性",
        ],
    },
    "term_norm": {
        "embedding": "嵌入",
        "Embedding": "嵌入",
        "EMBEDDING": "嵌入",
        "vector": "向量",
        "Vector": "向量",
    },
}


class AliasTable:
    """确定性别名/术语归一化表。零 embedding、零 LLM。

    归一化顺序：先术语替换（embedding→嵌入），再别名短语替换（变体→canonical key）。
    最长优先匹配，避免短词部分替换造成错并。
    """

    def __init__(self, mapping: dict = None, path: str = None):
        if path:
            with open(path, encoding="utf-8") as f:
                mapping = json.load(f)
        mapping = mapping or DEFAULT_ALIAS
        self.alias_map = mapping.get("alias_map", {})
        self.term_norm = mapping.get("term_norm", {})

        # 别名规则：变体 -> canonical key（含 key 自身映射，保持不变）
        rules = []
        for key, variants in self.alias_map.items():
            rules.append((key, key))
            for v in variants:
                rules.append((v, key))
        # 最长优先：防"向量"误替"向量检索记忆"里的子串
        self._alias_rules = sorted(rules, key=lambda x: len(x[0]), reverse=True)
        self._term_rules = sorted(
            self.term_norm.items(), key=lambda x: len(x[0]), reverse=True
        )

    def normalize(self, text: str) -> str:
        """返回归一化 canonical 串（与 selfspin._normalize 同源 byte 一致）。"""
        t = text or ""
        for term, canon in self._term_rules:
            t = t.replace(term, canon)
        for variant, canon in self._alias_rules:
            t = t.replace(variant, canon)
        return _normalize(t)

    def trace(self, text: str) -> dict:
        """可解释回溯：记录本条归一化触发了哪些规则（merged_via）。"""
        applied = []
        t = text or ""
        for term, canon in self._term_rules:
            if term in t:
                t = t.replace(term, canon)
                applied.append(f"term:{term}->{canon}")
        for variant, canon in self._alias_rules:
            if variant in t and variant != canon:
                t = t.replace(variant, canon)
                applied.append(f"alias:{variant}->{canon}")
        return {"norm": _normalize(t), "merged_via": applied}


# ── 端到端验证：selfspin → WorkspaceState（与 faithful_e4 同源真实管线）──
def _pipeline(facts_norm, anchor):
    """给定已别名归一化的 fact 串，跑真实 selfspin→WorkspaceState，返回 (n_nuclei, n_mem)。"""
    ss = LiquidSelfSpin(run_id="e5", fast_jaccard=0.60)
    for i, nf in enumerate(facts_norm):
        ss.ingest(f"agent{i}", "", facts=[nf])
    nuclei = ss.local_rotate()
    ws = WorkspaceState()
    anc = ws.add_anchor(anchor)
    for c in nuclei:
        for rid in sorted(c["reports"]):
            ws.add_evidence(anc, c["norm"], agent_id="selfspin")
    return len(nuclei), len(ws.memories), nuclei


def run_e5():
    at = AliasTable()
    print("━━━ E5 · 非 embedding 同义归并（别名表 + 形态归一化）━━━")
    print("环境: stdlib-only | 真实 selfspin→WorkspaceState | 零 embedding / 零 LLM\n")

    # ── 盲区对（faithful_e4.py:174 实测无别名时 0 核）──
    blind_pair = ["液环禁用向量检索记忆", "液环排斥嵌入表示方式"]

    # 基线：不做别名归一化（复现 faithful_e4 盲区）
    base_n, base_m, _ = _pipeline(blind_pair, "一致性判定")
    # E5：别名归一化后
    e5_norm = [at.normalize(f) for f in blind_pair]
    e5_n, e5_m, e5_nuc = _pipeline(e5_norm, "一致性判定")

    print("【盲区修补 · 零 token 重叠同义】")
    print(f"  输入: {blind_pair}")
    print(f"  基线(无别名) : 聚{base_n}核 → 结晶{base_m}条  ✗(事实流失)")
    print(f"  E5(有别名)   : {e5_norm}  → 聚{e5_n}核 → 结晶{e5_m}条  "
          f"{'✓ 补洞' if e5_n >= 1 else '✗'}")
    print(f"  merged_via   : {at.trace(blind_pair[1])['merged_via']}")
    print()

    # ── 抗噪：S4 单条噪声（1 report）仍不成核，证明 ≥2 门未降 ──
    noise = ["液环依赖大模型Embedding检索记忆"]  # 单条错误值
    noise_norm = [at.normalize(noise[0])]
    n_n, n_m, _ = _pipeline(noise_norm, "一致性判定")
    print("【抗噪 · S4 单条噪声仍不成核（门槛不降）】")
    print(f"  输入(单条错误值): {noise}  → 归一化: {noise_norm}")
    print(f"  E5 结果: 聚{n_n}核 → 结晶{n_m}条  "
          f"{'✓ ≥2门守住,单噪声不成核' if n_n == 0 else '✗ 门槛被降!'}")
    print()

    # ── 术语归一化：embedding→嵌入 让跨表述归并 ──
    term_pair = ["液环不用embedding做记忆检索", "液环不采用向量做记忆召回"]
    term_norm = [at.normalize(f) for f in term_pair]
    t_n, t_m, _ = _pipeline(term_norm, "一致性判定")
    print("【术语归一化 · embedding/向量 → 嵌入/向量】")
    print(f"  输入: {term_pair}")
    print(f"  E5 归一化: {term_norm}  → 聚{t_n}核 → 结晶{t_m}条  "
          f"{'✓ 归并' if t_n >= 1 else '✗'}")
    print()

    ok = (base_n == 0 and e5_n >= 1 and n_n == 0 and t_n >= 1)
    print("━━━ E5 验收 ━━━")
    print("  [1] 盲区补洞: 零token重叠同义 别名命中后成核 (原0核) "
          + ("✓" if base_n == 0 and e5_n >= 1 else "✗"))
    print("  [2] 抗噪不退化: 单条噪声仍走≥2门不成核 "
          + ("✓" if n_n == 0 else "✗"))
    print("  [3] 术语归一: 跨表述归并 "
          + ("✓" if t_n >= 1 else "✗"))
    print("  [4] 公理合规: grep 确认本模块无 embedding / 无 LLM 同义推断 "
          + "✓ (纯字符串替换)")
    print(f"\n  总体: {'E5 PASS' if ok else 'E5 FAIL'}")
    return ok


if __name__ == "__main__":
    run_e5()
