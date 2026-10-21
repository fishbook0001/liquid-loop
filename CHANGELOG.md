# Changelog

## v2.3.0 (2026-10-21) — 去中心化协作原语 + 熵结构降级（2.2.1→2.3.0 minor）

### 新机制（公共原语，零依赖，可被 cli/server/agent loop 接入）
- **拍卖路由 `auction_route.route`**（蒸馏 arXiv:2610.22201）：顺序拍卖替代中央图规划器，按 bid_value 胜出、平局 confidence 优先；低 trust 异常高报价（>3× 次优）治理拦截让渡次优；能力匹配池优先，无匹配退回全池（软约束），空出价 fail-open 悬空。
- **桶队信用分配 `bucket_brigade_credit.assign_credit`**（2610.22201 + Harvard/MIT MPT 2610.22322）：集体结果→局部信用，保留绝对值不归一化；正 outcome 放大、负 outcome 压低保底 floor；bucket-brigade 链式传导沿 links 衰减累加；空贡献/负激活 fail-open。
- **per-agent 成本计量 `cost_meter.CostMeter`**（蒸馏 magpie v392）：旁路记录 Usage，agent/account 维度聚合 token+cost，会话缓存亲和 bind/break，OTLP gauge 骨架；只旁路记账不持连接，fail-open。
- **熵结构分项降级 `entropy.structural_alerts/effective_level`**：任一结构维度越线显式暴露，综合绿灯不得掩盖单项崩塌（单项 ≥0.8 直接 RED，轻度告警 GREEN→YELLOW）；不改变 calculate() 加权语义。

### 修复
- `cost_meter.record()`：坏记录（非 Usage）原仅吞异常仍入列、污染后续聚合；加 isinstance 过滤直接丢弃。
- `state_at()`：补排 `superseded_by` 证据（原只排 archived/时间窗，docstring 与测试契约要求排除）；历史「当时怎么想」由 valid_from/invalidated_at 时间窗负责，与取代关系正交。

### 测试与接线
- 新增 `tests/test_primitives_230.py` 19 例（拍卖5/信用5/计量5/结构降级4）；全量 pytest **264 passed / 0 failed**。
- 调用侧（marvis_memory）接线 MCP 工具 auction_route / assign_credit / event_inbox（事件驱动编排触发），记忆版本演化链、信息素/词面重排（env 开关，默认关 dry-run）、write_guard 写侧认证。
- 六源对齐 2.3.0：`__init__.__version__` · `pyproject.toml` · README · `textutil._get_version` fallback · `/health`（动态读）· docstring；数据 schema 0.4.0 / workspace state 0.5.1 不动（红线#4）。

## v2.2.1 (2026-10-01) — 版本号钉定（六源对齐）

- **背景**：`9abb15a feat(v2.2.1)`（交叉熵 / Temporal 事件溯源）功能早已合入 `main`；`750da5c` 仅把版本注释回退为 v2.2.0（"实际版本未变"）。本次将**版本号正式钉定 v2.2.1**，使标注与已合入功能一致。
- **六源对齐**：`liquid_loop/__init__.__version__` · `pyproject.toml` · `README.md` 包版本 · `textutil._get_version` fallback · 8790 `/health`（动态读 `__version__`，重启生效）· 技术字典，全部对齐至 2.2.1；`tests/test_cli_version.py` 2 passed。
- 数据 schema 0.4.0 / workspace state 0.5.1 不动（禁区，红线#4）。

## v2.2.0 (2026-09-21) — 稀疏CSV编码归档（2.1.0→2.2.0，豆包升级 + Vera 审计）

### 新机制
- **分级存储归档 v2.0 · 稀疏CSV编码（**服务层** `Projects/marvis_memory/ll_archive.py`，193行）**：冷数据（`recall_hits<=0`）归档格式由 JSONL 改为**稀疏CSV**——字段名只在 CSV header 存一次、值按行列对齐，省略 `recall_hits=0` 等默认字段。
  - **实测复现（Vera dry-run，c≥1）**：`旧版JSONL 62.2KB → 稀疏CSV 16.6KB = 省 73.4%`（98条冷数据 / 878条总）；gzip 后预估 `~15.5KB → ~4.1KB`。
  - 兼容旧版 `evidences_archive_*.jsonl.gz` 读取；支持 `--dry-run/--apply/--restore`；零 LLM 零向量、可回滚。
  - ⚠️ 归属说明：该实现位于**服务层仓库**（marvis_memory），非本核心包内；本包 2.2.0 仅同步版号与 `__init__` 特性声明。

### 版号统一（Vera 审计 · 实测）
- 对齐至 **2.2.0** 的全部版本源：`liquid_loop/__init__.__version__` · `pyproject.toml` · `README.md` 包版本 · `textutil._get_version` fallback · 8790 `/health` · 技术字典。
- 修复 `tests/test_cli_version.py::test_version_matches_package` 实测失败（`assert '2.2.0' == '2.1.0'` 三源失配）。

