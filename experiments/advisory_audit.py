#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""advisory_audit — 轻量旁路语义审计（接活判据：runs≥1 & exit=0）

对液环最近 N 条未归档证据做 advisory 语义审计：每条与同锚点最新一条
既有证据配对，经 crystallize_gate 判 duplicate/conflict/distinct，
结果追加 advisory_log.jsonl。只读液环 state，零写入零阻断。
运行：默认审计最近 10 条；`--n 20` 调整。
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from semantic_gate import crystallize_gate  # noqa: E402

STATE = os.path.expanduser("~/.liquidloop/memory/.liquid/state.json")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "advisory_log.jsonl")


def main(n=10):
    with open(STATE, encoding="utf-8") as f:
        d = json.load(f)
    evs = [e for e in d["evidences"]
           if not e.get("archived") and (e.get("content") or "").strip()]
    evs.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    recent = evs[:n]
    if not recent:
        print("no recent evidences; nothing to audit")
        return 0

    results = []
    for e in recent:
        peers = [p for p in evs
                 if p.get("anchor_id") == e.get("anchor_id")
                 and p.get("id") != e.get("id")]
        if not peers:
            continue
        peer = peers[0]  # 同锚点最新一条既有证据
        g = crystallize_gate(e["content"][:800], peer["content"][:800])
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "evidence_id": e.get("id"), "anchor_id": e.get("anchor_id"),
               "peer_id": peer.get("id"),
               "verdict": g["verdict"] if g else None,
               "dup_prob": round(g["dup_prob"], 3) if g else None,
               "conflict_prob": round(g["conflict_prob"], 3) if g else None,
               "wall_ms": g["wall_ms"] if g else None,
               "fail_open": g is None}
        results.append(rec)
        tag = rec["verdict"] or "FAIL-OPEN"
        print(f"[{tag:9}] ev={rec['evidence_id']} dup={rec['dup_prob']} "
              f"conf={rec['conflict_prob']} ({rec['wall_ms']}ms)")

    with open(LOG, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_dup = sum(1 for r in results if r["verdict"] == "duplicate")
    n_conf = sum(1 for r in results if r["verdict"] == "conflict")
    n_fo = sum(1 for r in results if r["fail_open"])
    print(f"=== audit done: {len(results)} 对 → duplicate={n_dup} "
          f"conflict={n_conf} distinct={len(results) - n_dup - n_conf - n_fo} "
          f"fail-open={n_fo} ===")
    print(f"log: {LOG}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    n = int(args[args.index("--n") + 1]) if "--n" in args else 10
    sys.exit(main(n))
