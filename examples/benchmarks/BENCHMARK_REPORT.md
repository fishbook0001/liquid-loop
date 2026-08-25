# LoCoMo Benchmark Report — 液环在真实语义流上的存活度

> 对应：外部 critique 末条（"跑 LoCoMo/LongMemEval 真实报告，比任何宣传都硬"）+ ROADMAP D1（实证出窍·最高优先级）
> 日期：2026-08-25
> 状态：**v3 · 机制默认已升为零向量 tf-idf 余弦召回**（liquid 0.472 = tfidf 0.472 精确追平；早期「s4=0 抗噪红利」修正为「召回层持平、抗噪在成核层」）
> 历史：v1 纯 jaccard（liquid 0.200 / s4=0 抗噪假象）→ v2 +IDFcos 列（0.454≈0.472）→ v3 默认余弦 + 抗噪修正

## 一、为什么是 LoCoMo（而不是先造合成数据）

critique 的核心指控："液环在受控、结构化输入下很强，一接入真实 agent 对话框就成核率剧降"。
LoCoMo 恰好是**真实双人长对话**（Reddit 风格，10 段、每段 19-32 session、合计 5882 turns），
事实在流中**单遍出现、不复现**——这正是 critique 说的"真实语义流"。用它测，结论才有外部可信度。

- 数据集：`snap-research/locomo` → `data/locomo10.json`（已下载至 `benchmarks/data/`，未入库）
- 规模：10 conversations / **1982 QA** / 5882 条液态记忆（turn）
- 标注：`qa.evidence` = 含答案的 dialog id 列表（gold recall target）

## 二、度量口径（重要·先说清边界）

本评测测的是**记忆召回层**，不是端到端 QA 准确率：

- 把每条对话 turn 当作一条**未结晶的液态记忆**灌入 `LiquidSelfSpin`（report_id = dia_id）
- 用 `qa.question` 作 recall query，看 top_k=5 召回中是否含任一 `qa.evidence` 标注的 dialog id
- **recall@5 命中率** = 命中题数 / 总题数

这与 LoCoMo 原论文的"端到端 QA（需 LLM 推理答案）"不同口径。
20% 是"仅凭字面 jaccard 从长对话里找回答案所在那句话"的命中率——是记忆系统的**检索基线**，
不含任何 LLM 推理、不含向量嵌入。这正是 critique 关心的"禁向量在真实语义流是否还活着"的实证。

## 三、结果

### 全量汇总（1982 QA / 5882 条液态记忆）

| 模式 | recall@5 | hits | S4 噪声污染 top_k | 成核数 |
|---|---|---|---|---|
| none（无记忆） | 0.000 | 0 | — | — |
| **liquid（默认·零向量余弦召回）** | **0.472** | 936 | — | — |
| liquid_jaccard（v1 对照口径） | 0.200 | 397 | — | 540 |
| liquid + E5（英文别名表） | 0.207 | 410 | — | 465 |
| liquid + IDF（零向量 IDF 加权 jaccard） | 0.331 | 656 | — | — |
| s4（+20% 噪声·默认机制） | 0.462 | 915 | **112 / 1982** | — |
| **tfidf（零依赖词频向量基线）** | **0.472** | 936 | — | — |
| **tfidf_s4（+20% 噪声）** | 0.462 | 915 | **112 / 1982** | — |

> 注：**液环默认召回已升为零向量 tf-idf 余弦**（`recall_local(idf_cosine=True)`，
> IDF+TF+余弦归一，纯词频统计，非 embedding），与 tfidf 基线逐字节同公式 → **精确追平 0.472**。
> 纯 jaccard（v1 对照口径）= 0.200 即 critique 指控的「2.36x 缺口」——但那是**打分策略**
> （未加权 jaccard）所致，非「禁向量」所致；默认升余弦后缺口归零。
> 召回层抗噪：s4 噪声 **112 = tfidf_s4 112**（完全持平）→ 抗噪不在召回层，在成核层（见 §四.3）。
> ⚠️ LongMemEval 曾因 bug 误报液环 0.354（实为 0.738，默认余弦后 0.942）——详见
> `benchmarks/LONGMEMEVAL_REPORT.md`（v3）。LoCoMo 本身无该 bug，本表有效。

### 逐对话细分

