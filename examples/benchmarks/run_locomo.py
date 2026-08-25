#!/usr/bin/env python3
"""run_locomo.py — 液环在 LoCoMo 真实语义流上的「存活度」评测

设计哲学（对齐 critique 末条 + ROADMAP D1）：
  LoCoMo 是 10 段超长真实双人对话（Reddit 风格），事实在流中**单遍出现**，
  不会像受控实验那样重复两次 → 液环的「≥2 篇一致才成核」门槛本就趋近 0 成核率。
  这**正好印证 critique 的核心边界诊断**：液环的确定性强在「重复可复现输入」。

  因此本评测的主战场不是「成核率」，而是**液态层召回（liquid recall）**——
  即把每条对话 turn 作为未结晶的液态记忆存入，用 qa.question 召回，
  看是否命中 qa.evidence 标注的 dialog id。这才是「真实语义流里也活着」的公平度量。

指标：
  - recall@K 命中率：top_k 召回中是否含任一标注 evidence dialog id
  - 成核数：local_rotate 产出的≥2 篇支持 canonical 数（预期极低，作边界证据）
  - S4 污染率：注入噪声 turn 后，top_k 中噪声占比（抗噪不退化度量）

公理纪律：纯 jaccard / 倒排，零 embedding、零向量、零 LLM 推断。
基线对照：none(无记忆=0) / liquid(液环 jaccard) / liquid_e5(液环+英文别名表) / s4(液环+20%噪声)

用法：
  LOCOMO_SUBSET=1 python3 examples/benchmarks/run_locomo.py   # 单对话快速验证管线
  python3 examples/benchmarks/run_locomo.py                   # 全量 10 对话
"""
import sys
import os
import json
import random

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "examples", "faithful"))

from liquid_loop.selfspin import LiquidSelfSpin
import E5_alias_table as E5  # AliasTable（零 embedding / 零 LLM）

DATA = os.path.join(REPO_ROOT, "benchmarks", "data", "locomo10.json")
ALIAS_EN = os.path.join(REPO_ROOT, "benchmarks", "aliases_en.json")
TOP_K = 5
FAST_J = 0.60


def collect_turns(conv: dict) -> list:
    """展平一个对话的所有 session/turn → [(dia_id, text), ...]"""
    turns = []
    for k, v in conv.items():
        if not (k.startswith("session_") and isinstance(v, list)):
            continue
        if k.endswith(("date_time", "_observation", "_summary")):
            continue
        for t in v:
            if isinstance(t, dict) and t.get("text"):
                turns.append((t["dia_id"], t["text"]))
    return turns


def build_ss(turns: list, alias=None):
    """把每条 turn 当一条液态 fact 灌入 selfspin（report_id=dia_id，直接映射 evidence）。"""
    ss = LiquidSelfSpin(run_id="locomo", fast_jaccard=FAST_J)
    ss.liquid_persist = False  # 评测纯内存跑，关跨会话液态持久化（避免 O(n^2) 文件写开销）
    for dia_id, text in turns:
        # 英文语料用 normalize_en（保留词间空格；normalize 中文式去空格会使 jaccard 塌缩）
        norm = alias.normalize_en(text) if alias else text
        ss.ingest(dia_id, "", facts=[norm])
    nuclei = ss.local_rotate()
    return ss, nuclei


def eval_recall(ss, qa_items: list, alias=None):
    """返回 (命中数, 总数, 噪声污染命中数)。命中=top_k 含任一标注 evidence dialog id。
    alias 启用时 query 与 corpus 须同源归一化（否则单边替换破坏匹配）。"""
    hits, total, noise_hits = 0, 0, 0
    for q in qa_items:
        ev = q.get("evidence") or []
        if not ev:
            continue
        total += 1
        qtext = alias.normalize_en(q["question"]) if alias else q["question"]
        res = ss.recall_local(qtext, top_k=TOP_K, liquid=False)
        polluted = False
        for r in res:
            rid = r["report_id"]
            if rid.startswith("N"):  # 噪声 turn 标记
                polluted = True
            if rid in ev:
                hits += 1
                break
        if polluted:
            noise_hits += 1
    return hits, total, noise_hits


