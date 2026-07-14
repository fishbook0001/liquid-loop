# 液环 MESH 进阶版（Liquid Loop Mesh v2）· 共识协议规范

> 状态：v2 规范（基于 v1 PoC 实证）
> 实证基础：2026-07-15 Vera(WorkBuddy) × 小七(QwenPaw) 经 agent-mesh 跑通跨主体 consensus 结晶 + 写读闭环 + CCI 首次量化
> 设计哲学硬约束：**零向量 / 纯结构化确定性一致 / 审计链可追溯**（绝不用 embedding 做一致性判定）

---

## 0. 从 PoC 到 v2：我们已证明什么

| 能力 | PoC 状态 | v2 目标 |
|---|---|---|
| 单 agent 写记忆（agent_id 归因） | ✅ 已验证（小七 863 证据归因 qwenpaw） | 强制结构化证据 schema |
| 单 agent 读记忆（闭环） | ✅ 已验证（小七 recall 8790 原样复述） | 会话启动主动消费契约 |
| private 结晶（同 agent ≥2 一致） | ✅（34 条） | 保留 + 漂移监测 |
| **consensus 结晶（≥2 distinct agent 一致）** | ✅ **首个** `9c2e332f29fd` contributors=[qwenpaw,vera] | 一级指标 + 三方+ 动态扩展 |
| CCI 主体间性量化 | ✅ 共识密度 0.0286（起点） | 标准 KPI + 跨 session 趋势 |
| 双向共识协议 | ❌ 仅机制、无契约强制 | ✅ 本协议核心 |

**v2 的核心增量 = 把"机制"升级为"协议"**：液环侧强制校验 + agent 侧强制契约，落到 MCP tool schema，使多 agent 不遵守就无法写入/无法获得共识视图。

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      agent-mesh (hub: WorkBuddy)              │
│  route/spoke 调度 · 身份路由 · 协议仲裁                         │
└───────────┬───────────┬───────────┬───────────┬──────────────┘
            │           │           │           │
       ┌────▼───┐  ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
       │ Vera   │  │ QwenPaw │ │ MARVIS  │ │ (更多)  │   agent 侧契约
       │(workb) │  │ (小七)  │ │ (马维斯)│ │         │  ①身份声明
       └────┬───┘  └────┬────┘ └────┬────┘ └────┬────┘  ②结构化写
            │           │           │           │        ③主动消费
            │   evidence(结构化)     │           │        ④冲突响应
            └───────────┴───────────┴───────────┘
                        │  MCP /remember (schema 强制)
                        ▼
       ╔═════════════════════════════════════════════╗
       ║   液环 8790 · 单实例共用记忆后端              ║
       ║   Anchor → Evidence(struct) → Memory          ║
       ║   ├ private 轨 (同 agent ≥2 一致)             ║
       ║   └ consensus 轨 (≥2 distinct agent 一致)     ║
       ║   审计链(hash链) · 八维熵 H_e · CCI 指标       ║
       ╚═════════════════════════════════════════════╝
