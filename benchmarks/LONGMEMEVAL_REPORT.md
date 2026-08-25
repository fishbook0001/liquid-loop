# LongMemEval 基准报告 · 液环存活度（v2 修正版 · 诚实）

> 数据：`longmemeval_s_cleaned.json`（500 题 / 264.5MB，经 hf-mirror 下载）
> 运行：`examples/benchmarks/run_longmemeval.py`（每题独立记忆库，session 级 recall@5）
> 日期：2026-08-25（v2 修正：发现并修复基准 bug，数字全部重测）
> ⚠️ v1 报告（liquid=0.354 / s4 噪声=9）因**基准 bug 全部作废**，见 §零。

## 零、基准 bug 披露（关键诚实项）

v1 跑出的液环低分（0.354）和「抗噪反转」（s4=9 > tfidf_s4=1）是**假象**，根因两处：

1. **session 聚合覆盖 bug**：LongMemEval 的 `report_id` = `session_id`（同会话共享），而
   `selfspin.ingest` 对同 report_id 是**覆盖**而非追加。原 `build_ss` 每 turn 调一次
   `ingest(sid, ...)`，导致每会话只留**最后 1 个 turn**，其余 ~9/10 turn 全丢 → 液环只
   能检索每会话最后一句 → 分数被人为压低到 ~1/2。
   **修复**：按 session 聚合，每会话一次性 `ingest(sid, facts=[该会话全部 turn])`。
   修复后每会话全部 turn 可检索，session 级 recall 才正确。
2. **IDF 模式大小写敏感 bug**：`_build_idf`/`_idf_cosine` 用的 `_tokens` 大小写敏感，而
   TfidfBaseline 做了 `.lower()`。英文 "Graduated" vs "graduated" 被当不同词 → IDF 模式
   漏掉同义重叠。修复：IDF 模式统一小写（与 tfidf 同口径）。

两 bug 修复后重跑 → 真实数字见 §一。**LoCoMo 无 bug**（其 `dia_id` 每 turn 唯一），数字有效。

## 一、结果（全量 500 题 / 246,738 条液态记忆，修正后）

| 模式 | recall@5 | hits | 噪声入 top5 | 零向量? |
|---|---|---|---|---|
| none（无记忆） | 0.000 | 0 | — | — |
| **liquid（纯 jaccard）** | **0.738** | 369 | — | ✅ |
| liquid+E5 | 0.742 | 371 | — | ✅ |
| liquid+IDF（IDF 加权 jaccard） | 0.874 | 437 | — | ✅ |
| **liquid+IDFcos（tf-idf 余弦）** | **0.942** | 471 | — | ✅(稀疏词频向量,非 embedding) |
| s4（+20% 噪声） | 0.738 | 369 | **1** | ✅ |
| tfidf（向量基线） | 0.942 | 471 | — | 零依赖词频向量 |
| tfidf_s4（+噪声） | 0.942 | 471 | **1** | — |

## 二、诚实结论（推翻 v1 / 反驳 critique 的「召回 1/2.7 弱」）

### 结论 1：critique 的「液环召回 1/2.7 弱、需语义向量」被推翻
v1 的 2.66x 缺口（0.354 vs 0.942）是**基准 bug 人造**的。修正后：
- 液环纯 jaccard 已 **0.738**（缺口仅 1.28x，且这 0.738 是未做任何词频加权的下限）。
- **液环零向量 tf-idf 余弦 = 0.942，与 tfidf 基线精确追平。**
- 缺口 100% 由**词频统计（IDF + TF + 余弦归一）**解释 —— 全部是 lexical、零 embedding、
  零语义向量。即：液环此前的弱项不是「禁向量」，而是「召回打分用了未加权 jaccard」，
  属**打分策略选择**，在禁向量公理内可完全补上。

### 结论 2：LoCoMo 与 LongMemEval 统一指向同一结论
LoCoMo（无 bug，dia_id 唯一）真实数：liquid 0.200 → +IDFcos 0.454 ≈ tfidf 0.472（96% 追平）。
LongMemEval 修正后：liquid 0.738 → +IDFcos 0.942 = tfidf。
两基准共同证明：给液环机制层加**零向量 tf-idf 余弦召回**，即达词频向量基线水平，
**无需任何语义 embedding / 神经网络向量**。

### 结论 3：抗噪在两基准表现不同（取消「抗噪红利」普适声称）
- LoCoMo：s4 噪声 0 vs tfidf_s4 112 → 液环抗噪占优（查询 lexical 信号强时）。
- LongMemEval（修正后）：s4 噪声 **1** vs tfidf_s4 **1** → 两者**持平**（v1 的 s4=9 是 bug 假象）。
- 修正后诚实表述：**抗噪优劣取决于查询 lexical 强度与具体基准，非液环普适红利**。

### 结论 4：E5 英文别名表在当前样本下无贡献
liquid 0.738 → liquid+E5 0.742（±0.4pp，噪声内）。`aliases_en.json` 是玩具小样本，
英文归一化未覆盖真实同义分布 → 不贡献。需真实英文同义覆盖才有意义。

## 三、机制层改动（零向量，默认关闭）
`liquid_loop/selfspin.py`：
- `recall_local(..., idf=False, idf_cosine=False)` 新增两零向量模式：
  - `idf=True`：IDF 加权 jaccard（收复稀有关键词被长句稀释）。
  - `idf_cosine=True`：完整 tf-idf 余弦（与 TfidfBaseline 逐字节同口径，**稀疏词频向量，
    非语义 embedding**）—— 追平 tfidf 基线用。
- `_build_idf` / `_idf_jaccard` / `_idf_cosine` 均小写归一（英文正确）。
- 默认 `idf=False` → 既有纯 jaccard 行为不变，历史基准可比。

## 四、与禁向量红线的一致性
- 液环默认机制：纯 jaccard，零 embedding、零向量、零 LLM。
- IDF / tf-idf 余弦模式：稀疏词频向量（IDF+TF 标量权重），**非神经网络 embedding、非语义
  向量**，与 WHY_NO_VECTOR 允许的「词频向量对照」同性质；是否纳入机制层默认由飞哥裁定。
- TfidfBaseline 外部对照基线：纯标准库，物理隔离 `examples/`，不进 `liquid_loop/` 包。

## 五、下一步（待飞哥裁定）
1. 是否把 `idf_cosine=True` 设为液环召回默认（零向量、达词频向量基线）？
2. 真实英文同义别名表替换玩具样本（否则 E5 不贡献）。
3. 若需超越词频向量基线（真正 zero-overlap 语义题），那才需考虑语义 embedding 对照——
   但那已超出 critique 原指控范围（critique 指控的是「液环比词频向量差」，已被推翻）。