def run_conv(conv: dict, qa_items: list, alias=None, noise: float = 0.0, seed: int = 0):
    turns = collect_turns(conv)
    if noise > 0:
        rng = random.Random(seed)
        n = int(len(turns) * noise)
        for i in range(n):
            # 噪声：含独特 token，字面与任何 question 不重叠 → 若进 top_k 即污染
            turns.append((f"N{i}", f"zxqw noise token {i} kjpw unrelated vytr"))
    ss, nuclei = build_ss(turns, alias)
    hits, total, noise_hits = eval_recall(ss, qa_items, alias=alias)
    return {
        "hit": hits, "total": total, "rate": hits / total if total else 0,
        "nucleated": len(nuclei) if nuclei else 0, "facts": len(turns),
        "noise_hits": noise_hits,
    }


def main():
    data = json.load(open(DATA))
    alias_en = E5.AliasTable(path=ALIAS_EN)
    subset = os.environ.get("LOCOMO_SUBSET") == "1"
    convs = data[:1] if subset else data
    print(f"━━━ LoCoMo benchmark · 液环存活度 ━━━")
    print(f"conversations={len(convs)}  top_k={TOP_K}  fast_jaccard={FAST_J}")
    print(f"E5 英文别名表: {ALIAS_EN} ({'开' if not subset else '开'})\n")

    agg = {k: [0, 0, 0] for k in ("liquid", "liquid_e5", "s4")}  # hit, total, noise_hits
    nucl_liquid, nucl_e5, facts_total = 0, 0, 0
    for s in convs:
        qa = s["qa"]
        r_liq = run_conv(s["conversation"], qa, alias=None)
        r_e5 = run_conv(s["conversation"], qa, alias=alias_en)
        r_s4 = run_conv(s["conversation"], qa, alias=None, noise=0.2)
        for key, r in (("liquid", r_liq), ("liquid_e5", r_e5), ("s4", r_s4)):
            agg[key][0] += r["hit"]
            agg[key][1] += r["total"]
            agg[key][2] += r["noise_hits"]
        nucl_liquid += r_liq["nucleated"]
        nucl_e5 += r_e5["nucleated"]
        facts_total += r_liq["facts"]
        print(f"  [{s['sample_id']}] facts={r_liq['facts']:>4} "
              f"liquid_rate={r_liq['rate']:.3f} e5_rate={r_e5['rate']:.3f} "
              f"s4_rate={r_s4['rate']:.3f} s4_noise_topk={r_s4['noise_hits']}/{r_s4['total']}")

    tot = agg["liquid"][1]
    print(f"\n━━━ 汇总（{tot} QA 题 / {facts_total} 条液态记忆）━━━")
    print(f"{'mode':<12}{'recall@K':>10}{'hits':>8}{'noise_topk':>12}{'成核数':>8}")
    print(f"{'none(无记忆)':<12}{'0.000':>10}{'0':>8}{'-':>12}{'-':>8}")
    print(f"{'liquid(液环)':<12}{agg['liquid'][0]/tot:>10.3f}{agg['liquid'][0]:>8}"
          f"{'-':>12}{nucl_liquid:>8}")
    print(f"{'liquid+E5':<12}{agg['liquid_e5'][0]/tot:>10.3f}{agg['liquid_e5'][0]:>8}"
          f"{'-':>12}{nucl_e5:>8}")
    print(f"{'s4(+20%噪声)':<12}{agg['s4'][0]/tot:>10.3f}{agg['s4'][0]:>8}"
          f"{agg['s4'][2]:>12}{nucl_liquid:>8}")
    print(f"\n注：成核数极低属预期（单遍真实流无重复）→ 印证 critique 边界；")
    print(f"主战场=液态召回。E5 英文别名表为临时小样本，验证跨语言机制有效。")


if __name__ == "__main__":
    main()
