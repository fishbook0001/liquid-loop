# Liquid Loop Roadmap — 液环升级方向

> 定案于 2026-08-11，基于多轮深度 review + 受控实验（auto_principle_exp）+ 前沿对照
> （PlugMem / 阿尔钦演化经济 / Agent-Native Memory 横评警示）。
> 北极星不变：**禁向量 · 成核门槛 · 零丢失可审计 · 活态循环**。

---

## 一、北极星设计原则（实证定论）

### 取自动 · 存保守
- **自动化放【取】层（检索）**：principle 召回通道已实证——字面零重叠的语义等价记忆可无污染召回（literal≈0 仍入榜，零合并零污染）。
- **合并层【存】保守**：仅显式 principle 触发 v1.4 原理优先成核；**否决自动合并**。
- 依据：`auto_principle_exp`（漏合并 50% + 误合并 50%——"不同事实高表层重叠"被误并 = 走火入魔；取侧对照零污染）。
- 含义：**液环的"语义自动化"住在检索层，不住在合并层**——这是"元婴质变"的正确落点。

---

## 二、升级方向（按优先级）

### D1 · 实证出窍（元婴定义动作）— 最高优先级
- 目标：上公开基准 **LoCoMo / LongMemEval**，发布 benchmark 报告（结果表 + 基线对照 + S4 抗噪扩展）。
- 理由：架构完成度已高，**实证完成度是唯一硬缺口**；开源（GitHub/PyPI）后已具备"出窍见世面"的对象。
- 里程碑：benchmark runner → 对照基线 → 报告发布。

### D2 · 取侧元婴化（自动化放检索）
- principle **自动归纳**（取侧、不触发合并）→ 按任务召回 related-but-literal-disjoint 记忆。
- **跨代成核**：`caused_by→causes` 链反向作用于召回/成核候选排序（因果邻近加成）。
- 落地：`liquid_reweight` 深化 + `circulate` 泵入升级。

### D3 · 程序性记忆一等公民（借鉴 PlugMem）
- `distill_registry` 的 invocation 结构化投液环作 **procedural memory**；按任务召回可复用技能。
- 补 Winter 对 Alchian 的批评缺口：**成功行为的传递机制**。
- 落地：`/remember` principle 通道扩展 + `distill_to_liquid` 升级。

### D4 · 合并层保守加固
- 保持显式 principle 合并；**自动合并否决**（受控实验已证）。
- 突破"用词不重叠"合并盲区 → 需非字面通道（LLM 辅助/外部知识），且**必须过同门受控实验**才准上生产（标记 D4-experimental）。

### D5 · 生态与工程
- v1.8.0 推送 + GitHub Release + README/description 补全。
- benchmark 报告发布；外部 issue/PR 接入流程。

---

## 三、版本规划
- **v1.9**：D2 取侧自动化 + D3 程序记忆雏形。
- **v2.0**：D1 公开 benchmark 落地（元婴/出窍）。
- D4 视实验门动态放行。

---

## 四、守则
1. 任何新机制上生产前先过**受控实验门**（隔离命名空间 + dry-run + 误合并/漏合并率评估）。
2. 禁向量铁律不因"自动"破例（自动 = 结构化确定性推导，非 embedding）。
3. 活态循环（circulation）是基线能力，持续深化不回归。
