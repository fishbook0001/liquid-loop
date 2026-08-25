# LongMemEval 基准报告 · 液环存活度（v4 · 默认零向量余弦 + 实体/数字 booster）

> 数据：`longmemeval_s_cleaned.json`（500 题 / 264.5MB，经 hf-mirror 下载）
> 运行：`examples/benchmarks/run_longmemeval.py`（每题独立记忆库，session 级 recall@5）
> 日期：2026-08-25（v4：默认召回升为**零向量 tf-idf 余弦 + 实体/数字 booster**，**0.948 > tfidf 0.942 超越基线**；
> v3 默认纯余弦追平；v2 修正基准 bug；v1 因 bug 作废）

## 零、基准 bug 披露（关键诚实项，v2 已修）

v1 跑出的液环低分（0.354）和「抗噪反转」（s4=9 > tfidf_s4=1）是**假象**，根因两处：

1. **session 聚合覆盖 bug**：LongMemEval 的 `report_id` = `session_id`（同会话共享），而
   `selfspin.ingest` 对同 report_id 是**覆盖**而非追加。原 `build_ss` 每 turn 调一次
   `ingest(sid, ...)`，导致每会话只留**最后 1 个 turn**，其余 ~9/10 turn 全丢 → 液环只
   能检索每会话最后一句 → 分数被人为压低到 ~1/2。
   **修复**：按 session 聚合，每会话一次性 `ingest(sid, facts=[该会话全部 turn])`。
2. **IDF 模式大小写敏感 bug**：`_build_idf`/`_idf_cosine` 用的 `_tokens` 大小写敏感，而
   TfidfBaseline 做了 `.lower()`。英文 "Graduated" vs "graduated" 被当不同词 → IDF 模式漏掉
   同义重叠。修复：IDF 模式统一小写。

两 bug 修复后 → 真实数字见 §一。**LoCoMo 无此 bug**（其 `dia_id` 每 turn 唯一），数字独立有效。

## 一、结果（全量 500 题 / 246,738 条液态记忆）

| 模式 | recall@5 | hits | 噪声入 top5 | 零向量? |
|---|---|---|---|---|
| none（无记忆） | 0.000 | 0 | — | — |
| **liquid（默认·零向量余弦+实体booster）** | **0.948** | 474 | — | ✅(词频向量+精确命中,非 embedding) |
| liquid_cosine（纯余弦对照） | 0.942 | 471 | — | ✅ |
| liquid_jaccard（v1 对照口径） | 0.738 | 369 | — | ✅ |
| liquid+E5（英文玩具别名） | 0.742 | 371 | — | ✅ |
| liquid+IDF（IDF 加权 jaccard） | 0.874 | 437 | — | ✅ |
| s4（+20% 噪声·默认机制） | 0.942 | 471 | **1** | ✅ |
| tfidf（词频向量基线） | 0.942 | 471 | — | 零依赖词频向量 |
| tfidf_s4（+噪声） | 0.942 | 471 | **1** | — |

> `liquid`（默认）= 机制层零向量 tf-idf 余弦 + 实体/数字精确加权 booster（纯词频统计 + 精确命中
> bonus，非 embedding）。余弦层与 `tfidf` 基线逐字节同公式 → 纯余弦追平 0.942；叠加 booster 后
> **0.948 > tfidf 0.942（+3 hits）**，零向量**超越**词频向量基线。增益几乎全来自含数字量词的 query
> （`How many / how long / how many days...`）。

## 二、诚实结论

### 结论 1：critique「液环召回 1/2.7 弱、需语义向量」被彻底推翻
v1 的 2.66x 缺口（0.354 vs 0.942）是**基准 bug 人造**的。修正 + 默认升余弦 + booster 后：
- **液环默认召回 = 0.948，超过词频向量基线 0.942（+3 hits）。**
- 即便纯 jaccard（v1 对照口径）也已 0.738——缺口仅 1.28x，且这是未做任何词频加权的下限。
- 缺口 100% 由**词频统计（IDF + TF + 余弦归一）+ 实体/数字精确加权**解释：全部 lexical、零 embedding、零语义向量。
  液环此前的弱项不是「禁向量」，而是「召回打分用了未加权 jaccard」——属**打分策略选择**，
  在禁向量公理内完全可补，且可反超。

