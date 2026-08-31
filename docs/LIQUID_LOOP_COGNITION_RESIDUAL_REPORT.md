# 液环认知补足残差报告 v1.0

> **创建日期**：2026-08-31  
> **维护者**：豆包（本机主Agent）  
> **目的**：补足三个认知残差——底层数学模型、蒸馏来源溯源、性能瓶颈测量

---

## 一、CPE能力侵蚀数学模型深度解析

### 1.1 核心公式

CPE（Capability Erosion）正则化引擎的核心是**风险评分加权模型**：

```
risk_score = (retrospective_risk + drift_risk) × (1 + protection_weight)
```

其中：
- `retrospective_risk`：回顾性衰退风险（新证据与旧证据的重叠度）
- `drift_risk`：策略漂移风险（新证据与锚点定义的偏离度）
- `protection_weight`：保护权重递增（证据越多，保护越强）

### 1.2 回顾性检查（Retrospective Protection）

**公式**：
```
overlap = Jaccard(new_content, old_content) = |A∩B| / |A∪B|
max_overlap = max(overlap for all old_contents)
```

**风险分级**：
| max_overlap | risk_score增量 | 判定 |
|-------------|----------------|------|
| < 0.1 | +0.5 | 高回顾性衰退风险 |
| < 0.3 | +0.25 | 中等回顾性衰退风险 |
| ≥ 0.3 | +0 | 安全 |

**设计意图**：新证据与旧证据重叠度过低，意味着可能在"覆盖"旧能力而非"扩展"旧能力——这是CPE论文中定义的"回顾性衰退"（Retrospective Forgetting）。

### 1.3 漂移检查（Drift Constraint）

**公式**：
```
drift_score = 1.0 - Jaccard(new_content, anchor.description)
```

**风险分级**：
| drift_score | risk_score增量 | 判定 |
|-------------|----------------|------|
| > 0.7 | +0.3 | 高策略漂移风险 |
| > 0.4 | +0.1 | 轻微策略漂移 |
| ≤ 0.4 | +0 | 安全 |

**设计意图**：新证据偏离锚点定义过多，意味着锚点的"能力边界"被侵蚀——这是CPE论文中定义的"策略漂移"（Behavioral Drift）。

### 1.4 保护权重递增（Protection Weight）

**公式**：
```
protection_weight = min(existing_evidence_count / 10, 1.0)
risk_score = risk_score × (1.0 + protection_weight)
```

**设计意图**：证据越多的锚点，其"能力"越成熟，被新证据侵蚀的风险应该越敏感——这是CPE论文中"旧能力权重递增"（Old Capability Weight Escalation）思想的工程化。

### 1.5 重复检测（Duplicate Detection）

**公式**：
```
if Jaccard(new_content, old_content) > 0.7 → MERGE
```

**合并策略**：保留较长的证据，标记较短的为废弃。

### 1.6 最终判定

| risk_score | action | 含义 |
|------------|--------|------|
| > 0.7 | BLOCK | 拒绝写入，能力侵蚀风险过高 |
| > 0.4 | FLAG | 标记警告，允许写入但需复核 |
| ≤ 0.4 | PASS | 安全写入 |

### 1.7 CPE三大表现（scan_erosion）

1. **回顾性衰退**：`value_score`连续两次衰减（Δ > 0.1）
2. **策略漂移**：`stability`突变（Δ > 0.15）
3. **泛化崩塌**：证据间平均重叠度 < 0.2（≥3条证据）

---

## 二、liquid_reweight τ(x) 推导深度解析

### 2.1 核心隐喻：LNN液态时间常数

liquid_reweight的设计灵感来自**液态神经网络（LNN, Liquid Neural Networks）**的时间常数τ：

```
dh/dt = -h/τ(x) + f(h, x, θ)
```

其中：
- `h`：隐藏状态（激活态）
- `τ(x)`：液态时间常数，随输入x自适应变化
- `f(h, x, θ)`：状态转移函数

**关键洞察**：τ不是常数，而是输入的函数——这就是"液态"的含义：时间常数随输入流动变化。

### 2.2 τ(x) 黏滞窄带约束

**实测基准**：LNN CfC（Closed-form Continuous-time）模型的实测τ范围为 `[0.656, 0.780]`（来自lnn_cfc_demo实测）。

**液环工程化**：
```python
TAU_MIN = 0.656
TAU_MAX = 0.780

def tau_x(self, overlap: float) -> float:
    ov = max(0.0, min(1.0, overlap))
    tau = self.tau_max - ov * (self.tau_max - self.tau_min)
    return round(max(self.tau_min, min(self.tau_max, tau)), 4)
```

