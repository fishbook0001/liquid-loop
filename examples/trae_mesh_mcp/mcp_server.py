#!/usr/bin/env python3
"""Liquid Loop Mesh MCP server — bridges TRAE SOLO CN to the shared 8790 backend.

Stdio JSON-RPC (Model Context Protocol). Pure stdlib, no external deps.
Exposes 3 tools so TRAE's AI agent can natively read/write the shared
liquid-loop memory (the solidified A2A channel between Vera and trae):

  liquidloop_remember(content, agent_id, category="fact")
  liquidloop_recall(query, top_k=5, agent_id="trae")
  liquidloop_metrics()

8790 REST contract (from marvis_liquid_loop_server.py):
  POST /remember  {content, category, agent_id(required)} -> {ok, category, content, memory_id, scope, agent_id, nucleated}
  POST /recall    {query, top_k, agent_id} -> [matches]
  GET  /metrics    -> {anchors, evidences, memories, consensus, private, by_agent, audit_chain_hash, ...}
"""
import sys, os, json, urllib.request, urllib.error

# 后端地址：默认本地 8790；用环境变量 LIQUID_LOOP_BASE 覆盖，适配各用户不同的部署拓扑
# （用户 agent / 后端部署情况与作者本地不一致，故不写死路径）
LLM_BASE = os.environ.get("LIQUID_LOOP_BASE", "http://127.0.0.1:8790")
SERVER_NAME = "liquidloop-mesh"
SERVER_VERSION = "1.0.0"


def log(*a):
    print("[mcp-server]", *a, file=sys.stderr, flush=True)


def http_post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        LLM_BASE + path, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def http_get(path):
    req = urllib.request.Request(LLM_BASE + path)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


TOOLS = [
    {
        "name": "liquidloop_remember",
        "description": "存储一条记忆到液环共享后端(8790)。字符级一致性、双轨自动成核；必须声明写入者 agent_id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "记忆内容"},
                "agent_id": {"type": "string", "description": "写入者 agent 标识(如 trae/vera)"},
                "category": {"type": "string", "description": "记忆类别/动态锚点(如 fact/reference/共识)，跨 agent 须一致才能共识"}
            },
            "required": ["content", "agent_id"]
        }
    },
    {
        "name": "liquidloop_recall",
        "description": "检索液环共享后端记忆(字符级一致性，零向量)。返回相关记忆列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词"},
                "top_k": {"type": "integer", "description": "返回条数(默认5)"},
                "agent_id": {"type": "string", "description": "视角 agent(默认 trae)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "liquidloop_metrics",
        "description": "读取液环后端健康指标(anchors/evidences/memories/consensus/by_agent/audit_chain)。",
        "inputSchema": {"type": "object", "properties": {}}
    },
]


def call_tool(name, args):
    try:
        if name == "liquidloop_remember":
            if not args.get("agent_id"):
                return False, "拒绝：缺少 agent_id"
            r = http_post("/remember", {
                "content": args["content"],
                "category": args.get("category", "fact"),
                "agent_id": args["agent_id"],
            })
            return True, json.dumps(r, ensure_ascii=False)
        elif name == "liquidloop_recall":
            if not args.get("query"):
                return False, "拒绝：缺少 query"
            r = http_post("/recall", {
                "query": args["query"],
                "top_k": int(args.get("top_k", 5)),
                "agent_id": args.get("agent_id", "trae"),
            })
            return True, json.dumps(r, ensure_ascii=False)
        elif name == "liquidloop_metrics":
            r = http_get("/metrics")
            return True, json.dumps(r, ensure_ascii=False)
        else:
            return False, f"未知工具: {name}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read(200).decode(errors='replace')}"
    except Exception as e:
        return False, f"ERR {type(e).__name__}: {e}"


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params", {}) or {}

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0", "id": mid,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}
                }
            }
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            ok, text = call_tool(params.get("name", ""), params.get("arguments", {}) or {})
            resp = {
                "jsonrpc": "2.0", "id": mid,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": not ok
                }
            }
        else:
            if mid is not None:
                resp = {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": f"method not found: {method}"}}
            else:
                continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
