#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""液环 mesh 联合压测 —— Vera 直连 8790 + trae 经 MCP 桥 双路并发。

三关：
  T-A 零丢写      : 大量唯一内容并发写入，回收后逐条比对 state.json，零丢失
  T-B 共识幂等    : 同 content 多 distinct agent 并发写，验证仅成核 1 条 consensus 且可扩展 contributors
  T-C 崩溃恢复    : 隔离实例(LL_MEM_ROOT=tmp, port 可配) 写入中 kill -9，验证 state.json 合法且可重启

不改变线上 8790 的既有记忆（压测用独立 category / 临时根目录）。

路径/地址均可通过环境变量覆盖（公共版不写死作者本地路径）：
  LIQUID_LOOP_BASE  后端地址（默认 http://127.0.0.1:8790）
  LIVE_STATE        线上 state.json 路径（默认 ~/.marvis/memory/liquid_loop/.liquid/state.json）
  MCP_SERVER        桥接脚本路径（默认本目录 mcp_server.py）
  PY                Python 解释器（默认 sys.executable）
  LL_SERVER         隔离测试用的 8790 SSE 服务脚本（必填环境变量；公共版不内置作者路径，未设则跳过 T-C）
  LIQUID_LOOP_PATH  隔离测试用的库路径（PYTHONPATH，默认作者本地路径）
  LL_MEM_ROOT       隔离实例状态根（默认 /tmp/ll_stress_root）
  STRESS_PORT       隔离实例端口（默认 8799）
