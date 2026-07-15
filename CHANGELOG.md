# Changelog

## v0.7.0 (2026-07-15) — MESH v2 多智能体共识协议集成（正式发布）
- 新增：`liquid_loop.mesh` 官方 MESH 集成子包（原 `mesh/liquid_loop_mesh_v2.py` 迁入，随 pip 包发布）。
- 能力：结构化证据 schema 自检 `validate_evidence`、双向契约合规 `check_contract`、主体间性共识指数 `compute_cci`、统一认知健康仪表盘 `cognitive_health`、冲突检测 `detect_conflict`、从 8790 REST 拉取状态 `fetch_state`。
- 零向量哲学：一致性判定走结构化精确相等 + 审计链哈希，绝不引入 embedding / 相似度。
- 顶层 `mesh/liquid_loop_mesh_v2.py` 保留为兼容薄壳（仍 `python3 mesh/liquid_loop_mesh_v2.py` 连 8790 打印认知健康报告）。
- 发布：GitHub tag v0.7.0 + PyPI Trusted Publishing（GitHub Actions OIDC，无 token）。

## v0.6.4 (2026-07-14) — SEAL 双优化修复（解 v0.6.3 假落地）
- 修复：SelfRefineEngine.apply_strategy 改锚点 `seal_adjust` 自评层（Anchor 新增字段），`_recalc_anchor` 合成 `stability = base + seal_adjust`，SEAL 调整不再被下次 `add_evidence` 覆盖（E8 暴露 v0.6.3 为假落地）。
- 测试：新增 `tests/test_seal_persistence.py`（3 例），全量 pytest **27 passed**。
- E8 实验：class-1 脆性锚点喂 SelfRefine，结晶率 S 曲线（脆性组比稳固组晚 5 轮结晶=收敛慢）；暴露 stability 随记忆增长单调递减（decay 双因拖垮 base，标 v0.6.x 待办，本次未改）。

## v0.6.3 (2026-07-14) — PEEK 预算稳态 + SEAL 双优化（学术标杆落地）

### 新增能力
- **认知预算稳态器 `cognitive_budget.py`（落地 PEEK, arXiv:2604.09932 固定预算驱逐）**：
  - 三模块蒸馏并适配液环：`Distiller`（evidence.weight + anchor.value_score + recency → 价值）、`Cartographer`（价值 × 访问热度 × 流动性 → 重要性）、`Evictor`（超预算驱逐最低重要性证据）。
  - 驱逐 = **冷归档**（`Evidence.archived=True`，从活跃检索集移出），**零丢失**、审计链完整，呼应液环零向量/零丢失哲学。
  - 预算来源环境变量 `LIQUID_EVIDENCE_BUDGET`（默认 0 = 不限制）；每次 `add_evidence` 自动触发 `_stabilize_budget()`。
- **SEAL 失败诊断双优化（落地 SEAL, arXiv:2605.24426 协同进化）**：
  - `SelfRefineEngine.diagnose()`：失败探测归因到锚点，判定 `retrieval`（召回失败→boost_stability）或 `reason`（推理失败→downweight_noise）。
  - `SelfRefineEngine.apply_strategy()`：策略侧调参（修复确认 +0.1 stability / 噪声 -0.1），与原有 memory 侧 `repair` 构成**双优化**闭环。
  - `run()` 现串起 probe→verify→诊断→双优化，返回含 `diagnoses` / `strategy_actions`。

### 与科研方向映射
- PEEK 预算稳态 → **稳态**方向（记忆系统维持预算稳态而非无限膨胀，防熵爆）。
- SEAL 双优化 → **智能体意识**方向（自洽性：失败→诊断→双侧面修复，强化原级自我意识自洽闭环）。

### 工程
- 测试：`tests/test_peek_seal.py` 7 例（PEEK 3 + SEAL 4），全量 24 passed。
- 版本号 bump 至 0.6.3（包名保持 `liquid_loop`）。