**推导**：
- `overlap = 1.0`（完全重叠）→ `τ = TAU_MIN = 0.656`（快吸收）
- `overlap = 0.0`（零重叠）→ `τ = TAU_MAX = 0.780`（慢渗透）
- 线性插值：`τ = τ_max - overlap × (τ_max - τ_min)`

**设计意图**：
- 重叠高 → τ小 → 1/τ大 → 激活传播快 → 强相关立即并入
- 重叠低 → τ大 → 1/τ小 → 激活传播慢 → 弱相关缓慢演化

### 2.3 激活拓扑传播（Activation Topology Propagation）

**公式**：
```
amp = min(edge_weight × (1/τ) × 0.5, AMP_CAP=0.5)
activation[neighbor] = min(1.0, activation[neighbor] + amp)
```

**参数**：
- `edge_weight`：拓扑边权（基于keyword containment，范围[0,1]）
- `1/τ`：时间常数的倒数（τ越小，传播越快）
- `0.5`：衰减系数（防止一次传播拉满）
- `AMP_CAP=0.5`：单次传播幅度硬封顶（黏滞防震荡）

**设计意图**：
- 注入锚点自身激活=1.0（被直接命中）
- 邻居锚点按边权×(1/τ)×0.5被唤醒
- 幅度封顶0.5，防止邻居被一次注入瞬间拉满（黏滞约束）

### 2.4 液态召回（Liquid Recall）

**公式**：
```
score = literal_overlap + β × activation + principle_β × principle_overlap
```

**参数**：
- `β = 0.6`：唤醒加成系数
- `principle_β = 0.6`：原理(why)通道召回加成系数（MSM反哺）
- `min_activation = 0.2`：精度护栏，激活低于此的弱边唤醒视为噪声滤除

**精度护栏**：
```
if literal_overlap <= 0 and principle_overlap <= 0 and activation < min_activation:
    skip  # 纯噪声弱边唤醒：滤除
```

**设计意图**：
- 纯字面召回只召回字面匹配的记忆
- 液态召回把"被激活唤醒的邻接记忆"加权拉入候选
- 弱相关但已"活"(激活)的记忆不再漏召
- 精度护栏防止拓扑噪声导致的过激活

### 2.5 激活态持久化

**机制**：
- 激活态按当前值原样落盘
- 冷却在load()时按时间重算（单一事实源=时间戳）
- 半衰期`half_life = 86400秒`（1天），TTL黏滞冷却

**内容寻址anchor id**：
```
anchor_id = explicit_id or sha1(name|description)[:12]
```
跨会话同一事实共享同一激活身份，配套save/load可恢复激活态。

---

## 三、蒸馏来源溯源

### 3.1 Semantica → 治理三件套

