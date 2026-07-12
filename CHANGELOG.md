# Changelog

## v0.5.3 (2026-07-12) — 锚点自进化补强 + bug 修复

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