## v0.6.2 (2026-07-14) — 跨锚点误结晶修复 + 文档校正

### 核心修复
- **P0 跨锚点误结晶（`_nucleate`，#bug）**：成核查重由全局 `content` 唯一键改为 `(anchor_id, content)` 复合键。原实现不限定锚点，当两个不同锚点存在相同 `content` 证据时，第二条会被错误跳过、且结晶 `evidence_ids` 跨锚点污染——在 E2 液环↔GNN 桥接（共享边对称表示）下会污染审计链。修复后各锚点独立成核、零跨锚点污染。新增回归测试 `test_nucleate_no_cross_anchor_pollution`。

### 文档校正
- 纠正"四维分类 / 四维熵"误导表述：实际为**三维锚点分类**（value_density / cognitive_stage / liquidity）+ **一维证据质量**，熵值为**八维加权**（含 CPE 三维）。同步校正 `workspace.py` / `entropy.py` 注释、`README.md`、`cli.py`、`examples/quickstart.py`。

### 工程
- 版本号 bump 至 0.6.2（包名保持 `liquid_loop`）

## v0.6.1 (2026-07-13) — 节律采样检索 + 缓存持久化修复

### 核心修复
- **overlap_cache 持久化崩溃（#bug）**：`_keyword_overlap` 缓存键由 `tuple` 改为 `str`（md5 哈希拼接），根除 `storage.save` 因 tuple 键无法 JSON 序列化而抛 `TypeError` 的问题；`storage.save` 同时 `pop("overlap_cache")` 排除运行时缓存，双重保险
- **跨机共识桥接可用**：液环现可安全持久化含 GNN 解释边证据的 workspace（E2 桥接实证 5 锚点 / 80 证据 / 31 结晶）

### 新增能力
- **节律采样检索 `rhythmic_retrieve`**：受 Biba et al. 2026（Nature Human Behaviour, 7Hz theta 脉冲记忆编码）启发，分窗口脉冲采样、每组取最优、跨组去重；相比直接 top-N 避免同质记忆堆叠、增加多样性。已加入 `__init__` 导出

### 其他
- 版本号 bump 至 0.6.1（包名保持 `liquid_loop`，与 0.6.0 一致）

## v0.6.0 (2026-07-13) — CPE 融合 + 主动自精炼 + 策略层

### 核心升级
- **CPE 融合（CPE-fused）**：能力侵蚀检测（`CPERegularizer`）与自组织记忆深度融合，新证据自动评估侵蚀风险并施加正则保护
- **主动自精炼（proactive）**：`SelfRefineEngine` 正式导出，支持证据驱动的主动精炼与回流

### 新增能力（CLI / API）
- 自精炼：`self-refine` — 证据驱动主动精炼
- 策略层：`strategy-health` / `strategy-advice` — 锚点策略健康度与针对性建议
- 能力侵蚀：`cpe-check` / `cpe-scan` / `cpe-status` — 逐点检查 / 全量扫描 / 状态总览
- 关系建模：`relate` / `relation-list` — 锚点间关联（`AnchorRelation`）
- 记忆与冲突：`memory-add` / `conflict-resolve` — 手动结晶与冲突消解
- 锚点描述：`anchor-describe` — 回填 / 设定锚点描述
- `version` 命令打印当前版本

### 熵模型扩展（八维）
- `entropy.py` 新增 `anchor_drift` / `conflict_density` / `evidence_fragmentation` / `activity_gap` / `value_decay_entropy` / `strength_entropy` / `retrospective_decay_entropy` / `behavioral_drift_entropy`，认知健康度量更细

### 工程
- 包内导入统一为相对导入；零外部依赖（仅 click + pyyaml）
- 测试扩展：`test_entropy.py` / `test_workspace.py`
- 最低 Python 由 3.9 提升至 **3.10**（`list[str] | None` 等 3.10 语法）

## v0.5.4 (2026-07-12) — 锚点自进化补强 + bug 修复（跳过被 PyPI yank 的 0.5.3）

