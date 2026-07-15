# 液环外部审计接收与处置（归档 · v0.7.1）

- **标的**：liquid-loop v0.7.0（外部评审自 release 包解包审阅）
- **处置纪律**：Critique Before Confidence —— 先做版本差异校准，避免把旧描述误当当前事实，再逐点决策。
- **拍板日期**：2026-07-15

---

## 版本差异校准（先纠偏，再接受）

评审部分描述基于旧版，已核查当前代码纠正：

| 评审描述 | 当前代码实况 |
|---|---|
| `calculate_entropy(state)` 五维加权 | 函数已改名 `calculate()`，**八维加权**（含 CPE 三维） |
| "完全缺时间动力学" | **已有衰减基建**：`Anchor.decay_value(factor=0.95)`、`activity_gap`、`value_score` 衰减；缺的是**显式 dM/dt 过程封装** |
| Evidence → Memory 一致即真 | 确为 2+ 一致成核，且有 `conflict_density` 计数；但**冲突未参与稳定性调节**（无反证轨） |

---

## 最终决策

### ① 命名：entropy 保留，文档补 LEI 定义（不用 CDI）
- 代码层 `entropy.calculate()` **不改名**（历史连续性）。
- 文档 / 论文层首次出现处定义：**Liquid Entropy Index (LEI): a system-stability deviation metric, inspired by entropy but not equivalent to thermodynamic entropy.**
- 理由：当前指标综合 anchor drift / conflict / fragmentation / activity gap / value decay / CPE 三维，实际描述"系统状态偏离稳定流形的程度"，与"熵"隐喻一致。CDI 偏"诊断指标"，而液环目标是动态控制，故不采用 CDI 作主名。

### ② 版本节奏：先 v0.7.1（仅文档），v0.8 才扩功能
- **v0.7.1 范围限定**（本次）：文档同步、API 示例修正、entropy 机制说明补全（LEI）、审计回应归档、修复与零向量哲学矛盾的路线图死项。**不增加任何新机制。**
- **v0.8 正式引入**：
  1. **反证轨**：`Evidence → {support, contradiction} → stability score`（一致增稳、冲突降稳，不再"一致即真"）。
  2. **显式时间动力学**：`M(t+1) = M(t) + reinforcement − decay − contradiction_penalty`（真正的液态循环）。

### ③ 实验顺序：E2 → E3 → E1
1. **E2 错误记忆恢复**（核心）：系统能否主动遗忘错误并恢复？不能则液态只是带衰减的数据库。
2. **E3 多 agent 冲突**（mesh v2 价值证明）：A support / B contradiction / C noise → 是否形成稳定状态。
3. **E1 长期漂移**（压力测试）：1000 轮随机 evidence → 观察收敛。

### ④ 对外定位重述
不宣传"AI 意识"，而宣传 **Self-Regulating Persistent Memory Dynamics for Agent Systems**。
`AuditChain + LEI(Entropy) + Memory decay` 三者组合形成闭环：
```
Input Evidence → Memory State → LEI Evaluation → Decay/Reinforcement → New State → AuditChain
```
这比单独 memory system 更接近可被实验检验的动态系统。

---

## v0.7.1 已执行（本次）
- README：`calculate_entropy → calculate`、"四维 → 八维"（前次修正）。
- README：新增 **LEI 语义澄清块** + **Self-Regulating 闭环定位段**。
- README 路线图：删除与零向量哲学矛盾的"嵌入相似度结晶"死项，改写 v0.8 反证轨 / 时间动力学 / 三实验路线。
- 归档本审计处置文档（`docs/AUDIT_RESPONSE_v0.7.1.md`）。
- bump 0.7.0 → 0.7.1，CHANGELOG 更新。