**原始项目**：[semantica-agi/semantica](https://github.com/semantica-agi/semantica)  
**定位**：Graph-Native Infrastructure for Context and Accountable AI Systems  
**核心特征**：
- 决策历史：一等公民，可查询（vs 向量DB+RAG不存储）
- 溯源：W3C PROV-O，每个事实可追溯
- 推理：前向链/Rete/Datalog/SPARQL（vs 黑盒）
- 冲突检测：检测、标记、可解决（vs 静默覆盖）
- 时间回溯：时间点图快照（vs 无）
- 合规导出：PROV-O/SHACL/OWL/RDF

**液环蒸馏**：
- `state_at()`：时间点快照（对应Semantica的Time Travel）
- `analyze_impact()`：影响分析（对应Semantica的Decision Intelligence）
- `find_duplicates()`：实体消解（对应Semantica的Entity Resolution）
- 全只读设计：不破坏生产状态

### 3.2 Palantir → 治理dry-run + Authority Line

**原始项目**：Palantir Foundry / AIP  
**核心思想**：
- Ontology（本体）：全局最高约束权限，所有模型推理、系统运行、业务操作都被限定在本体划定的语义与规则边界内
- 数据血缘与全链路审计：所有决策全程可追溯、可核验
- Action Layer Governance：Policy should travel with the business object and action
- Test-driven frameworks for publishing changes：变更先测试再发布
- Stage and review changes in batch：批量暂存和审核变更

**液环蒸馏**：
- 治理dry-run：变更先预演不落地（对应Palantir的test-driven publishing）
- Authority Line：权限分层，不同角色看到不同数据（对应Palantir的Object Security Policies）
- 审计链：所有写入可追溯（对应Palantir的data lineage）

### 3.3 其他蒸馏来源（基于CHANGELOG和源码注释）

| 来源 | 蒸馏机制 | 液环版本 |
|------|----------|----------|
| Octomind | 压缩机制 | v1.8.3 |
| RobSelf | 召回过滤 | v1.8.3 |
| U-OPSD | 共识自检 | v1.8.3 |
| PlugMem | 程序性记忆 | v1.8.4 |
| Skill1 | 程序性记忆 | v1.8.4 |
| EvoC2F | 程序性记忆 | v1.8.4 |
| RE-TRAC | 程序性记忆 | v1.8.4 |
| DeepTutor (m60) | 反绝对化词表 | v2.0.2 |
| DeepSeek Harness (#204) | 可逆副作用底座 | v2.0.2 |
| LNN CfC | τ(x)液态时间常数 | v1.1.0 |
| MSM (Model-Society-Memory) | 原理优先成核 | v1.4.0 |
| PEEK/SEAL | 预算稳态+双优化 | v0.6.3 |
| Science (重放压力论文) | replay_pressure局部阻尼 | v1.8.0 |
| 神经科学 (注意力增益) | attn_bonus | v1.8.0 |

---

## 四、性能基准测试结果

### 4.1 测试环境

- **硬件**：Apple M4 + 16GB RAM（推测，基于LNN硬件底座）
- **Python**：3.14.5（/opt/homebrew/bin/python3）
- **液环版本**：v2.0.3
- **测试数据**：真实生产state（2.02MB，51 anchors，1397 evidences，314 memories）

### 4.2 测试结果

| 指标 | 平均值 | P50 | P99 | 备注 |
|------|--------|-----|-----|------|
| **load()加载** | 13.91ms | 13.88ms | 14.04ms | 2.02MB文件，1397+314条记录 |
| **/recall召回** | 113.90ms | 107.70ms | 157.07ms | 8个不同query，top_k=5 |
| **/remember写入** | 61.71ms | 61.80ms | 62.55ms | 5次写入（perf_test agent） |
| **step()时间动力学** | 18.00ms | 17.05ms | 21.09ms | 处理1397ev+314mem，decay_rate=0.001 |
| **测试进程内存** | 100.30MB RSS | - | - | Python进程加载完整state |

### 4.3 性能分析

**优势**：
1. **加载极快**：13.91ms加载2MB文件+1700条记录，IO+反序列化效率高
2. **step开销可控**：18ms处理1700条记录的stability重算，O(n)复杂度
3. **写入延迟低**：61ms完成写入+成核判断+审计链更新
4. **内存占用合理**：100MB加载完整state，无内存泄漏迹象

**潜在瓶颈**：
1. **召回延迟偏高**：113ms平均召回延迟，P99=157ms。可能原因：
   - RAR索引构建/查询开销
   - tf-idf余弦计算开销
   - entity_boost精确匹配开销
   - HTTP请求+JSON序列化开销
2. **首次召回慢**：第一个query 157ms，后续稳定在107ms，可能有冷启动开销
3. **8790服务内存**：之前观测约387MB RSS，对于1700条记录偏高，可能有缓存或索引占用

**优化建议（非紧急）**：
1. 召回路径增加缓存层（相同query短时间内复用结果）
2. RAR索引持久化（避免每次请求重建）
3. 8790服务内存占用分析（确认是否有内存泄漏或缓存膨胀）

### 4.4 性能基线确认

当前性能对于**本地单机部署**完全够用：
- 召回113ms < 人类感知阈值200ms
- 写入61ms < 实时交互需求
- step 18ms/小时（tick每小时一次），对系统负载无影响
- 内存100MB（测试进程）/ 387MB（8790服务），对于16GB RAM完全可控

**结论**：当前无紧急性能瓶颈，召回延迟有优化空间但不影响使用。

---

## 五、认知提升总结

通过补足这三个残差，我的认知提升如下：

### 5.1 底层数学模型
- 从"知道CPE是什么"到"理解CPE的风险评分加权模型和保护权重递增的设计意图"
- 从"知道τ(x)是液态时间常数"到"理解LNN CfC的dh/dt方程和τ窄带约束的工程化推导"
- 理解了"黏滞"（viscosity）在液环中的工程含义：幅度封顶+窄带约束，防止系统震荡

### 5.2 蒸馏来源溯源
- 从"知道机制是蒸馏来的"到"理解原始项目的核心思想和液环蒸馏的对应关系"
- Semantica的"可审计记忆"哲学与液环的"零丢失+可审计"红线同源
- Palantir的"本体约束"思想与液环的"三条铁律"设计哲学一致
- 理解了液环的"蒸馏"不是简单复制，而是**核心思想的工程化再实现**，守零向量零LLM红线

### 5.3 性能瓶颈认知
- 从"不知道性能如何"到"有完整的性能基线数据"
- 理解了召回延迟的构成（RAR索引+tf-idf+entity_boost+HTTP开销）
- 确认当前性能对于本地部署完全够用，无紧急瓶颈
- 建立了性能基线，后续版本可对比验证是否有性能退化

---

> **报告归档**：2026-08-31  
> **下次更新**：v2.1.0或重大机制变更时  
> **维护者**：豆包（本机主Agent）