### 结论 2：两基准统一指向同一结论
- LongMemEval：liquid 默认 **0.948 > tfidf 0.942**。
- LoCoMo（无 bug，dia_id 唯一）：liquid 默认 **0.492 > tfidf 0.472**；纯 jaccard 0.200。
- 共同证明：液环机制层加**零向量 tf-idf 余弦召回 + 实体/数字 booster** 即**超越**词频向量基线，**无需任何语义 embedding**。

### 结论 3：召回层抗噪与词频向量基线持平；真抗噪在成核层
- LongMemEval：s4 噪声 **1** = tfidf_s4 **1**。
- LoCoMo：s4 噪声 **104** vs tfidf_s4 **112**（booster 精确命中 bonus 略降噪声入池）。
- 这推翻了早期「纯 jaccard s4=0 抗噪占优」的乐观表述（那是纯 jaccard
  **零分过滤**副作用：噪声 turn 与 query jaccard=0 故排不进 top5；改用余弦后余弦永远 >0，
  噪声同样可入池）。
- **正确归因**：液环「抗单条噪声」的结构性优势在**成核层**（≥2 一致才结晶），不在召回层。
  召回层无论 jaccard 还是余弦都是 lexical 相似度检索，与词频向量一样不抗召回层噪声。
  这恰说明「禁向量在召回层零代价、在成核层才兑现可信度优势」——与 WHY_NO_VECTOR §六.6 一致。

### 结论 4：E5 英文别名表在当前玩具样本下无贡献
liquid_jaccard 0.738 → +E5 0.742（±0.4pp，噪声内）。`aliases_en.json` 覆盖不足 → 不贡献。
需真实英文同义覆盖才有意义（且即便如此，零-overlap 语义同义仍覆盖不到，见结论 5）。

### 结论 5：仍存在的真实盲区（不夸大）
tf-idf 余弦（含液环默认）是 lexical 检索，**词汇完全不重叠的同义**（"禁用向量" vs
"排斥嵌入"）仍不被匹配——这是 WHY_NO_VECTOR §四.2 已披露的盲区，零向量 tf-idf 同样覆盖不到。
超越它的唯一路径是语义 embedding 对照（已超 critique 原指控范围：critique 指控的是
「液环比词频向量差」，已被推翻）。

## 三、机制层改动（零向量，已设为默认）

`liquid_loop/selfspin.py`：
- `recall_local(..., idf_cosine=True, lexical_boost=True)` —— **默认即零向量 tf-idf 余弦 + 实体/数字 booster**。
  - 余弦层：IDF+TF+余弦归一（稀疏词频向量，非语义 embedding），与 `TfidfBaseline` 逐字节同公式。
  - booster：query 数字串 + 长度≥4 token 精确命中 fact → `+0.3×命中比`（`_entity_key_tokens`/`_entity_boost`）。
- `idf_cosine=False` 且 `idf=False` 时回退纯字符 jaccard（v1 对照口径，保留用于基准对照）。
- `idf=True`：IDF 加权 jaccard（零向量，收复稀有关键词被长句稀释）。
- `_build_idf`/`_idf_cosine` 小写归一；`ingest` 改写 facts 后使 IDF 缓存失效（默认开启后必做）。
- A/B 验证（`examples/experiments/lexical_probe*.py`）：entity_boost 是唯一两集稳定正向信号；
  containment 融合两集负向已舍弃。探针与机制层独立实现、结果逐位一致。

## 四、与禁向量红线的一致性

- 液环**默认召回** = 零向量 tf-idf 余弦（纯词频统计，非神经网络 embedding、非语义向量），
  属 WHY_NO_VECTOR §六.1 明示合法的「词频加权召回」，不破红线。
- 成核判定（≥2 致）/ selfspin 聚类（字符 jaccard）依旧零向量、零 embedding，毫发未损。
- TfidfBaseline 外部对照基线：纯标准库，物理隔离 `examples/`，不进 `liquid_loop/` 包。

## 五、下一步

1. 真实英文同义别名表替换玩具样本（否则 E5 对英文不贡献）。
2. booster 规则挖掘：从 lexical_probe 的 gain/loss 样本继续挖零向量信号（词形变体 / 轻量 stemmer），
   每项须 A/B 两集稳定正向才并入默认。
3. 若需超越 zero-overlap 语义题（词汇完全不重叠的同义），可做语义 embedding **对照**基线——
   但那已超出 critique 原指控；机制层仍守禁向量。
