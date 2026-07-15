# 液环 Mesh · 固态 A2A 通道（MCP 桥接示例）

把共享液环后端封装成一个 **stdio JSON-RPC 的 MCP server**，让任意支持
Model Context Protocol 的客户端**原生读写同一份共享记忆**——这就是多 agent 间的
**固化（solidified）A2A 通道**。

> 本例以 **TRAE SOLO CN** 作为接入演示（一个支持 MCP 的桌面 AI IDE）。换成 Claude Desktop、
> Cursor、任意 MCP 客户端流程相同：把 `mcp_server.py` 注册为该客户端的 MCP server 即可。

> **后端由你自部署**：`mcp_server.py` 只做协议翻译，**不内置 8790 服务**。你需要先运行自己的
> 液环 SSE 后端（监听某个 URL），再把该 URL 通过环境变量 `LIQUID_LOOP_BASE` 告诉桥接。
> 桥接暴露的三个工具（`liquidloop_remember` / `liquidloop_recall` / `liquidloop_metrics`）全部
> 转发到该后端；成核 / 共识 / 审计链由后端按液环理论执行。

## 文件

| 文件 | 作用 |
|------|------|
| `mcp_server.py` | 纯 stdlib 的 MCP server，暴露 3 个工具（地址由 `LIQUID_LOOP_BASE` 决定，默认 `http://127.0.0.1:8790`） |
| `stress_test.py` | 直连 8790 + 经本桥 双路并发压测（T-A 零丢写 / T-B 共识幂等 / T-C 崩溃恢复）；路径均走环境变量 |
| `cleanup_stress.py` | 压测后原子剔除压测数据、还原基线（**仅在 8790 停止时使用**） |

## 1. 注册到你的 MCP 客户端

把桥接脚本注册为 MCP server（持久化到客户端的 `mcp.json`）。下面以 TRAE 的 `--add-mcp` 为例，
**路径请替换为你自己的**：

```bash
# 1) 准备两个路径（示例值，请改成你机器上的真实路径）
export PY=python3                                   # 任意 Python 3.10+ 解释器
export SVR=/path/to/liquid-loop/examples/trae_mesh_mcp/mcp_server.py

# 2) 若后端不在默认地址，设置环境变量（可选）
export LIQUID_LOOP_BASE=http://127.0.0.1:8790       # 改成你的后端地址

# 3) 注册（TRAE 的 code CLI 路径随安装而异，请替换）
"<path-to-your-trae-code-cli>" \
  --add-mcp "{\"servers\":{\"liquidloop-mesh\":{\"command\":\"$PY\",\"args\":[\"$SVR\"]}}}"
```

> 不同客户端的注册方式不同（如 Claude Desktop 直接编辑 `claude_desktop_config.json` 的 `mcpServers`）。
> 核心是填写 `command`（python 解释器）+ `args`（桥接脚本路径），二者均可带绝对路径。

注册后客户端即可调用 `liquidloop_remember(content, agent_id, category)` 等工具，写入落到你的 8790
后端，与其他 agent 共享同一份记忆。

## 2. 后端前置条件

- 一个运行中的液环 SSE 后端（你自部署，监听 `LIQUID_LOOP_BASE` 指向的地址）。
- 每个写入**必须声明 `agent_id`**（共用机制要求声明写入者标识），否则后端拒绝。
- 桥接进程仅本地 stdio，不暴露端口。

## 3. 双轨共识（桥接视角）

- 同一 `content` 被 **≥2 个 distinct `agent_id`** 写入同一 `category` → 第 2 次写触发
  `nucleated=True, scope=consensus`，且贡献方动态并入 `contributors`（支撑三方+ CCI 计量）。
- 同一 `agent_id` 在**同 `category`** 下写入 **≥2 条一致** `content` → 触发 `scope=private` 结晶。
- 一致性判定走**字符级精确相等 + 审计链哈希**，绝不引入 embedding / 相似度（液环硬约束）。

## 4. 压测用法

```bash
# 先确保后端在线，再：
python3 stress_test.py        # 三关全跑，写 stress_report.json
# 压测会向后端注入 400+ 条压测证据，跑完务必清理：
#   停后端 -> python3 cleanup_stress.py -> 重启后端
```

压测结论（见根目录 `CHANGELOG` v0.9.1）：直连 + 经桥 双路并发 **零丢写**；
共识**唯一成核且幂等**；写入中 `kill -9` 后 `state.json` 仍合法、服务可重启恢复。

所有路径/地址均可经环境变量覆盖（见 `stress_test.py` 顶部注释），适配不同部署拓扑。
