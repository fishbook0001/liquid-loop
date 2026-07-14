#!/usr/bin/env python3
# 液环 MESH 穿透式诊断审计 + 压力测试（隔离运行，不污染生产记忆）
import os, sys, json, time, shutil, threading, multiprocessing, traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# 固定根目录（spawn 子进程继承环境变量，避免 mkdtemp 每次重生成）
TMP = Path("/tmp/liquid_audit")
TMP.mkdir(parents=True, exist_ok=True)
os.environ["LL_MEM_ROOT"] = str(TMP)
sys.path.insert(0, str(Path.home() / "liquid-loop"))
sys.path.insert(0, "/Users/feixubuke/Projects/marvis_memory")

import types
_mcp = types.ModuleType("mcp"); _srv = types.ModuleType("mcp.server"); _typ = types.ModuleType("mcp.types")
class _Server:
    def __init__(self, *a, **k): pass
    def list_tools(self): return lambda f: f
    def call_tool(self): return lambda f: f
    def create_initialization_options(self): return {}
_srv.Server = _Server; _typ.Tool = object; _typ.TextContent = object
sys.modules["mcp"] = _mcp; sys.modules["mcp.server"] = _srv; sys.modules["mcp.types"] = _typ

import marvis_liquid_loop_server as srv

R = {}
def log(name, **kw):
    R[name] = kw
    print(f"[{'OK' if kw.get('passed') else 'FAIL'}] {name}: {kw.get('detail','')}")

def setup(name):
    root = TMP / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    srv.WORKSPACE_ROOT = root
    return root

def refresh():
    return srv._state()

# ── T1: 多线程并发写同 content 多 agent -> consensus 竞态(lost update) ──
def t1_concurrent_consensus():
    root = setup("t1")
    srv.ll_remember(refresh(), "共识压测事实X", "audit", agent_id="vera")
    def worker(i):
        srv.ll_remember(refresh(), "共识压测事实X", "audit", agent_id=f"agent{i}")
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(worker, range(8)))
    st = refresh()
    cons = [m for m in st.memories if m.scope == "consensus"]
    evs = [e for e in st.evidences if e.content == "共识压测事实X"]
    ok = len(cons) == 1 and len(evs) == 9
    log("T1_concurrent_consensus", passed=ok,
        detail=f"期望9证据/1共识 实得{len(evs)}证据/{len(cons)}共识 contributors={cons[0].contributors if cons else []}")

# ── T2: 多进程 lost update（各写独立 content，无锁全量 load/save）──
def _mp_worker(args):
    root, contents = args
    srv.WORKSPACE_ROOT = root
    for c in contents:
        srv.ll_remember(refresh(), c, "audit", agent_id="mp")
def t2_multiprocess_lost_update():
    root = setup("t2")
    base = [f"MP独写-{i}" for i in range(40)]
    halves = [base[:20], base[20:]]
    with ProcessPoolExecutor(max_workers=2) as ex:
        list(ex.map(_mp_worker, [(root, h) for h in halves]))
    st = refresh()
    got = {e.content for e in st.evidences if e.content.startswith("MP独写-")}
    ok = got == set(base)
    log("T2_multiprocess_lost_update", passed=ok,
        detail=f"期望{len(base)} 实得{len(got)} 丢失{len(set(base)-got)}")

# ── T3: 崩溃安全（save 非原子 -> 截断损坏）──
def t3_crash_safety():
    root = setup("t3")
    srv.ll_remember(refresh(), "崩溃安全测试", "audit", agent_id="vera")
    sf = root / ".liquid" / "state.json"
    raw = sf.read_text(encoding="utf-8")
    sf.write_text(raw[: len(raw)//2], encoding="utf-8")  # 模拟写中途崩溃
    try:
        st = refresh()
        ok = False
        detail = f"截断后 load 未抛错但恢复{len(st.evidences)}条（静默损坏）"
    except Exception as e:
        ok = False
        detail = f"截断后 load 抛 {type(e).__name__} -> save 非原子确认"
    log("T3_crash_safety", passed=ok, detail=detail)

# ── T4: 边界输入 ──
def t4_boundary():
    root = setup("t4")
    cases = {"empty": ("", "audit", "vera"), "huge": ("X"*200000, "audit", "vera"),
             "emoji": ("🎮游戏🔥", "audit", "vera"), "inject": ("'; DROP--", "audit", "vera")}
    res = {}
    for k, (c, cat, aid) in cases.items():
        try:
            r = srv.ll_remember(refresh(), c, cat, agent_id=aid)
            res[k] = r.get("ok", False)
        except Exception as e:
            res[k] = f"EXC:{type(e).__name__}"
    empty_stored = any(e.content == "" for e in refresh().evidences)
    # 期望：空 content 被拒绝
    ok = res.get("empty") is not True
    log("T4_boundary", passed=ok, detail=f"cases={res} 空content入库={empty_stored}")

# ── T5: 隔离泄漏（不带 agent_id 的 recall 返回他人私有）──
def t5_isolation():
    root = setup("t5")
    srv.ll_remember(refresh(), "隔离测试A私有", "audit", agent_id="agentA")
    all_res = srv.ll_recall(refresh(), "隔离测试", top_k=10, agent_id="")
    leak = any(r.get("agent_id") == "agentA" for r in all_res)
    b_res = srv.ll_recall(refresh(), "隔离测试", top_k=10, agent_id="agentB")
    isolated = not any(r.get("agent_id") == "agentA" for r in b_res)
    ok = (not leak) and isolated
    log("T5_isolation", passed=ok, detail=f"无agent_id泄漏={leak} 带agentB见A={not isolated}")

# ── T6: 性能（灌入 N 条测耗时）──
def t6_perf():
    root = setup("t6")
    N = 1000
    t0 = time.time()
    for i in range(N):
        srv.ll_remember(refresh(), f"perf-{i}", "audit", agent_id="perf")
    dt = time.time() - t0
    per = dt / N * 1000
    ok = per < 5.0
    log("T6_perf", passed=ok, detail=f"{N}条耗时{dt:.1f}s 平均{per:.2f}ms/条")

if __name__ == "__main__":
    for t in [t1_concurrent_consensus, t2_multiprocess_lost_update, t3_crash_safety,
              t4_boundary, t5_isolation, t6_perf]:
        try:
            t()
        except Exception as e:
            log(t.__name__, passed=False, detail=f"EXC:{traceback.format_exc()}")
    passed = sum(1 for v in R.values() if v.get("passed"))
    print("\n=== 诊断汇总 ===")
    print(json.dumps(R, ensure_ascii=False, indent=2, default=str))
    print(f"\n通过 {passed}/{len(R)}")