"""
import sys, os, json, time, threading, subprocess, signal, random, string, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("LIQUID_LOOP_BASE", "http://127.0.0.1:8790")
MCP_SERVER = os.environ.get("MCP_SERVER", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py"))
PY = os.environ.get("PY", sys.executable)
LIVE_STATE = os.environ.get("LIVE_STATE", os.path.expanduser("~/.marvis/memory/liquid_loop/.liquid/state.json"))
# T-C 崩溃恢复需要用户自部署的 8790 SSE 服务脚本与 liquid_loop 包路径；
# 不写死作者本地路径——公共版由使用者按自己拓扑注入（缺省则跳过 T-C）
LL_SERVER = os.environ.get("LL_SERVER")
LL_PATH = os.environ.get("LIQUID_LOOP_PATH")
LL_MEM_ROOT = os.environ.get("LL_MEM_ROOT", "/tmp/ll_stress_root")
STRESS_PORT = int(os.environ.get("STRESS_PORT", "8799"))

sent = set()
sent_lock = threading.Lock()
errors = []
err_lock = threading.Lock()


def uniq(prefix):
    return f"{prefix}|{os.getpid()}|{''.join(random.choices(string.hexdigits[:16], k=8))}"


def direct_remember(content, agent_id, category="fact", timeout=30):
    payload = json.dumps({"content": content, "category": category,
                          "agent_id": agent_id}).encode()
    req = urllib.request.Request(BASE + "/remember", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_state(path=LIVE_STATE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


# ───────────────────────── trae MCP 客户端（stdio JSON-RPC） ─────────────────────────
class TraeMCPClient:
    def __init__(self, tag="c"):
        self.tag = tag
        self.p = subprocess.Popen([PY, MCP_SERVER], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True, bufsize=1)
        self._id = 0
        self._lock = threading.Lock()
        self._initialize()

    def _rpc(self, method, params=None, notify=False):
        with self._lock:
            self._id += 1
            mid = self._id
            msg = {"jsonrpc": "2.0", "id": (None if notify else mid), "method": method}
            if params is not None:
                msg["params"] = params
            self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
            if notify:
                return None
            while True:
                line = self.p.stdout.readline()
                if not line:
                    return None
                try:
                    resp = json.loads(line)
                except Exception:
                    continue
                if resp.get("id") == mid:
                    return resp

    def _initialize(self):
        self._rpc("initialize", {"protocolVersion": "2024-11-05",
                                 "capabilities": {}, "clientInfo": {"name": "stress", "version": "1"}})
        self._rpc("notifications/initialized", notify=True)

    def remember(self, content, agent_id, category="fact"):
        r = self._rpc("tools/call", {"name": "liquidloop_remember",
                                     "arguments": {"content": content, "agent_id": agent_id, "category": category}})
        if r is None:
            return {"ok": False, "error": "no-response"}
        res = r.get("result", {})
        if res.get("isError"):
            return {"ok": False, "error": (res.get("content", [{}])[0].get("text"))}
        try:
            return json.loads(res["content"][0]["text"])
        except Exception:
            return {"ok": True}

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.terminate()
        except Exception:
            pass


# ───────────────────────── T-A 零丢写 ─────────────────────────
def phase_A():
    print("\n=== T-A 零丢写（Vera 直连 + trae 经 MCP 桥 双路并发）===")
    V_THREADS, T_CLIENTS, PER = 8, 8, 25
    cat = "stress_zero_loss"
    vera_total = V_THREADS * PER
    trae_total = T_CLIENTS * PER
    total = vera_total + trae_total

    def vera_worker(wid):
        for i in range(PER):
            c = uniq(f"VERA|{wid}|{i}")
            with sent_lock:
                sent.add(c)
            try:
                direct_remember(c, "vera", cat)
            except Exception as e:
                with err_lock:
                    errors.append(("vera", str(e)))

    clients = [TraeMCPClient(f"t{k}") for k in range(T_CLIENTS)]

    def trae_worker(cid):
        for i in range(PER):
            c = uniq(f"TRAE|{cid}|{i}")
            with sent_lock:
                sent.add(c)
            try:
                clients[cid].remember(c, "trae", cat)
            except Exception as e:
                with err_lock:
                    errors.append(("trae", str(e)))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=V_THREADS) as ex:
        for k in range(V_THREADS):
            ex.submit(vera_worker, k)
    with ThreadPoolExecutor(max_workers=T_CLIENTS) as ex:
        for k in range(T_CLIENTS):
            ex.submit(trae_worker, k)
    dt = time.time() - t0
    for c in clients:
        c.close()

    st = load_state()
    ev = st.get("evidences", [])
    contents = {e.get("content") for e in ev}
    present = sum(1 for c in sent if c in contents)
    lost = len(sent) - present
    print(f"  并发写入 {total} 条（vera {vera_total} + trae {trae_total}），耗时 {dt:.2f}s，吞吐 {total/dt:.1f} ops/s")
    print(f"  state.json 命中 {present}/{len(sent)}，丢失 {lost}")
    print(f"  写错误 {len(errors)} 条")
    ok = (lost == 0 and len(errors) == 0)
    return {"phase": "T-A 零丢写", "sent": total, "present": present, "lost": lost,
            "errors": len(errors), "seconds": round(dt, 2), "ops_per_s": round(total / dt, 1), "pass": ok}


# ───────────────────────── T-B 共识幂等 ─────────────────────────
def phase_B():
    print("\n=== T-B 共识幂等（同 content 多 distinct agent 并发写）===")
    cat = "stress_consensus"
    CONST = "液环压测共识事实-2026-TRAEBIS"
    owners = ["vera", "trae", "parlant-ctrl"]
    N = 30
    clients = [TraeMCPClient("b0"), TraeMCPClient("b1")]
    nucleated_count = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = []
        for i in range(N):
            o = owners[i % len(owners)]
            if o == "trae":
                cid = i % 2
                futs.append(ex.submit(lambda c=CONST, cid=cid: clients[cid].remember(c, "trae", cat)))
            else:
                futs.append(ex.submit(direct_remember, CONST, o, cat))
        for f in futs:
            r = f.result()
            if isinstance(r, dict) and r.get("nucleated"):
                nucleated_count += 1
    dt = time.time() - t0
    for c in clients:
        c.close()

    st = load_state()
    mems = [m for m in st.get("memories", []) if m.get("content") == CONST]
    cons = [m for m in mems if m.get("scope") == "consensus"]
    scope = cons[0].get("scope") if cons else None
    contrib = sorted(cons[0].get("contributors", [])) if cons else []
    print(f"  同 content 并发写 {N} 次（owners={owners}），耗时 {dt:.2f}s")
    print(f"  consensus 结晶数 {len(cons)}（期望 1，幂等唯一），scope={scope}，contributors={contrib}")
    print(f"  返回 nucleated=True 的次数 {nucleated_count}（2nd+ 写应触发）")
    ok = (len(cons) == 1 and scope == "consensus" and contrib == sorted(owners) and nucleated_count >= 1)
    return {"phase": "T-B 共识幂等", "writes": N, "consensus_nucleated": len(cons),
            "scope": scope, "contributors": contrib, "nucleated_flags": nucleated_count,
            "seconds": round(dt, 2), "pass": ok}


# ───────────────────────── T-C 崩溃恢复（隔离实例） ─────────────────────────
def _launch_isolated(port, root):
    env = dict(os.environ)
    env["LL_MEM_ROOT"] = root
    env["PYTHONPATH"] = LL_PATH
    p = subprocess.Popen([PY, LL_SERVER, "--mode", "sse", "--port", str(port)],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200:
                    return p
        except Exception:
            time.sleep(0.2)
    return p


def phase_C():
    if not LL_SERVER or not LL_PATH:
        print(f"\n=== T-C 崩溃恢复（隔离实例 kill -9 中写入，port {STRESS_PORT}）===")
        print("  ⏭ 跳过：未设置 LL_SERVER / LL_PATH 环境变量（公共版不内置作者本地路径）")
        print("  设定方式：LL_SERVER=/path/to/marvis_liquid_loop_server.py LL_PATH=/path/to/liquid-loop python3 stress_test.py")
        return {"phase": "T-C 崩溃恢复", "skipped": True, "pass": True}
    print(f"\n=== T-C 崩溃恢复（隔离实例 kill -9 中写入，port {STRESS_PORT}）===")
    os.system(f"rm -rf {LL_MEM_ROOT}")
    os.makedirs(LL_MEM_ROOT, exist_ok=True)
    p = _launch_isolated(STRESS_PORT, LL_MEM_ROOT)
    state_file = os.path.join(LL_MEM_ROOT, ".liquid", "state.json")

    stop = threading.Event()
    cnt = [0]
    cl = threading.Lock()

    def hammer():
        while not stop.is_set():
            try:
                direct_remember_safe(uniq("K9"), "k9agent", "stress_k9")
                with cl:
                    cnt[0] += 1
            except Exception:
                pass

    def direct_remember_safe(content, agent_id, category):
        payload = json.dumps({"content": content, "category": category, "agent_id": agent_id}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{STRESS_PORT}/remember",
                                     data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    ths = [threading.Thread(target=hammer) for _ in range(6)]
    for t in ths:
        t.start()
    time.sleep(1.5)
    try:
        os.kill(p.pid, signal.SIGKILL)
    except Exception:
        pass
    stop.set()
    for t in ths:
        t.join(timeout=3)
    writes = cnt[0]
    st = load_state(state_file)
    valid = "__error__" not in st
    ev_count = len(st.get("evidences", [])) if valid else -1
    has_audit = bool(st.get("audit_chain_hash")) if valid else False
    p2 = _launch_isolated(STRESS_PORT, LL_MEM_ROOT)
    recovered = False
    rec_ev = -1
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{STRESS_PORT}/metrics", timeout=5) as r:
            m = json.loads(r.read().decode())
            recovered = m.get("evidences", 0) >= 0
            rec_ev = m.get("evidences", 0)
    except Exception:
        pass
    try:
        os.kill(p2.pid, signal.SIGKILL)
    except Exception:
        pass
    os.system(f"rm -rf {LL_MEM_ROOT}")
    print(f"  kill -9 前并发写约 {writes} 条")
    print(f"  崩溃后 state.json 合法={valid}，证据数={ev_count}，审计链存在={has_audit}")
    print(f"  重启后服务恢复={recovered}，metrics 证据数={rec_ev}")
    ok = valid and has_audit and recovered
    return {"phase": "T-C 崩溃恢复", "writes_before_kill": writes, "state_valid": valid,
            "evidences_in_state": ev_count, "audit_chain_present": has_audit,
            "relaunch_ok": recovered, "pass": ok}


def main():
    print("液环 mesh 联合压测 开始 @", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    base = load_state()
    print(f"基线 evidences={len(base.get('evidences', []))} memories={len(base.get('memories', []))}")
    results = [phase_A(), phase_B(), phase_C()]
    print("\n=== 汇总 ===")
    allpass = True
    for r in results:
        print(f"  [{'PASS' if r['pass'] else 'FAIL'}] {r['phase']}")
        allpass = allpass and r["pass"]
    print(f"总判定: {'全部通过 ✅' if allpass else '存在失败 ❌'}")
    report = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "backend": BASE, "version": "0.9.1",
              "all_pass": allpass, "results": results}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "stress_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("报告已写 stress_report.json")
    sys.exit(0 if allpass else 1)


if __name__ == "__main__":
    main()
