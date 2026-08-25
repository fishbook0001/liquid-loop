import sys, os
REPO = "/Users/feixubuke/liquid-loop"
sys.path.insert(0, REPO)
sys.path.insert(0, REPO + "/examples/faithful")
sys.path.insert(0, REPO + "/examples/benchmarks")
import run_locomo as R
import json

data = json.load(open(R.DATA))
s = data[0]
conv = s["conversation"]
qa = s["qa"]
alias = R.E5.AliasTable(path=R.ALIAS_EN)
ss_l, _ = R.build_ss(R.collect_turns(conv), None)
ss_e, _ = R.build_ss(R.collect_turns(conv), alias)

shown = 0
for q in qa:
    ev = q.get("evidence") or []
    if not ev:
        continue
    rl = ss_l.recall_local(q["question"], top_k=5, liquid=False)
    re_ = ss_e.recall_local(alias.normalize(q["question"]), top_k=5, liquid=False)
    hl = any(r["report_id"] in ev for r in rl)
    he = any(r["report_id"] in ev for r in re_)
    if hl and not he:
        print("Q:", q["question"])
        print("EV:", ev)
        print(" L:", [(r["report_id"], r["score"]) for r in rl][:5])
        print(" E:", [(r["report_id"], r["score"]) for r in re_][:5])
        shown += 1
        if shown >= 3:
            break

if shown == 0:
    print("== no L-hit/E-miss case; dump qa[0] ==")
    q = qa[0]
    print("Q0:", q["question"], "EV:", q["evidence"])
    print(" L:", [(r["report_id"], r["score"]) for r in ss_l.recall_local(q["question"], top_k=5, liquid=False)])
    print(" E:", [(r["report_id"], r["score"]) for r in ss_e.recall_local(alias.normalize(q["question"]), top_k=5, liquid=False)])
    # 直接看 E5.normalize 对 question 和某 evidence turn 的作用
    ev_id = q["evidence"][0] if q["evidence"] else None
    print("E5.norm(Q):", alias.normalize(q["question"]))
    # 找该 evidence turn 文本
    for k, v in conv.items():
        if k.startswith("session_") and isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and t.get("dia_id") == ev_id:
                    print(f"E5.norm(turn {ev_id}):", alias.normalize(t["text"]))
                    print(f"raw turn {ev_id}:", t["text"])

print("\n=== DIRECT D1:3 CHECK ===")
raw = None
for k, v in conv.items():
    if k.startswith("session_") and isinstance(v, list):
        for t in v:
            if isinstance(t, dict) and t.get("dia_id") == "D1:3":
                raw = t["text"]
print("RAW D1:3:", raw)
print("E5.norm RAW:", alias.normalize(raw) if raw else None)
print("ss_l D1:3:", ss_l._facts.get("D1:3"))
print("ss_e D1:3:", ss_e._facts.get("D1:3"))
print("ss_e total facts:", sum(len(v) for v in ss_e._facts.values()))
print("ss_l total facts:", sum(len(v) for v in ss_l._facts.values()))
