#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lexical_probe.py — 零向量召回增强 A/B 探针（LME · 全量可选）

liquid 默认 = tfidf 逐字节同公式 → 零外部资源下数学相等。本脚本验证「加 tfidf 没有的
零向量 lexical 信号」能否让 liquid 超越词频基线：
  - cont        : 重叠系数 containment = |Q∩F|/|Q|（对"短问句全被长答案句覆盖"鲁棒）
  - cos_cont_max: max(cosine, containment)
  - cos_cont_add: cosine + 0.4*containment*(1-cosine) 互补融合
  - entity      : cosine + 实体/数字精确匹配 bonus
  - all         : cos_cont_add + entity
全零向量、零 embedding、零 LLM（守 WHY_NO_VECTOR §六.1 合法符号/标量增强）。
单遍预计算每 fact 的 (cos, cont, ent)，各策略组合排序，避免重复 _idf_cosine。
复用 run_longmemeval 的 collect_turns/build_ss/TfidfBaseline 保证评测口径一致。
"""
import sys, os, json, re, math

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "examples", "faithful"))
import examples.benchmarks.run_longmemeval as R
from liquid_loop.selfspin import _tokens, _idf_cosine, _containment

TOP_K = 5
N_SUB = int(os.environ.get("PROBE_N", "500"))


def entity_boost(q: str, f: str) -> float:
    qt = set(_tokens(q)); ft = set(_tokens(f))
    keys = set(re.findall(r"[0-9]+", q)) | {t for t in qt if len(t) >= 4}
    if not keys:
        return 0.0
    return 0.3 * len(keys & ft) / len(keys)


def main():
    data = json.load(open(R.DATA))
    items = data[:N_SUB]
    modes = ["cosine", "cont", "cos_cont_max", "cos_cont_add", "entity", "all"]
    agg = {m: 0 for m in modes}
    agg_tf = 0
    total = 0
    gain_all, loss_all = [], []
    for it in items:
        ev = set(it.get("answer_session_ids") or [])
        if not ev:
            continue
        total += 1
        turns = R.collect_turns(it)
        ss, _ = R.build_ss(turns, None)
        idf = ss._build_idf()
        q = it["question"]
        # 单遍预计算
        pre = []
        for rid, fs in ss._facts.items():
            for f in fs:
                cos = _idf_cosine(q, f, idf)
                cont, _ = _containment(q, f)
                ent = entity_boost(q, f)
                pre.append((rid, cos, cont, ent))
        # tfidf 基线
        ids = [t[0] for t in turns]; docs = [t[1] for t in turns]
        tf = R.TfidfBaseline(ids, docs)
        top_tf = [rid for rid, _ in tf.query(q, TOP_K)]
        tf_hit = any(rid in ev for rid in top_tf)
        if tf_hit:
            agg_tf += 1
        # 各策略
        hits = {}
        for m in modes:
            if m == "cosine":
                sc = [(c, rid) for rid, c, _, _ in pre]
            elif m == "cont":
                sc = [(c, rid) for rid, _, c, _ in pre]
            elif m == "cos_cont_max":
                sc = [(max(c, co), rid) for rid, co, c, _ in pre]
            elif m == "cos_cont_add":
                sc = [((co + 0.4 * c * (1 - co) if co < c else co), rid)
                      for rid, co, c, _ in pre]
            elif m == "entity":
                sc = [(co + e, rid) for rid, co, _, e in pre]
            elif m == "all":
                sc = [(((co + 0.4 * c * (1 - co)) if co < c else co) + e, rid)
                      for rid, co, c, e in pre]
            sc.sort(key=lambda x: -x[0])
            top = [rid for _, rid in sc[:TOP_K]]
            if any(rid in ev for rid in top):
                agg[m] += 1
                hits[m] = True
        if hits.get("all") and not tf_hit:
            gain_all.append(q)
        if tf_hit and not hits.get("all"):
            loss_all.append(q)

    print(f"━━━ LME 前 {total} 题 · 零向量增强 A/B ━━━")
    print(f"{'mode':<16}{'recall@5':>10}{'hits':>7}{'Δvs tfidf':>12}")
    print(f"{'tfidf(基线)':<16}{agg_tf/total:>10.3f}{agg_tf:>7}{'-':>12}")
    for m in modes:
        print(f"{m:<16}{agg[m]/total:>10.3f}{agg[m]:>7}{(agg[m]-agg_tf)/total:>+12.3f}")
    print(f"\n增益(all 命中 / tfidf 漏) : {len(gain_all)} 例")
    for q in gain_all[:8]:
        print(f"   + {q[:90]}")
    print(f"\n损失(all 漏 / tfidf 命中) : {len(loss_all)} 例")
    for q in loss_all[:8]:
        print(f"   - {q[:90]}")


if __name__ == "__main__":
    main()
