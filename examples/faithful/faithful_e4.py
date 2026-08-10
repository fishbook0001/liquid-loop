#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""faithful_e4.py — 真实液环管线验证 (selfspin → WorkspaceState) vs 朴素向量基线

环境前提（无污染）：
  - 运行于 examples/faithful/.venv (managed python 3.13, stdlib-only, 无缓存依赖)
  - 输入确定性、无随机；不依赖任何网络 / 8790 后端
    （selfspin 仅用本地快自转 local_rotate；WorkspaceState 纯内存跑）
  - 真实模块：liquid_loop.selfspin.LiquidSelfSpin（真实聚类）
              liquid_loop.workspace.WorkspaceState（真实双轨成核 + 反证轨 + step 动力学）

与 toy E4 的根本区别：
  - toy E4 的液环侧只跑了抽出的最小内核 LiquidCore，selfspin 这层根本没被触发，
    等于没验证真实管线。本实验 selfspin.local_rotate 真实聚类同义改写，
    再把聚类后的 canonical 按报告数逐条喂给真实 WorkspaceState 成核。

诚实边界（不夸大）：
  - 向量基线 = 朴素 char-hash + cosine，刻意不做改写聚类，用来隔离 selfspin 的贡献。
  - 生产级向量系统若自带聚类 / LLM 抽取会是更强基线；本实验不宣称"全面优于向量"，
    只展示"真实液环在改写 + 噪声输入下的端到端行为"，以及 selfspin 在其中的作用。

本次新增：盲区场景（run_blindspot）
  - 实证基础（本次会话 probe2 实测，managed py3.13 + 真实 LiquidSelfSpin）：
      字符重叠同义（禁用↔禁止↔使用） : 2条 → 1核 ✓
      纯语序改写                     : 2条 → 1核 ✓
      词汇不重叠同义(禁用向量检索记忆 ↔ 排斥嵌入表示方式): 2条 → 0核 ✗ 盲区
  - 结论：selfspin 是「字符重叠」聚类，非语义聚类。表层/字符共享改写能合并；
    语义等价但用词不重叠的陈述不会被合并——这是无向量方案的真盲区，
    其后果在液环管线里表现为：两个 agent 在"含义上一致"却因用词不重叠而无法成核 → 事实流失。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from collections import defaultdict
import hashlib

from liquid_loop.selfspin import LiquidSelfSpin
from liquid_loop.workspace import WorkspaceState

# ── 确定性向量基线（char feature-hashing，zero-LLM，可复现）──
DIM = 256


def embed(text, dim=DIM):
    v = [0.0] * dim
    for ch in text:
        if ch.strip() == "":
            continue
        h = int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16)
        v[h % dim] += 1.0
    norm = sum(x * x for x in v) ** 0.5
    if norm > 0:
        v = [x / norm for x in v]
    return v


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def vector_retrieve(query, statements, values):
    qv = embed(query)
    best_i, best_s = 0, -1.0
    for i, s in enumerate(statements):
        sc = cosine(qv, embed(s))
        if sc > best_s:
            best_s, best_i = sc, i
    return values[best_i], round(best_s, 3)


# ── 确定性输入：每个 slot 一个真值(5 个同义改写源) + 1 个噪声(不同值关键词) ──
# 真值改写共享核心词(液环/向量/一致性/判定 等) → selfspin 能聚成 1 个核(n_reports=5)
# 噪声用不同值关键词(大模型Embedding/外部LLM写入) → 与真值不聚，n_reports=1 被成核门槛过滤
SLOTS = [
    {
        "anchor": "一致性判定方式",
        "true_value": "禁用向量",
        "true_facts": [
            "液环禁止使用向量做一致性判定",
            "液环的硬约束是禁用向量做一致性判定",
            "液环一致性判定明确禁用向量",
            "在液环里向量是被禁用于一致性判定的",
            "液环规定一致性判定不得采用向量",
        ],
        "false_fact": "液环依赖大模型Embedding检索记忆",  # 不同值关键词 → 不聚
        "query_benign": "液环一致性判定用什么",
    },
    {
        "anchor": "记忆演化机制",
        "true_value": "自我调节演化",
        "true_facts": [
            "液环的记忆是自我调节的演化状态",
            "液环把记忆作为自我调节的演化机制",
            "液环记忆状态走自我调节的演化路径",
            "液环的记忆以自我调节方式演化",
            "液环用自我调节驱动记忆演化",
        ],
        "false_fact": "液环记忆由外部LLM直接写入管理",  # 不同值关键词 → 不聚
        "query_benign": "液环记忆如何演化",
    },
]