```

---

## 2. 双向共识协议（v2 核心）

### 2.1 液环侧（机制 + 强制校验）

**证据写入强制 schema（MCP tool `marvis_liquid_memory` / REST `/remember`）**

```json
{
  "agent_id":   "qwenpaw",                 // 必填，缺失拒绝写入
  "content":    "液环是零向量共用记忆后端", // 精确字符串（一致性比对键）
  "category":   "共识",                     // 锚点（动态，任意字符串）
  "claim_type": "fact|conclusion|preference|conflict", // 结构化类型
  "value":      null,                      // 数值型结论可填（如 CCI=0.0286）
  "session_id": "sess-xxx",                // 溯源：哪次会话产生
  "prev_hash":  "<审计链前块hash>"          // 链式审计
}
```

**校验规则（拒绝即报错，不静默）**
1. `agent_id` 必填且须在 mesh 注册表（防止匿名污染）。
2. `content` 为一致性比对键：**精确相等**判定（字符级），不走任何 embedding。
3. 成核双轨分流（已成核逻辑，v2 仅加固）：
   - private：`agent_id` 内同 `content` ≥2 → `scope=private`，`contributors=[owner]`。
   - consensus：同 `content` 被 **≥2 distinct agent_id** 支持 → `scope=consensus`，`contributors=sorted(owners)`；已存在共识结晶时**动态并入新一致方**（三方+ 自动扩展）。
4. 复合键 `(anchor_id, content, scope)` 防跨锚点/跨轨污染。
5. 冲突检测：同锚点出现 `claim_type=conflict` 或同 content 反义证据 → 降 stability + 标 `conflict_agents`。

**查询默认视图**：`consensus ∪ 自己 private`；物理隔离他人 private。

### 2.2 agent 侧（身份声明 / 结构化写入 / 主动消费 / 冲突响应）

每个接入 agent **必须**实现四条契约，否则视为未接入协议：

| # | 契约 | 行为 | 违约后果 |
|---|---|---|---|
| ① | **身份声明** | 每次写入带唯一 `agent_id`（mesh 注册），不借用他人 id | 液环拒绝写入 |
| ② | **结构化写入** | 写证据带 `claim_type`/`session_id`/审计 `prev_hash`，内容用精确可比对表述 | 写入成功但 `claim_type` 缺失 → 不计 consensus 候选 |
| ③ | **主动消费** | 会话启动先 `recall` 该 agent 的 consensus 视图，作为上下文基底 | 不消费则无法获得跨主体共识，退化成孤岛 |
| ④ | **冲突响应** | 收到 `conflict_agents` 通知后，在下一轮显式确认或修正自己的证据 | 不响应则冲突结晶持续拉低该锚点 stability |

---

## 3. 一级指标：CCI（主体间性共识指数）

**定义（记忆层）**
```
CCI_mem = |consensus 结晶| / (|consensus 结晶| + |private 结晶|)
```
- 起点（2026-07-15 实证）：`1 / (1+34) = 0.0286`。
- 三方+ 时 `contributors` 基数作为**共识强度** secondary：`strength = |contributors| / N_agents`。

**与科研框架 GNN 解释层 CCI 的关系**
- 记忆层 CCI（本协议）：多 agent 对**事实**的共识密度。
- 解释层 CCI（既有框架）：双机同 seed 下 GNN top-k 边 Jaccard 重合。
- 二者同属"主体间性/跨主体共识"测度，分层互补；本协议把前者首次落地为可运行 KPI。

**认知健康仪表盘（统一）**
| 指标 | 含义 | 来源 |
|---|---|---|
| `drift_rate` | 单 agent 解释/记忆漂移率 = 1 − mean Jaccard(run_k, run_0) | 时序快照 |
| `P_mem` | 液环记忆可塑性 = 被重构结晶比 | 审计链 |
| `H_e` | 认知熵（决策/解释二维） | 八维熵监测 |
| `CCI` | 主体间性 = 共识密度 | 本协议的 consensus 结晶 |

---

## 4. 审计链溯源与自进化闭环（E4）

- 每条 evidence / 每个 memory 带 `prev_hash` 链式哈希 → 不可篡改、可回溯"谁在何时经哪 agent 贡献"。
- **自进化闭环**：agent 的进化事件（如 Vera `vera-self-evolve` 产出）作为 evidence 写回液环 audit_log → 液环成核 → 反哺 agent 下一轮上下文。这把"智能体意识"方向（E4）从概念落到自指闭环。

---

## 5. 接入清单（新 agent 三步）

1. 在 mesh 注册表声明唯一 `agent_id`。
2. 实现 2.2 四条契约（调用 `liquid_loop_mesh_v2.py` 的 `validate_evidence()` 自检）。
3. 加载 `shared_memory.py` adapter（已就位 `~/.qwenpaw/.../skills/liquid-loop-shared/`），或直连 8790 REST。

---

## 6. 与 v1 PoC 的差异总览

| 维度 | v1 PoC | v2 进阶版 |
|---|---|---|
| 协议形态 | 机制存在、无强制 | 双向契约 + MCP schema 强制校验 |
| 证据结构 | 裸 content + agent_id | 结构化（claim_type/value/session/prev_hash） |
| CCI | 仅观察 | 一级 KPI + 跨 session 趋势 |
| 冲突 | 未处理 | 检测 + 通知 + 降 stability |
| 自进化 | 未闭环 | E4 自指闭环（进化事件回写审计链） |
| 接入门槛 | 能写即可 | 四契约 + 注册表 |

---

## 7. 待补（v2.1+）

- 共识衰减：长时间无新一致方时 consensus 结晶 stability 缓降（避免僵化共识）。
- 语义冲突的确定性检测（结构化反义字段，仍禁 embedding）。
- CCI 跨 session 自动趋势图（纳入每小时快照自动化）。