## v2.1.0 (2026-09-21) — 活锚点结晶 + 即时觉醒 + 分级归档（2.0.4→2.1.0，豆包升级 + Vera 审计）

### 新机制
- **自适应结晶 `adaptive_crystallization.py`（新，337行）**：记忆结晶作为**活锚点**——新 evidence 写入时按「关键词重叠 + Jaccard + 同锚点加成 + 原理匹配」自动吸附到最相关 Memory 并加权融合演化（零向量·纯标量，守禁向量公理）；防过度匹配（每 evidence 最多匹配 1 个 Memory、每 Memory 证据上限 50）。
- **即时觉醒 `instant_awareness.py`（新，212行）**：关键操作（remember/recall/governance）后**实时**检测越界事件（duplicate_remember / unauthorized_write / recall_failure_spike / user_dissatisfaction / state_corruption / interface_error / workflow_drift），写入 `state.instant_events`。
- **L1 工作记忆层 `WorkingMemoryItem`（workspace.py +211）**：短期任务上下文缓冲，带 TTL + 容量上限 + priority，可 `promote` 提升为 evidence/memory；与 L2 evidences（持久、参与成核）分层。
- **环境锚时效门实现体（guard.py +136）**：补齐 v3.2 PerceptionGate 的 `env_anchor_probe` 缺口——判定「时效漂移（锚超 TTL）」与「值漂移（live_probe 实测 ≠ 锚值）」；无环境断言的动作返回 None（零影响，不改既有门行为）。
- **分级存储归档 `ll_archive.py`（服务层）**：`recall_hits<=0` 冷数据 gzip 压缩为 `evidences_archive_*.jsonl.gz`，支持 `--dry-run/--apply/--restore`，零 LLM 零向量可回滚。
- **CPE 聚合锚点豁免（cpe.py +86）**：`AGGREGATE_ANCHOR_NAMES` 对齐 workspace.py:690 冲突检测口径，消除同一 overlap 度量的**双口径**误报（仅豁免依赖 pairwise overlap 的 `generalization_erosion`；`retrospective_decay/behavioral_drift` 不依赖 overlap，不受影响）。
- **自检双判据（self_refine.py +31 / textutil.py +15）**：`_judge_answer` 由 bool 改为 `(passed, confidence)` 元组，`verify` 要求 `passed and confidence>=0.3`；probe answer 取前 80 字片段提升命中率。

### 加固
- **undo 快照节流（storage.py）**：`UNDO_KEEP` 10→5、新增 `UNDO_MIN_INTERVAL=60s` —— 60 秒内多次 save 只生成 1 份快照，根除「1 分钟 10 次 save = 25MB 写入」风暴。
- **save 回归护栏门槛（storage.py）**：`GUARD_MIN` 50→10（坑37修复「保护小工作区」），磁盘全量 ≥10 即启用护栏。

### 审计修复（2026-09-21 Vera 诊断审计 · c≥1 实测）
- **版本三源对齐**：`pyproject.toml` 2.0.5→**2.1.0**（与 `__init__.__version__` / `/health` / `state.version` 一致；修复 `test_cli_version` 失败）。
- **觉醒率分母错误（形式闭环修复）**：原 `awareness_rate = detected/total_events` 的分母**只含命中事件**（`_record_event` 是唯一写 stats 处且只在命中时调用）→ 恒为 1.0、完全失去判别力。新增 `ops_checked`（每个被检测操作都计入分母）→ 真觉醒率 = 命中数/被检测操作数（实测矫正为 227/2122≈10.7%）。
- **`instant_recent_ops` 无限膨胀**：三处截断写作 `self._recent_ops = self._recent_ops[-50:]`，**创建新列表使引用脱钩**，state 那份永不截断（实测已达 2122 条含 query/content 全文）。改为 `del self._recent_ops[:-50]` **原地裁剪**，保持与 state 同一对象（同法修复 `instant_events`）。
- **SEAL 诊断判据错配**：`diagnose()` 原把「关键词不匹配」归入 `retrieval`，但其 docstring 明确定义 `reason=检索到但判断不中`——「关键词不匹配」正是判断不中的描述（`verify()` 产出文案含此串）。归错会让策略**系统性走反**（该 `downweight_noise` 的却 `boost_stability`）。已修正为仅「检索/无相关/未找到」归 retrieval。
- **测试同步**：`test_save_regression_guard`（GUARD_MIN=10）、`test_undo_snapshot`（适配 60s 节流）→ 回归 **231 passed / 0 failed**。

- **版号统一**：README.md(2.0.4→2.1.0)、技术字典(v1.5→v1.6)


> 历史版本（v2.0.4 及以下，2026-08-24 ~ 09-05）已归档至 `liquid_loop/_archive/CHANGELOG_history_pre_v2.1.0.md`（2026-10-19 #v383 清理·零删除可恢复）。