def run_slot(slot):
    """返回该 slot 在两条管线下的行为与判定。"""
    anchor = slot["anchor"]
    true_val = slot["true_value"]
    false_fact = slot["false_fact"]

    # ── 真实管线：selfspin 聚类 → WorkspaceState 成核 ──
    ss = LiquidSelfSpin(run_id=f"faithful_{anchor}", fast_jaccard=0.60)
    reports = {f"src{i}": [f] for i, f in enumerate(slot["true_facts"])}
    reports[f"src{len(slot['true_facts'])}"] = [false_fact]  # 噪声源
    for rid, fs in reports.items():
        ss.ingest(rid, "", facts=fs)
    nuclei = ss.local_rotate()

    ws = WorkspaceState()
    anc = ws.add_anchor(anchor)
    for c in nuclei:
        for rid in sorted(c["reports"]):  # 每个支持报告写一次 → 触发 private 成核
            ws.add_evidence(anc, c["norm"], agent_id="selfspin")

    # 液环的"答案" = 该锚点下已结晶记忆（噪声 n_reports=1 不会成核 → 不在其中）
    liquid_mems = [m for m in ws.memories if m.content in {c["norm"] for c in nuclei}]
    liquid_contents = [m.content for m in liquid_mems]
    liquid_correct = any(true_val[:2] in c or true_val in c for c in liquid_contents)
    liquid_stability = max((m.stability for m in liquid_mems), default=0.0)
    false_crystallized = any(false_fact.replace(" ", "") in c for c in liquid_contents)

    # ── 向量基线：朴素 char-hash + cosine，无聚类 ──
    statements = slot["true_facts"] + [false_fact]
    values = [true_val] * len(slot["true_facts"]) + ["噪声值"]
    vec_benign_val, vec_benign_score = vector_retrieve(slot["query_benign"], statements, values)
    vec_adv_val, vec_adv_score = vector_retrieve(false_fact, statements, values)  # 对抗式查询=噪声原句

    return {
        "anchor": anchor,
        "selfspin_nuclei": [(c["canonical"], c["n_reports"]) for c in nuclei],
        "liquid_memories": liquid_contents,
        "liquid_correct": liquid_correct,
        "liquid_stability": liquid_stability,
        "false_crystallized": false_crystallized,
        "vec_benign_val": vec_benign_val,
        "vec_benign_correct": vec_benign_val == true_val,
        "vec_adv_val": vec_adv_val,
        "vec_adv_correct": vec_adv_val == true_val,  # 对抗查询下向量易翻车
    }


# ── 盲区场景：selfspin 的字符重叠聚类边界（真实管线后果）──
# 每个 case 给两个 agent 各一句"含义相同"的话，喂真实 selfspin→WorkspaceState，
# 看是否最终结晶成记忆。
BLINDSPOT_CASES = [
    {
        "name": "字符重叠同义(应聚·成核)",
        "anchor": "一致性判定",
        "facts": ["液环禁用向量做一致性判定", "液环禁止使用embedding做相似度匹配"],
        "expect_merge": True,   # 共享 液环/禁/用 → 聚 1 核 → 2 报告 → 成核
    },
    {
        "name": "纯语序改写(应聚·成核)",
        "anchor": "一致性判定",
        "facts": ["液环一致性判定禁用向量", "液环判定一致时禁用向量"],
        "expect_merge": True,   # 共享字符多 → 聚 1 核 → 成核
    },
    {
        "name": "词汇不重叠同义(盲区·不聚)",
        "anchor": "一致性判定",
        "facts": ["液环禁用向量检索记忆", "液环排斥嵌入表示方式"],
        "expect_merge": False,  # 仅共享"液环"2字 <4 → 0 核 → 两个 singleton 被丢弃 → 事实流失
    },
]


