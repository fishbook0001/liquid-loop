# Changelog

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