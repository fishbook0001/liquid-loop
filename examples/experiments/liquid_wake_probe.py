"""LiquidRewight 液态唤醒路径(liquid=True)基准探针——验证 A-1 核心盲区是否有增益。
口径与 run_longmemeval 一致(session 级 recall@5),对照 liquid=False(默认)。"""
import json
import sys
import time

sys.path.insert(0, ".")
from examples.benchmarks.run_longmemeval import collect_turns, build_ss
from examples.benchmarks.run_locomo import collect_turns as lc_collect

TOP_K = 5


def eval_items(items, liquid):
    hits = total = 0
    t0 = time.time()
    for it in items:
        turns = collect_turns(it)
        ev = it.get("answer_session_ids") or []
        if not ev:
            continue
        total += 1
        ss, _ = build_ss(turns)
        res = ss.recall_local(it["question"], top_k=TOP_K, liquid=liquid,
                              idf_cosine=True, lexical_boost=True)
        if any(r["report_id"] in ev for r in res):
            hits += 1
    dt = time.time() - t0
    return (hits / total if total else 0.0), hits, total, dt


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    d = json.load(open("benchmarks/data/longmemeval_s_cleaned.json"))
    items = d[:n]
    b, h, t, dt = eval_items(items, False)
    l, h2, t2, dt2 = eval_items(items, True)
    print(f"[LME n={t}] liquid=False={b:.3f}({dt:.0f}s)  liquid=True={l:.3f}({dt2:.0f}s)  Δ={l-b:+.3f}")

    convs = json.load(open("benchmarks/data/locomo10.json"))
    convs = convs[:2]
    hits_l = hits_f = total = 0
    t0 = time.time()
    for s in convs:
        ss, _ = build_ss(lc_collect(s["conversation"]))
        for q in s["qa"]:
            ev = q.get("evidence") or []
            if not ev:
                continue
            total += 1
            if any(r["report_id"] in ev for r in ss.recall_local(
                    q["question"], top_k=TOP_K, liquid=True, idf_cosine=True, lexical_boost=True)):
                hits_l += 1
    dt_l = time.time() - t0
    print(f"[LoCoMo n={total}] liquid=True={hits_l/total:.3f}({dt_l:.0f}s)  (False 基线见报告 0.452-0.524)")


if __name__ == "__main__":
    main()
