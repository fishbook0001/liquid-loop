#!/usr/bin/env python3
"""
液环5大洞察HTTP API服务（独立包装层）
将5个新模块的功能暴露为REST API端点，运行在8791端口，作为8790 MCP服务的补充。

5大洞察：
1. 生成式记忆检索（GenerativeRecall）
2. 赫布关联引擎（HebbianAssociation）
3. 记忆再生抗衰指标（RegenerationMetrics）
4. 双引擎监控架构（DualEngineMonitor）
5. LNN设计原则（文档，见docs/LNN_DESIGN_PRINCIPLES.md）

使用方式：
  python3 liquid_loop_5insights_api.py --port 8791
  curl http://127.0.0.1:8791/health
  curl -X POST http://127.0.0.1:8791/api/v1/recall -H "Content-Type: application/json" -d '{"cue":"赫布法则","memories":[]}'
"""
import sys
import os
import json
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# 添加液环核心库路径
# 2026-09-03 订正：原为 dirname(dirname(__file__)) = /Users/feixubuke，而核心包
# 实际在 /Users/feixubuke/liquid-loop/liquid_loop（仓库目录是连字符，包是下划线），
# 指向上级导致 `import liquid_loop` 只能靠 cwd 或外部 PYTHONPATH 侥幸命中；
# crontab/launchd 下 cwd 不确定 → 必 ImportError。改为脚本所在目录，与 8790
# wrapper 的 PYTHONPATH=/Users/feixubuke/liquid-loop 对齐，任何 cwd 下都可导入。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from liquid_loop.generative_recall import GenerativeRecall
from liquid_loop.hebbian_association import HebbianAssociation
from liquid_loop.regeneration_metrics import RegenerationMetrics
from liquid_loop.dual_engine_monitor import DualEngineMonitor, dual_engine_monitor_decision

# 全局引擎实例
generative_recall_engine = GenerativeRecall(activation_ratio=0.4)
hebbian_engine = HebbianAssociation(
    association_store_path=os.path.expanduser("~/.liquidloop/memory/.liquid/hebbian_associations.json")
)
regeneration_engine = RegenerationMetrics(
    metrics_store_path=os.path.expanduser("~/.liquidloop/memory/.liquid/regeneration_metrics.json")
)
monitor_engine = DualEngineMonitor(
    monitor_store_path=os.path.expanduser("~/.liquidloop/memory/.liquid/dual_engine_audit.json")
)


