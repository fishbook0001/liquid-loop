# 记忆治理一等公民：液态记忆的可审计演化

**Memory Governance as a First-Class Citizen: Auditable Evolution of Liquid-State Memory**

> Liquid Loop v1.8.2 ｜ 2026-08-11 ｜ 配液环开源发布

---

## 摘要

液环（Liquid Loop）是零向量、零 LLM 管理的自组织认知记忆系统。其 North-Star 公理是："**Liquid Loop is not a memory storage mechanism; it is a self-regulating memory state evolution mechanism.**"（液环不是一种记忆存储机制，而是一种自调节的记忆状态演化机制。）

v1.8.2 把"**记忆治理**"提升为一等公民：`supersede` 软取代原语、agent 信任分级、时间点快照/影响分析/实体消解查询三件套，配合既有的成核门槛（≥2）、反证轨与稳定性公式 s/(s+2c+1)，形成"**检测 → 复核 → 软取代 → 验证 → 零丢失**"的完整治理闭环。

本文论证三点：① 记忆系统正沿"存储 → 演化 → 治理"三阶段演进，治理是与写入同级的核心能力；② **可证伪性**（可计算、可审计、可推翻）是这一演进的核心判据；③ 液环的每项核心设计都有独立外部印证（Semantica / Claude Code / BRG / PLoS Biology / 闭环 tACS），处于"液态记忆"设计空间的收敛中心。

---

## 1. 引言：为什么记忆需要治理

长期记忆系统的生命周期可划分为三阶段：

| 阶段 | 关注 | 典型机制 | 失败模式 |
|---|---|---|---|
| **存储** | 写入与检索 | append-only、RAG | 长期 horizon 退化（附录[1]警示：append-only store collapse） |
| **演化** | 巩固与衰减 | 成核、老化回收、活性循环 | 冗余堆积、共识与重复不分 |
| **治理** | 冗余检测与问责 | supersede、信任分级、审计 | 不可撤销、不可问责、不可追溯 |

第三阶段（治理）是最新出现的需求，其驱动力来自三个现实缺口：

1. **冗余堆积**：多写入方（agent、harness 自动化、蒸馏桥）产生模板化重复记录，污染激活集。
2. **决策问责**：记忆作为决策依据时，监管/审计需要回答"为什么这样记、影响了谁、何时知道"。
3. **共识 vs 冗余的区分**：跨 agent 一致是成核的合法输入，同 agent 重复是无意义噪声——治理必须精确区分二者。

---

## 2. 液环治理原语（v1.8.2）

### 2.1 `supersede` 软取代原语

与硬删除对立的软治理：被取代证据仅置 `superseded_by` + 时间戳，**零丢失可解冻**；取代者携带反向血缘指针（`supersedes`），形成"我取代了谁"的可审计谱系。与 `archived`（熵增自动清理）正交——`archived`=清理，`superseded_by`=显式语义取代。防呆：自取代/目标不存在拒绝、幂等。

### 2.2 agent 信任分级

借鉴 Claude Code 跨会话信任矩阵（附录[2]）：trust.json 定义 0=guest / 1=trusted / 2=admin。**非 admin 仅能治理自己归属的证据；admin 可跨 agent 治理**，治理写（supersede）过 `is_authorized` + 归属校验双闸。这把"记忆治理权"从"谁能访问 8790"升级为"谁有资格动谁的记忆"。

### 2.3 治理查询三件套（全部只读）

| 原语 | 语义 | 借鉴 |
|---|---|---|
| `state_at(dt)` | 时间点快照：返回 dt 时刻应可见的记忆状态（时间旅行） | Semantica `state_at()` [3] |
| `analyze_impact(node, depth)` | 沿因果出边 + 血缘 BFS 展开下游影响子图（决策问责） | Semantica `analyze_decision_impact()` [3] |
| `find_duplicates(threshold)` | 字符 bigram Jaccard + blocking 预过滤的重复候选检测（**禁向量**），只报告不自动处理 | Semantica `DuplicateDetector` [3] |

### 2.4 既有基础

成核门槛 ≥2（双轨：private/consensus）、反证轨（contradiction 降稳）、稳定性公式 `s/(s+2c+1)`、链式 SHA256 审计——治理原语叠加其上，而非另起炉灶。

---

## 3. 治理闭环实证（真实数据）

生产记忆库（~800 evidences）上的完整治理流程：