### 修复
- **严重 bug**：`CPERegularizer.evaluate_new_evidence` 的 MERGE 分支引用了未定义变量 `protection_weight`（该变量在函数末尾才定义），命中"近似重复"证据时必抛 `NameError`。已移除该键。

### 新增：锚点自进化（回应「锚点群自进化」思路，先做窄不做宽）
- **成核回流**：`WorkspaceState._nucleate` 中，结晶 confidence ≥ 0.8 时自动回填锚点空描述（仅当描述为空，尊重人工设定），让结晶结论回流提升锚点质量
- **群内自洽检测**：`_on_evidence_added` 新增 `_detect_conflicts`，同锚点证据平均一致度 < 0.2（≥3 条）自动生成 `Conflict` 记录并降 stability，让 `Conflict` 类从死字段变活角标
- `liquid-loop status` 现列出冲突/不一致明细
- 锚点群（Cluster）全局概念暂缓：当前锚点量未到阈值（<200），属过度设计；单对单关联已由 `AnchorRelation` 表达

## v0.5.2 (2026-07-12) — 补丁版

### 修复
- CPE 正则化器稳定性改进
- SelfRefineEngine 探测匹配优化
- 内部代码清理与重构

---

## v0.3.0 (2026-07-11) — KFG 补强版

### 🔧 新增：链式哈希审计
- `AuditChain` 类：每次 save/锚点变更自动追加 SHA256 链式哈希
- 审计日志：`.liquid/audit.log`，每次写入含 `index | prev_hash | hash | timestamp | message`
- CLI：`liquid-loop audit`（显示链完整性）/ `liquid-loop audit-log`（查看原始日志）
- `WorkspaceState` 新增 `audit_chain_hash` + `audit_prev_hash` 字段

### 🔧 新增：四维分类自动推导
- `Anchor.auto_classify()`：基于证据数 + 稳定度 + 活跃度自动打标签
- 值密度：`high`(≥5证据+强锚定) / `medium`(≥3证据) / `low`(其余)
- 认知阶段：`crystallized`(≥3+强锚定+高稳定) / `wip`(有证据) / `raw`(无证据) / `tooling`(工具类)
- 流动性：`hot`(24h内活跃) / `warm`(7天内) / `cold`(30天内) / `frozen`(超过30天)

### 🔧 升级：衰减双因模型
- `Anchor.decay_value()` 升级为频率+时间双因子：`score = freq_score×0.4 + time_score×0.6`
- `freq_score`：证据数×0.12 + 访问次数×0.08（上限1.0）
- `time_score`：30天从1.0→0.0指数衰减
- 自动衰减触发：`add_evidence`/`add_anchor`/`add_memory` 时自动降级长期未访问锚点

### 改进
- `anchor-add` CLI 命令：新增 `--value-density`、`--cognitive-stage`、`--liquidity` 可选参数
- `status` 命令：新增审计链哈希显示
- 所有测试通过，端到端验证通过

---

## v0.1.1 (2026-07-11)

### 🎉 首次发布

**核心功能：**
- **自组织记忆**：证据注入 → 自动成核（≥2 条一致证据生成 Memory）→ 权重衰减 → 稳定性重算
- **四维熵值**：锚点分布熵 + 证据一致性熵 + 记忆稳定性熵 + 冲突度，量化认知健康度
- **CLI 9 命令**：`init` / `status` / `anchor-add` / `anchor-list` / `evidence-add` / `evidence-list` / `memory-list` / `snapshot` / `snapshots`
- **Python API**：`WorkspaceState().add_anchor().add_evidence()` 自动触发液环链

**性能（50,000 证据压测）：**
- 写入吞吐：~73K 写入/秒
- 单条延迟：~0.014ms
- 熵值计算：3.5ms（50,000 证据）

**开源：**
- PyPI: `pip install liquid-loop`
- GitHub: https://github.com/fishbook0001/liquid-loop
- 零依赖（仅 click + pyyaml）
- MIT 协议