class InsightsAPIHandler(BaseHTTPRequestHandler):
    """5大洞察API请求处理器"""
    
    def _send_json(self, data, status=200):
        """发送JSON响应"""
        response = json.dumps(data, ensure_ascii=False, indent=2)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(response.encode("utf-8"))
    
    def _read_body(self):
        """读取请求体"""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode("utf-8"))
        except:
            return {}
    
    def do_GET(self):
        """处理GET请求"""
        if self.path == "/health":
            self._send_json({
                "status": "ok",
                "service": "liquid-loop-5insights-api",
                "version": "1.0.0",
                "timestamp": datetime.now().isoformat(),
                "modules": {
                    "generative_recall": "ok",
                    "hebbian_association": "ok",
                    "regeneration_metrics": "ok",
                    "dual_engine_monitor": "ok"
                }
            })
        elif self.path == "/api/v1/insights":
            self._send_json({
                "insights": [
                    {"id": 1, "name": "生成式记忆检索", "module": "generative_recall", "status": "implemented"},
                    {"id": 2, "name": "赫布关联引擎", "module": "hebbian_association", "status": "implemented"},
                    {"id": 3, "name": "记忆再生抗衰指标", "module": "regeneration_metrics", "status": "implemented"},
                    {"id": 4, "name": "双引擎监控架构", "module": "dual_engine_monitor", "status": "implemented"},
                    {"id": 5, "name": "LNN设计原则", "module": "docs/LNN_DESIGN_PRINCIPLES.md", "status": "documented"}
                ]
            })
        elif self.path == "/api/v1/hebbian/stats":
            self._send_json(hebbian_engine.get_stats())
        elif self.path == "/api/v1/hebbian/shortcuts":
            self._send_json({"shortcuts": hebbian_engine.get_shortcuts()})
        elif self.path == "/api/v1/regeneration/status":
            self._send_json(regeneration_engine.get_status())
        elif self.path == "/api/v1/regeneration/trend":
            self._send_json(regeneration_engine.get_trend())
        elif self.path == "/api/v1/regeneration/recommendation":
            self._send_json(regeneration_engine.get_regeneration_recommendation())
        elif self.path == "/api/v1/monitor/stats":
            self._send_json(monitor_engine.get_monitor_stats())
        elif self.path == "/api/v1/monitor/audit":
            self._send_json({"audit_log": monitor_engine.get_audit_log(limit=20)})
        else:
            self._send_json({"error": "not_found", "path": self.path}, status=404)
    
    def do_POST(self):
        """处理POST请求"""
        body = self._read_body()
        
        if self.path == "/api/v1/recall":
            # 生成式记忆检索
            cue = body.get("cue", "")
            memories = body.get("memories", [])
            activation_ratio = body.get("activation_ratio", 0.4)
            generate = body.get("generate", True)
            if not memories:
                self._send_json({"error": "memories_required", "message": "请提供记忆列表"}, status=400)
                return
            generative_recall_engine.activation_ratio = activation_ratio
            result = generative_recall_engine.recall(cue, memories, generate=generate)
            self._send_json(result)
        
        elif self.path == "/api/v1/hebbian/update":
            # 更新赫布关联
            activated_ids = body.get("activated_ids", [])
            context = body.get("context", "")
            if not activated_ids:
                self._send_json({"error": "activated_ids_required"}, status=400)
                return
            count = hebbian_engine.update_from_activation(activated_ids, context)
            self._send_json({"updated": count, "stats": hebbian_engine.get_stats()})
        
        elif self.path == "/api/v1/hebbian/query":
            # 查询某个记忆的关联
            memory_id = body.get("memory_id", "")
            min_strength = body.get("min_strength", 0.0)
            limit = body.get("limit", 20)
            if not memory_id:
                self._send_json({"error": "memory_id_required"}, status=400)
                return
            assocs = hebbian_engine.get_associations(memory_id, min_strength, limit)
            self._send_json({"memory_id": memory_id, "associations": assocs})
        
        elif self.path == "/api/v1/regeneration/collect":
            # 采集再生指标
            metrics = regeneration_engine.collect_metrics(
                new_distillations=body.get("new_distillations", 0),
                new_associations=body.get("new_associations", 0),
                new_domains=body.get("new_domains", 0),
                activation_counts=body.get("activation_counts", {}),
                total_memories=body.get("total_memories", 0)
            )
            self._send_json({
                "metrics": metrics,
                "should_trigger": regeneration_engine.should_trigger_regeneration(),
                "recommendation": regeneration_engine.get_regeneration_recommendation()
            })
        
        elif self.path == "/api/v1/monitor/decision":
            # 双引擎监控决策
            internal_params = body.get("internal", {})
            external_params = body.get("external", {})
            if not internal_params or not external_params:
                self._send_json({"error": "both_internal_and_external_required"}, status=400)
                return
            result = dual_engine_monitor_decision(internal_params, external_params)
            self._send_json(result)
        
        else:
            self._send_json({"error": "not_found", "path": self.path}, status=404)
    
    def log_message(self, format, *args):
        """简化日志"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="液环5大洞察HTTP API服务")
    parser.add_argument("--port", type=int, default=8791, help="服务端口（默认8791）")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="服务地址（默认127.0.0.1）")
    args = parser.parse_args()
    
    server = HTTPServer((args.host, args.port), InsightsAPIHandler)
    print(f"液环5大洞察API服务启动: http://{args.host}:{args.port}")
    print(f"健康检查: http://{args.host}:{args.port}/health")
    print(f"5大洞察列表: http://{args.host}:{args.port}/api/v1/insights")
    print("按 Ctrl+C 停止服务")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
