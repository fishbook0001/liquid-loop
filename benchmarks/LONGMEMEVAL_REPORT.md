# LongMemEval 基准报告 · 液环存活度（诚实版）

> 数据：HuggingFace `xiaowu0162/longmemeval-cleaned` → `longmemeval_s_cleaned.json`（500 题 / 264.5MB）
> 经 `hf-mirror.com` 下载（huggingface.co 主域本环境 rc=000 不可达）。
> 运行：`examples/benchmarks/run_longmemeval.py`（每题独立记忆库，session-level recall@5）
> 日期：2026-08-25

## 一、结果（全量 500 题 / 246,738 条液态记忆）

| 模式 | recall@5 | hits | 噪声入 top5 | 成核数 |
|---|---|---|---|---|
| none（无记忆） | 0.000 | 0 | — | — |
| **liquid（液环）** | **0.354** | 177 | — | 1426 |
| liquid+E5 | 0.346 | 173 | — | 1653 |
| s4（+20% 噪声） | 0.352 | 176 | **9** | 1426 |
| tfidf（零依赖向量基线） | **0.942** | 471 | — | — |
| tfidf_s4（+噪声） | 0.942 | 471 | **1** | — |

## 二、诚实结论

### 结论 1：critique 核心断言被量化坐实——lexical 召回 ≈ 向量基线的 1/2.7
跨两基准一致：
- LoCoMo：liquid 0.200 vs tfidf 0.472（**2.36x**）
- LongMemEval：liquid 0.354 vs tfidf 0.942（**2.66x**）

根因（q1 单题深挖确认，非 bug）：
gold turn *"I graduated with a degree in Business Administration..."* 与问题
*"What degree did I graduate with?"* 的 jaccard 仅 **0.077**；池中另一个无关 turn 因巧合
token 重叠得 0.133，挤掉 gold。液环用**无 IDF 加权的纯 jaccard**，稀有关键词
（degree/graduate）被长句稀释 → top5 漏掉。TF-IDF 因 **IDF 加权同一批词**而命中。

### 结论 2：抗噪红利是「有条件的」——修正 LoCoMo 报告表述
| 基准 | s4 噪声入 top5 | tfidf_s4 噪声入 top5 | 胜方 |
|---|---|---|---|
| LoCoMo | 0 | 112 | 液环 |
| LongMemEval | **9** | **1** | TF-IDF |

机制：当查询本身 lexical 信号强（LoCoMo 多事实题），零分候选少，噪声 turn 无法挤入；
当查询弱（LongMemEval 多 paraphrase / temporal 题），大量真实 turn 与问题 jaccard≈0，
噪声 turn 凭**零分平局**进入 top5。故「抗噪红利」是相对优势，取决于查询 lexical 强度，
**不能当普适结论**。BENCHMARK_REPORT.md 原「s4 噪声 0」表述需加此限定。

### 结论 3：缺口是 IDF 加权，不是「需要向量」——可无向量补
TF-IDF 的全部优势来自对**同一批词**做 IDF 加权（仍是纯词袋、零 embedding、零向量）。
给液环液态召回加 **IDF 加权 lexical 打分**（机制层零向量）即可收复大部分 35%→94% 缺口。
此改动与 WHY_NO_VECTOR.md 红线自洽（机制层仍无 embedding / 无向量），是下一步正路。

### 结论 4：E5 英文别名表在当前样本下无效甚至略负
liquid 0.354 → liquid+E5 0.346（173<177）。原因：`benchmarks/aliases_en.json` 是玩具级
小样本，英文归一化反而引入噪声，未覆盖 LongMemEval 的真实同义分布。需真实英文同义覆盖
才有意义（LoCoMo 上 E5 曾 +0.7pp，两基准信号不一致，均微小，结论：E5 当前不做出贡献）。

## 三、成核数解读
1426 个 canonical（liquid）/ 1653（E5），相对 246,738 条事实极低——符合 critique「单遍
会话流无重复→成核率趋近 0」边界。但 1426 非 0：LongMemEval 各会话含大量重复 boilerplate
（如 "Hello, how can I help you?" 跨会话出现），selfspin 正确地将其去重成核。这印证
selfspin 的真实功能是**跨会话去重/归一**，而非单会话内「≥2 一致成核」。

## 四、与禁向量红线的一致性
- 液环内部机制：纯 jaccard / 倒排，零 embedding、零向量、零 LLM 推断（已确认 selfspin、
  workspace 自 d136731 未引入任何向量依赖）。
- TfidfBaseline 是**评测对照基线**（纯标准库 `re`/`math`，零 HF / 零 embedding），与
  WHY_NO_VECTOR.md 自建的 V1/V2/V3 向量 baseline 同性质，物理隔离于 `examples/`，
  不进入 `liquid_loop/` 包 → 不破红线。

## 五、下一步（建议，待 飞哥确认）
1. 机制层加 **IDF 加权 jaccard 召回**（零向量），预期 liquid 35%→接近 90%+。
2. 真实英文同义别名表替换玩具样本（否则 E5 不贡献）。
3. 抗噪：零分平局时加确定性 tie-break（如噪声 turn 标记降权），补强弱查询下的抗噪。