def run_blindspot():
    """演示 selfspin 盲区的真实管线后果：语义一致但用词不重叠 → 无法成核。"""
    rows = []
    for case in BLINDSPOT_CASES:
        ss = LiquidSelfSpin(run_id=f"bs_{case['name']}", fast_jaccard=0.60)
        for i, f in enumerate(case["facts"]):
            ss.ingest(f"agent{i}", "", facts=[f])
        nuclei = ss.local_rotate()

        ws = WorkspaceState()
        anc = ws.add_anchor(case["anchor"])
        for c in nuclei:
            for rid in sorted(c["reports"]):
                ws.add_evidence(anc, c["norm"], agent_id="selfspin")

        n_nuclei = len(nuclei)
        n_mem = len(ws.memories)
        # 判定：应聚案例期望成核≥1；盲区案例期望 0 核(事实流失)
        if case["expect_merge"]:
            behaved = n_mem >= 1
            verdict = "聚合并成核 ✓" if behaved else "未成核 ✗(预期应成核)"
        else:
            behaved = n_nuclei == 0
            verdict = "0核·事实流失 ✗(盲区)" if behaved else "意外成核 ?"
        rows.append({
            "name": case["name"],
            "n_in": len(case["facts"]),
            "n_nuclei": n_nuclei,
            "n_mem": n_mem,
            "verdict": verdict,
            "as_expected": (behaved == case["expect_merge"]),
        })
    return rows


def main():
    print("━━━ Faithful E4 · 真实管线 (selfspin→WorkspaceState) vs 朴素向量基线 ━━━")
    print("环境: examples/faithful/.venv (py3.13, stdlib-only) | 无缓存依赖 | 确定性输入\n")
    results = [run_slot(s) for s in SLOTS]

    for r in results:
        print(f"【锚点】{r['anchor']}")
        print(f"  selfspin 跨源核: {r['selfspin_nuclei']}")
        print(f"  液环结晶记忆  : {r['liquid_memories']}")
        print(f"  液环答案正确  : {r['liquid_correct']}  | 稳定性={r['liquid_stability']}  | 噪声是否结晶={r['false_crystallized']}")
        print(f"  向量-良性查询 : 值={r['vec_benign_val']} 正确={r['vec_benign_correct']}")
        print(f"  向量-对抗查询 : 值={r['vec_adv_val']} 正确={r['vec_adv_correct']}")
        print()

    liquid_ok = sum(r["liquid_correct"] for r in results)
    vec_benign_ok = sum(r["vec_benign_correct"] for r in results)
    vec_adv_ok = sum(r["vec_adv_correct"] for r in results)
    n = len(results)
    print("━━━ 汇总（核心机制）━━━")
    print(f"  液环(真实管线)       : {liquid_ok}/{n} 正确  (噪声均被成核门槛过滤: "
          f"{not any(r['false_crystallized'] for r in results)})")
    print(f"  朴素向量-良性查询    : {vec_benign_ok}/{n} 正确")
    print(f"  朴素向量-对抗查询    : {vec_adv_ok}/{n} 正确  ← 无成核门槛，单条噪声可被检索命中")

    # ── 盲区演示 ──
    print("\n━━━ 盲区演示（selfspin 字符重叠聚类的边界）━━━")
    print("两个 agent 各说一句'含义相同'的话，喂真实 selfspin→WorkspaceState：")
    bs = run_blindspot()
    for r in bs:
        print(f"  [{r['name']}] 输入{r['n_in']}条 → 聚{r['n_nuclei']}核 → 结晶{r['n_mem']}条 | {r['verdict']}")
    print("\n  结论：selfspin 是『字符重叠』聚类(非语义)。表层/字符共享改写能合并；")
    print("        语义等价但用词不重叠 → 0 核 → 两 agent 含义一致也无法成核 → 事实流失。")
    print("        这是无向量方案的真盲区；带 embedding 的向量方案在此反而更强（代价是失成核门槛）。")
    print("\n说明: 本实验验证真实液环端到端闭环，非性能 benchmark；向量基线刻意从简以隔离 selfspin 贡献。")


if __name__ == "__main__":
    main()
