#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lexical_probe_locomo.py — 零向量召回增强 A/B 探针（LoCoMo · 全量 10 对话）

与 lexical_probe.py 同设计哲学，但跑 LoCoMo（英文长对话，整体 recall@5≈0.472，
空间远大于 LME），复用 run_locomo 的 collect_turns/build_ss/TfidfBaseline 保证口径一致。
验证零向量 lexical 信号（containment 融合 / 实体·数字精确加权）能否让 liquid 超越词频基线。
"""
import sys, os, json, re, math

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "examples", "faithful"))
import examples.benchmarks.run_locomo as RL
from liquid_loop.selfspin import _tokens, _idf_cosine, _containment

TOP_K = RL.TOP_K


def entity_boost(q: str, f: str) -> float:
    qt = set(_tokens(q)); ft = set(_tokens(f))
    keys = set(re.findall(r"[0-9]+", q)) | {t for t in qt if len(t) >= 4}
    if not keys:
        return 0.0
    return 0.3 * len(keys & ft) / len(keys)


def main():
    data = json.load(open(RL.DATA))
    modes = ["cosine", "cont", "cos_cont_max", "cos_cont_add", "entity", "all"]
    agg = {m: 0 for m in modes}
    agg_tf = 0
    total = 0
    gain_all, loss_all = [], []
    for s in data:
        qa = s["qa"]
        for q in qa:
            ev = set(q.get("evidence") or [])
            if not ev:
                continue
            total += 1
            turns = RL.collect_turns(s["conversation"])
            ss, _ = RL.build_ss(turns, None)
            idf = ss._build_idf()
            qq = q["question"]
            pre = []
            for rid, fs in ss._facts.items():
                for f in fs:
                    cos = _idf_cosine(qq, f, idf)
                    cont, _ = _containment(qq, f)
                    ent = entity_boost(qq, f)
                    pre.append((rid, cos, cont, ent))
            ids = [t[0] for t in turns]; docs = [t[1] for t in turns]
            tf = RL.TfidfBaseline(ids, docs)
            top_tf = [rid for rid, _ in tf.query(qq, TOP_K)]
            tf_hit = any(rid in ev for rid in top_tf)
            if tf_hit:
                agg_tf += 1
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
                gain_all.append(qq)
            if tf_hit and not hits.get("all"):
                loss_all.append(qq)

    print(f"━━━ LoCoMo 全量 {total} 题 · 零向量增强 A/B ━━━")
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
