"""机制层真实接口验证：selfspin.recall_local(lexical_boost=...) 在 LME / LoCoMo 上的增益。
直接调用机制层函数（非探针独立实现），确认无回归 + 真实增益。"""
import json
import sys
import os

sys.path.insert(0, ".")
from liquid_loop.selfspin import LiquidSelfSpin
from examples.benchmarks.run_longmemeval import collect_turns, build_ss as lme_build
from examples.benchmarks.run_locomo import collect_turns as lc_collect, build_ss as lc_build

FAST_J = False
TOP_K = 5


def eval_lme(items, lexical_boost):
    hits = total = 0
    for it in items:
        turns = collect_turns(it)
        ev = it.get("answer_session_ids") or []
        if not ev:
            continue
        total += 1
        ss, _ = lme_build(turns)
        res = ss.recall_local(it["question"], top_k=TOP_K, liquid=False,
                              idf_cosine=True, lexical_boost=lexical_boost)
        if any(r["report_id"] in ev for r in res):
            hits += 1
    return (hits / total if total else 0.0), hits, total


def eval_locomo(convs, lexical_boost):
    hits = total = 0
    for s in convs:
        turns = lc_collect(s["conversation"])
        for q in s["qa"]:
            ev = q.get("evidence") or []
            if not ev:
                continue
            total += 1
            # 跳过 local_rotate（O(n²) 仅用于成核计数，与召回验证无关）
            ss = LiquidSelfSpin(run_id="locomo", fast_jaccard=FAST_J)
            ss.liquid_persist = False
            for dia_id, text in turns:
                ss.ingest(dia_id, "", facts=[text])
            res = ss.recall_local(q["question"], top_k=TOP_K, liquid=False,
                                  idf_cosine=True, lexical_boost=lexical_boost)
            if any(r["report_id"] in ev for r in res):
                hits += 1
    return (hits / total if total else 0.0), hits, total


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    d = json.load(open("benchmarks/data/longmemeval_s_cleaned.json"))
    b, _, _ = eval_lme(d[:n], False)
    t, _, _ = eval_lme(d[:n], True)
    print(f"[LME n={n}] lexical_boost=False={b:.3f}  True={t:.3f}  Δ={t-b:+.3f}")

    convs = json.load(open("benchmarks/data/locomo10.json"))
    convs = convs[:n] if n < len(convs) else convs
    b2, _, t3 = eval_locomo(convs, False)
    b3, _, _ = eval_locomo(convs, True)
    print(f"[LoCoMo n={t3}] lexical_boost=False={b2:.3f}  True={b3:.3f}  Δ={b3-b2:+.3f}")


if __name__ == "__main__":
    main()
