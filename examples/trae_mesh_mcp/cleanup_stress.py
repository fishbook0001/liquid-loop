#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理压测污染：剔除 VERA|/TRAE|/CONST 证据与 CONST 记忆，原子写回 state.json。
仅在 8790 已停止时使用（服务器内存态会覆盖磁盘）。
"""
import json, os, time

P = os.path.expanduser("~/.marvis/memory/liquid_loop/.liquid/state.json")
CONST = "液环压测共识事实-2026-TRAEBIS"

def is_stress(c):
    return c.startswith("VERA|") or c.startswith("TRAE|") or c == CONST

st = json.load(open(P, encoding="utf-8"))
ev = st.get("evidences", [])
before = len(ev)
del_ids = {e["id"] for e in ev if is_stress(e.get("content", ""))}
st["evidences"] = [e for e in ev if not is_stress(e.get("content", ""))]
st["memories"] = [m for m in st.get("memories", []) if m.get("content") != CONST]
for a in st.get("anchors", []):
    a["evidence_ids"] = [i for i in a.get("evidence_ids", []) if i not in del_ids]
st["anchors"] = [a for a in st["anchors"] if not (a["name"].startswith("stress") and not a["evidence_ids"])]
st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

tmp = P + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(st, f, ensure_ascii=False, indent=2)
os.replace(tmp, P)
print(f"证据 {before} -> {len(st['evidences'])}; 记忆 {len(st['memories'])}; 锚点 {len(st['anchors'])}")
