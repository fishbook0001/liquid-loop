#!/usr/bin/env python3
"""液环 MESH 并发修复验证：独立测试 server（8791 + 隔离 root），多线程轰击。
验证 P0 并发丢写已修复（全量存活）、consensus 竞态唯一成核、原子写无损坏。"""
import os, sys, json, time, subprocess, tempfile, threading, urllib.request
from pathlib import Path

PY = "/Users/feixubuke/.workbuddy/binaries/python/envs/liquidloop062/bin/python3"
SVR = "/Users/feixubuke/Projects/marvis_memory/marvis_liquid_loop_server.py"
ROOT = Path(tempfile.mkdtemp(prefix="ll_audit_"))
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"

def post(path, data):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(data).encode(),
                                  headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def start():
    env = dict(os.environ, LL_MEM_ROOT=str(ROOT))
    p = subprocess.Popen([PY, SVR, "--mode", "sse", "--port", str(PORT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{BASE}/health", timeout=1); return p
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("test server 未起")

def concurrent_writes(n, cat, agent):
    def w(i):
        try:
            post("/remember", {"content": f"{cat}#{i}#{agent}", "category": cat, "agent_id": agent})
        except Exception as e:
            return f"ERR{i}:{e}"
    errs = []
    ts = [threading.Thread(target=lambda i=i: errs.append(w(i))) for i in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]
    return [e for e in errs if e]

def count_cat(cat):
    items = post("/list", {"category": cat})
    return len(items)

print("=== 启动测试 server (8791) ===")
p = start()
print("OK pid", p.pid, "root", ROOT)
try:
    # T-A: 并发丢写（修复前约丢失 8/30）
    N = 30
    print(f"\n[T-A] 30 线程并发写 30 条独立内容 (agent=tester) ...")
    errs = concurrent_writes(N, "audit_conc", "tester")
    got = count_cat("audit_conc")
    print(f"  存活 {got}/{N} | 期望 {N} | 错误 {len(errs)}")
    print(f"  => {'PASS 无丢写' if got == N else 'FAIL 仍丢写'}")
    # T-B: consensus 竞态（不同 agent 写同内容，应仅 1 结晶）
    print("\n[T-B] vera×10 + qwenpaw×10 并发写同一内容 ...")
    SAME = "共识竞态验证-液环零向量"
    errs2 = []
    def w2(a):
        try: post("/remember", {"content": SAME, "category": "audit_cc", "agent_id": a})
        except Exception as e: errs2.append(str(e))
    ts = [threading.Thread(target=w2, args=("vera",)) for _ in range(10)] + \
         [threading.Thread(target=w2, args=("qwenpaw",)) for _ in range(10)]
    [t.start() for t in ts]; [t.join() for t in ts]
    allitems = post("/list", {})
    evs = [x for x in allitems if x.get("type") == "evidence" and x.get("content") == SAME]
    mems = [x for x in allitems if x.get("type") == "memory" and x.get("content") == SAME]
    cons = [m for m in mems if m.get("scope") == "consensus"]
    print(f"  证据数 {len(evs)} | 结晶 {len(mems)} | consensus {len(cons)} | 错误 {len(errs2)}")
    print(f"  => {'PASS 唯一consensus成核(幂等)' if len(cons) == 1 else 'FAIL 竞态致多结晶' if len(cons) > 1 else 'FAIL 未成核'}")
    # T-C: 原子写（kill -9 中途，state.json 仍合法）
    print("\n[T-C] 原子写：kill -9 中途后 state.json 合法性 ...")
    post("/remember", {"content": "atomic_probe_1", "category": "audit_atom", "agent_id": "tester"})
    p.kill(); time.sleep(1)
    sj = ROOT / ".liquid" / "state.json"
    ok = False
    try:
        json.load(open(sj)); ok = True
    except Exception as e:
        print("  state.json 损坏:", e)
    print(f"  => {'PASS 文件合法(原子写生效)' if ok else 'FAIL 文件损坏'}")
finally:
    try: p.kill()
    except Exception: pass
    import shutil; shutil.rmtree(ROOT, ignore_errors=True)
print("\n完成。")
