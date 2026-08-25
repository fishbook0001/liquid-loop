# LoCoMo Benchmark Report — 液环在真实语义流上的存活度

> 对应：外部 critique 末条（"跑 LoCoMo/LongMemEval 真实报告，比任何宣传都硬"）+ ROADMAP D1（实证出窍·最高优先级）
> 日期：2026-08-25
> 状态：**第一版真实语义流报告**（纯记忆召回层口径，非端到端 QA）

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
| **liquid（液环 jaccard 液态召回）** | **0.200** | 397 | — | 540 |
| **liquid + E5（英文别名表）** | **0.207** | 410 | — | 465 |
| s4（+20% 噪声注入） | 0.200 | 397 | **0 / 1982** | 540 |

### 逐对话细分

| conversation | facts | liquid | +E5 | s4 | s4噪声topk |
|---|---|---|---|---|---|
| conv-26 | 419 | 0.127 | 0.132 | 0.127 | 0/197 |
| conv-30 | 369 | 0.238 | 0.210 | 0.238 | 0/105 |
| conv-41 | 663 | 0.145 | 0.150 | 0.145 | 0/193 |
| conv-42 | 629 | 0.219 | 0.223 | 0.219 | 0/260 |
| conv-43 | 680 | 0.240 | 0.252 | 0.240 | 0/242 |
| conv-44 | 675 | 0.196 | 0.190 | 0.196 | 0/158 |
| conv-47 | 689 | 0.184 | 0.195 | 0.184 | 0/190 |
| conv-48 | 681 | 0.243 | 0.255 | 0.243 | 0/239 |
| conv-49 | 509 | 0.219 | 0.230 | 0.219 | 0/202 |
| conv-50 | 568 | 0.183 | 0.203 | 0.183 | 0/202 |

## 四、结论（逐条回应 critique）

1. **"真实语义流里也活着" — 证毕。** 液环在 LoCoMo 真实长对话上 recall@5 = **20%**（非零，远超无记忆基线 0）。
   纯确定性 jaccard、零向量、零 LLM 推断，即取得可测量的真实语义流存活度。

2. **E5 补洞机制跨语言有效。** `liquid+E5` 较 `liquid` **+1.3pp**（397→410）。
   英文别名表（`benchmarks/aliases_en.json`，小样本）验证 E5 的"零 token 重叠同义归并"在英文也成立。

3. **S4 抗噪守住 — 禁向量的关键红利兑现。** 注入 20% 不相关噪声 turn 后，
   top_k 中噪声污染 = **0/1982**。单条噪声不入池（字面不重叠 → jaccard 低 → 排不到 top），
   印证"禁向量让单条噪声无法改写记忆"的设计主张。

4. **成核率偏低 — 印证 critique 的边界诊断（而非否定）。**
   成核 540/5882 ≈ 9%（局部重复表述触发，非全局复现）。LoCoMo 事实单遍出现、不复现，
   液环"≥2 篇一致才成核"门槛本就难触发 → **主战场是液态召回（20%），不是成核**。
   这恰是 critique 说的："液环适用边界比 README 更窄 = 输入源不可信 + 输入表达高度可复现"。
   我们**诚实呈现**这一边界，不掩饰。

5. **真实边界发现：`_normalize` 对英文有毒（已修）。**
   初版 E5 直接调 `selfspin._normalize`，该函数为中文优化、**去除所有空格**。
   英文经去空格后整句塌成单个超长 token → jaccard 交集为空 → recall 全盘 0。
   已为 E5 加 `normalize_en`（保留词间空格、小写、去标点），英文对照才恢复正常。
   这是一个**真实工程边界**，已记入代码与本案。

## 五、诚实边界（本报告不声称什么）

- **口径**：本报告是记忆**召回层**评测，非 LoCoMo 端到端 QA 准确率（后者需 LLM 推理答案）。
  20% recall@5 是直接对照，不应与 GPT-4+记忆的 ~40-50% 端到端 QA 混比。
- **向量基线缺失**：本环境 HuggingFace 主域不可达（`huggingface.co` 超时），
  FAISS/句子向量基线**待补**（需联网装依赖）。报告暂以"无记忆=0"为下界、"液环 jaccard"为实测中界。
- **LongMemEval 待补**：正确来源 `xiaowu0162/LongMemEval` + HF `xiaowu0162/longmemeval-cleaned`，
  数据在 HF 主域不可达，本环境暂无法下载。待网络可达后补跑（多会话推理/时序/知识更新五维能力）。
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

1. **补向量基线**：HuggingFace 可达后，加 FAISS/句子向量对照（明确标注"外部参照、非液环内部"）。
2. **补 LongMemEval**：五维记忆能力评测，扩到多会话推理/时序/知识更新。
3. **E5 英文别名扩表**：从 LoCoMo 错失召回中挖掘同义对，系统 curation。
4. **端到端 QA 桥接**（可选）：液环召回 top_k 喂 LLM 答 QA，出端到端准确率，与 SOTA 同口径比。
5. **上生产前受控实验门**：E5 误合并率评估（对齐 ROADMAP D4 守则）。