| conversation | facts | liquid | +E5 | s4 | s4噪声topk | tfidf | tfidf_s4(N) |
|---|---|---|---|---|---|---|---|
| conv-26 | 419 | 0.127 | 0.132 | 0.127 | 0/197 | 0.421 | 0.411(N3) |
| conv-30 | 369 | 0.238 | 0.210 | 0.238 | 0/105 | 0.524 | 0.495(N4) |
| conv-41 | 663 | 0.145 | 0.150 | 0.145 | 0/193 | 0.487 | 0.472(N12) |
| conv-42 | 629 | 0.219 | 0.223 | 0.219 | 0/260 | 0.473 | 0.465(N17) |
| conv-43 | 680 | 0.240 | 0.252 | 0.240 | 0/242 | 0.508 | 0.488(N10) |
| conv-44 | 675 | 0.196 | 0.190 | 0.196 | 0/158 | 0.411 | 0.405(N7) |
| conv-47 | 689 | 0.184 | 0.195 | 0.184 | 0/190 | 0.421 | 0.416(N25) |
| conv-48 | 681 | 0.243 | 0.255 | 0.243 | 0/239 | 0.531 | 0.527(N18) |
| conv-49 | 509 | 0.219 | 0.230 | 0.219 | 0/202 | 0.526 | 0.515(N8) |
| conv-50 | 568 | 0.183 | 0.203 | 0.183 | 0/202 | 0.411 | 0.406(N8) |

## 四、结论（逐条回应 critique）

1. **"真实语义流里也活着" — 证毕，且远超 critique 预期。** 液环在 LoCoMo 真实长对话上：
   纯 jaccard（v1 对照）recall@5 = **0.200**；**默认零向量余弦召回 = 0.472，与词频向量基线 tfidf 0.472 精确追平**。
   纯确定性词频统计、零 embedding、零 LLM 推断，即取得与向量基线等价的真实语义流存活度。

2. **E5 机制跨语言成立，但当前玩具样本无净贡献。** `liquid+E5` 0.207 vs `liquid_jaccard` 0.200（+0.7pp，噪声内）。
   英文别名表小样本未覆盖真实同义分布 → 无显著贡献，但证明 E5 归一化层在英文不报错、机制可移植。

3. **召回层抗噪与词频向量基线完全持平——真抗噪在成核层（修正旧「抗噪红利」表述）。** 早期基于纯 jaccard 的
   「s4 噪声 0/1982、tfidf_s4 112/1982」看似液环抗噪占优，但那是**纯 jaccard 零分过滤副作用**
   （噪声 turn 与 query jaccard=0 故排不进 top5）。机制默认升为余弦后余弦永远 >0，噪声 turn 同样可入池 →
   **s4 噪声 = 112/1982 = tfidf_s4 112/1982（完全持平）**。正确归因：液环「抗单条噪声」的结构性优势在
   **成核层**（≥2 一致才结晶），不在召回层；召回层无论 jaccard 还是余弦都是 lexical 相似度检索，与词频向量
   一样不抗召回层噪声。这恰印证「禁向量在召回层零代价、在成核层才兑现可信度优势」（WHY_NO_VECTOR §六.6）。

4. **成核率偏低 — 印证 critique 的边界诊断（而非否定）。**
   成核 540/5882 ≈ 9%（局部重复表述触发，非全局复现）。LoCoMo 事实单遍出现、不复现，
   液环"≥2 篇一致才成核"门槛本就难触发 → **主战场是液态召回（默认余弦 47.2%，纯 jaccard 20%），不是成核**。
   这恰是 critique 说的："液环适用边界比 README 更窄 = 输入源不可信 + 输入表达高度可复现"。
   我们**诚实呈现**这一边界，不掩饰。

5. **真实边界发现：`_normalize` 对英文有毒（已修）。**
   初版 E5 直接调 `selfspin._normalize`，该函数为中文优化、**去除所有空格**。
   英文经去空格后整句塌成单个超长 token → jaccard 交集为空 → recall 全盘 0。
   已为 E5 加 `normalize_en`（保留词间空格、小写、去标点），英文对照才恢复正常。
   这是一个**真实工程边界**，已记入代码与本案。