| 步骤 | 数据 | 结果 |
|---|---|---|
| **检测** | `/duplicates?threshold=0.85` 扫描 123,375 对 | 50 个候选 |
| **复核** | 按 `(content, agent_id)` 分组全量扫描，区分「同 agent 字节级重复=冗余」vs「跨 agent 同 content=共识素材」vs「模板相似事件流」 | 仅 3 组 7 条真实重复 |
| **软取代** | `supersede` 7 条到各组最早记录（admin 跨 agent） | 7/7 成功 |
| **验证** | 重复残留=0，evidence 总数未删，仅软标记 | 零丢失 |
| **源头预防** | harness_bridge 10 分钟 hash 去重 + distill 投前幂等 + server 60s 短窗去重 | 新增重复被源头拦截 |

配套测试 115 passed（108 存量 + 7 新增）。**治理闭环三原则**：只报告不自动处理（存保守）、共识零误伤、零丢失可解冻。

---

## 4. 外部镜像验证：收敛证据

液环的每项核心设计，都能在独立的外部系统中找到同构实现——这是"液态记忆"设计空间收敛的强信号，非巧合：

| 液环设计 | 外部印证 | 来源 |
|---|---|---|
| 决策/结晶一等公民 | 决策作为一等公民图节点（`record_decision`） | Semantica [3] |
| 链式审计溯源 | W3C PROV-O 逐事实溯源 | Semantica [3] |
| 记忆治理权分级 | 跨会话信任矩阵（bypass↔prompt 互 hold） | Claude Code [2] |
| 增益调制（注意力增益） | 双向循环门控乘法调制（类脑视觉） | BRG, Nature Comms 2026 [4] |
| 惩罚不对称（`2c` 系数） | 惩罚历史单向干扰奖赏学习 | PLoS Biology 2026 [5] |
| 时机即疗效（重放压力/靶向阻尼） | 闭环 tACS 在 beta 反弹时刻刺激更有效 | 闭环 tACS 研究 [6] |

多个独立团队/系统各自推导出同一组机制——液环不是闭门造车，而是这一设计空间的**自然收敛点**。

---

## 5. 讨论

### 5.1 可证伪性标尺

对照民间理论（感知论[7]、元因果论[8]）——其共同特征是**符号堆砌、无操作定义、不可证伪**（"低维投影"吸收一切反例）。液环的反命题是**可证伪**：稳定性公式可计算、因果边可回溯（时间序+含容器）、成核可被反证轨推翻。**可计算、可审计、可推翻，是"记忆理论"与"玄学"的分界线**，也是液环作为工程系统的护城河。

### 5.2 纯度路线

液环坚持禁向量（字符级一致性），Semantica 等工业镜像允许向量（混合路线）。这是 trade-off：**纯度换"无漂移/可解释"，代价是模糊召回弱**。液环用"可证伪 + 确定性推导"补偿纯度路线的短板——该取舍在多次外部对照中被确认成立。

### 5.3 机制优先于条款

开发实践中反复验证"弱模型 + 强机制 ≫ 强模型 + 无机制"：误删事故 → safe_rm + 备份；测试误写生产 → LL_MEM_ROOT 隔离铁律；推送 hook 超时 → guard-ok 门禁。**把纪律焊进执行路径**，是液环自身设计哲学的自我应用。

---

## 6. 结论

液环 v1.8.2 把记忆治理提升为一等公民，形成"检测 → 复核 → 软取代 → 验证 → 零丢失"的完整闭环，并配以可证伪、可审计、可问责的查询原语。外部镜像验证表明液环处于"液态记忆"设计空间的收敛中心。**记忆系统正在从"存储什么"进化到"如何演化"，再到"如何治理"——治理能力将成为下一代记忆系统的分层标准。**

---

## 参考文献

[1] 上交+清华，*Are We Ready For An Agent-Native Memory System?* arXiv:2606.24775（append-only 长期退化警示）
[2] Anthropic Claude Code，cross-session messaging（ListAgents/SendMessage，信任矩阵），v2.1.224，2026-08
[3] Semantica AGI，*Graph-Native Infrastructure for Context and Accountable AI Systems*，github.com/semantica-agi/semantica
[4] Salehi et al., *Modeling attention and binding in the brain through bidirectional recurrent gating*, Nature Communications, 2026
[5] Lin et al., *Competing value signals impair reward-learning via dopaminergic mechanisms*, PLoS Biology e3003922, 2026
[6] 闭环 tACS 增强 beta ERS 提升运动保留（mBrainTrain 报道；Maastricht bioRxiv 2025）
[7] 王建平《感知论》（民间哲学，不可证伪对照）
[8] 谷宏波《元因果论》白皮书（民间玄学数学化，TSA 确权，不可证伪对照）

---
*本文为液环开源项目配套技术小论文，随 v1.8.2 发布。内容基于真实代码与生产数据，非纯理论推演。*