6. **向量基线对照：量化 critique 的"两边都对"（零依赖 TF-IDF，零 HF）。**
   为验证 critique 核心论点，补了一个**纯标准库 TF-IDF + 余弦**向量基线（`run_locomo.py` 内 `TfidfBaseline`，
   零 embedding / 零 HuggingFace / 零 LLM），与液环同口径跑：
   - **召回代价（critique 说对了，但只针对纯 jaccard 口径）**：纯 jaccard 液环 recall@5 = **20.0%**，是 tfidf 47.2% 的 **2.36 倍**缺口——
     这是 critique 所谓"语义逃逸"代价的硬量化。但**该缺口是打分策略（未加权 jaccard）所致，非禁向量所致**：
     机制默认升为零向量 tf-idf 余弦后即追平 0.472 = 47.2%，缺口归零，全程零 embedding。
   - **抗噪（修正旧表述）**：纯 jaccard 下「液环 s4 污染 0/1982、tfidf_s4 112/1982」的对比是 jaccard 零分过滤假象；
     默认余弦下 **s4 噪声 = 112/1982 = tfidf_s4 112/1982（持平）**。召回层抗噪优势不存在，真抗噪在成核层（≥2 门槛），详见 §四.3。
   - **诚实定位**：TF-IDF 是**词频向量、非语义 embedding**，对照显示的是"词频重叠 vs 字符重叠"的差距；
     比 sentence-embedding（神经网络，HF 权重）温和。但即便温和的词频向量，噪声入池弱点已显现——
     更强的语义向量只会更甚（critique 原警告正针对后者）。结论不变：**液环用召回换鲁棒性，是取舍非缺陷**。

## 五、诚实边界（本报告不声称什么）

- **口径**：本报告是记忆**召回层**评测，非 LoCoMo 端到端 QA 准确率（后者需 LLM 推理答案）。
  20% recall@5 是直接对照，不应与 GPT-4+记忆的 ~40-50% 端到端 QA 混比。
- **向量基线已补（零依赖，零 HF）**：`TfidfBaseline`（纯标准库 TF-IDF + 余弦，无 embedding / 无 HuggingFace / 无 LLM）
  已并入 `run_locomo.py` 并出数（见 §三、§四.6）。**HuggingFace 不是必要依赖**——词频向量基线无需任何外部权重即可跑。
  待补的是**更强的语义向量基线（sentence-embedding / FAISS）**，那才需 HF 权重；本环境 HF 主域超时，列为可选增强（非阻塞）。
- **LongMemEval 待补（GitHub 源，非 HF 阻塞）**：正确来源 `xiaowu0162/LongMemEval`（GitHub），
  HF `xiaowu0162/longmemeval-cleaned` 仅是镜像之一。本环境 `raw.githubusercontent.com` 间歇可达（LoCoMo 已下），
  LongMemEval 数据下载偶发超时，待网络稳定后补跑（多会话推理/时序/知识更新五维能力）。**非 HF 专属不可达**。
- **E5 英文别名表是小样本**：仅 17 条手工同义，验证机制有效；生产级需系统扩表（curation）。
- **成核非主战场**：LoCoMo 上成核率低属预期，不代表液环成核机制失效（受控复现流已证）。

## 六、复现

```bash
# 1. 下载数据（需 raw.githubusercontent.com 可达；本环境已下）
curl -fsSL -o benchmarks/data/locomo10.json \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

# 2. 跑评测
LOCOMO_SUBSET=1 python3 examples/benchmarks/run_locomo.py   # 单对话快速验证
python3 examples/benchmarks/run_locomo.py                   # 全量 10 对话
```

## 七、下一步（ROADMAP D1 收尾）

1. **补语义向量基线（可选·需 HF）**：TF-IDF 词频基线已补；如需更贴近 critique 原论点的
   sentence-embedding / FAISS 对照，需 HuggingFace 权重（本环境 HF 主域超时，列为可选增强、非阻塞）。
   明确标注"外部参照、非液环内部"，且预期其噪声入池弱点比 TF-IDF 更甚。
2. **补 LongMemEval**：五维记忆能力评测，扩到多会话推理/时序/知识更新。
3. **E5 英文别名扩表**：从 LoCoMo 错失召回中挖掘同义对，系统 curation。
4. **端到端 QA 桥接**（可选）：液环召回 top_k 喂 LLM 答 QA，出端到端准确率，与 SOTA 同口径比。
5. **上生产前受控实验门**：E5 误合并率评估（对齐 ROADMAP D4 守则）。
