#!/usr/bin/env python3
"""run_locomo.py — 液环在 LoCoMo 真实语义流上的「存活度」评测

设计哲学（对齐 critique 末条 + ROADMAP D1）：
  LoCoMo 是 10 段超长真实双人对话（Reddit 风格），事实在流中**单遍出现**，
  不会像受控实验那样重复两次 → 液环的「≥2 篇一致才成核」门槛本就趋近 0 成核率。
  这**正好印证 critique 的核心边界诊断**：液环的确定性强在「重复可复现输入」。

  因此本评测的主战场不是「成核率」，而是**液态层召回（liquid recall）**——
  即把每条对话 turn 作为未结晶的液态记忆存入，用 qa.question 召回，
  看是否命中 qa.evidence 标注的 dialog id。这才是「真实语义流里也活着」的公平度量。

指标：
  - recall@K 命中率：top_k 召回中是否含任一标注 evidence dialog id
  - 成核数：local_rotate 产出的≥2 篇支持 canonical 数（预期极低，作边界证据）
  - S4 污染率：注入噪声 turn 后，top_k 中噪声占比（抗噪不退化度量）

公理纪律（液环**内部机制**）：纯 jaccard / 倒排，零 embedding、零向量、零 LLM 推断。
  selfspin / 成核 / 液态召回全程零向量 —— 这是 WHY_NO_VECTOR 的禁向量红线所在层。
  TfidfBaseline 是**外部评测对照基线**（非液环内部机制），与 WHY_NO_VECTOR.md 自身建的
  V1/V2/V3 向量 baseline 同性质；它量化"若用语义向量检索会怎样"，不进入 liquid_loop/ 包，
  故**不破禁向量红线**（红线在机制层，不在评测对照层）。
基线对照：none(无记忆=0) / liquid(液环 jaccard) / liquid_e5(液环+英文别名表) /
          s4(液环+20%噪声) / tfidf(零依赖TF-IDF向量基线·外部对照) / tfidf_s4(向量基线+20%噪声)

用法：
  LOCOMO_SUBSET=1 python3 examples/benchmarks/run_locomo.py   # 单对话快速验证管线
  python3 examples/benchmarks/run_locomo.py                   # 全量 10 对话
"""
import sys
import os
import json
import re
import math
import random

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "examples", "faithful"))

from liquid_loop.selfspin import LiquidSelfSpin
import E5_alias_table as E5  # AliasTable（零 embedding / 零 LLM）

DATA = os.path.join(REPO_ROOT, "benchmarks", "data", "locomo10.json")
ALIAS_EN = os.path.join(REPO_ROOT, "benchmarks", "aliases_en.json")
TOP_K = 5
FAST_J = 0.60


def collect_turns(conv: dict) -> list:
    """展平一个对话的所有 session/turn → [(dia_id, text), ...]"""
    turns = []
    for k, v in conv.items():
        if not (k.startswith("session_") and isinstance(v, list)):
            continue
        if k.endswith(("date_time", "_observation", "_summary")):
            continue
        for t in v:
            if isinstance(t, dict) and t.get("text"):
                turns.append((t["dia_id"], t["text"]))
    return turns


def build_ss(turns: list, alias=None):
    """把每条 turn 当一条液态 fact 灌入 selfspin（report_id=dia_id，直接映射 evidence）。"""
    ss = LiquidSelfSpin(run_id="locomo", fast_jaccard=FAST_J)
    ss.liquid_persist = False  # 评测纯内存跑，关跨会话液态持久化（避免 O(n^2) 文件写开销）
    for dia_id, text in turns:
        # 英文语料用 normalize_en（保留词间空格；normalize 中文式去空格会使 jaccard 塌缩）
        norm = alias.normalize_en(text) if alias else text
        ss.ingest(dia_id, "", facts=[norm])
    nuclei = ss.local_rotate()
    return ss, nuclei


def eval_recall(ss, qa_items: list, alias=None, idf: bool = False, idf_cosine: bool = False,
                lexical_boost: bool = False):
    """返回 (命中数, 总数, 噪声污染命中数)。命中=top_k 含任一标注 evidence dialog id。
    alias 启用时 query 与 corpus 须同源归一化（否则单边替换破坏匹配）。
    idf=True 启用零向量 IDF 加权 jaccard 召回；idf_cosine=True 启用零向量 IDF 加权余弦。"""
    hits, total, noise_hits = 0, 0, 0
    for q in qa_items:
        ev = q.get("evidence") or []
        if not ev:
            continue
        total += 1
        qtext = alias.normalize_en(q["question"]) if alias else q["question"]
        res = ss.recall_local(qtext, top_k=TOP_K, liquid=False, idf=idf, idf_cosine=idf_cosine,
                              lexical_boost=lexical_boost)
        polluted = False
        for r in res:
            rid = r["report_id"]
            if rid.startswith("N"):  # 噪声 turn 标记
                polluted = True
            if rid in ev:
                hits += 1
                break
        if polluted:
            noise_hits += 1
    return hits, total, noise_hits


def run_conv(conv: dict, qa_items: list, alias=None, noise: float = 0.0, seed: int = 0,
             idf: bool = False, idf_cosine: bool = False, lexical_boost: bool = False):
    turns = collect_turns(conv)
    if noise > 0:
        rng = random.Random(seed)
        n = int(len(turns) * noise)
        for i in range(n):
            # 噪声：含独特 token，字面与任何 question 不重叠 → 若进 top_k 即污染
            turns.append((f"N{i}", f"zxqw noise token {i} kjpw unrelated vytr"))
    ss, nuclei = build_ss(turns, alias)
    hits, total, noise_hits = eval_recall(ss, qa_items, alias=alias, idf=idf, idf_cosine=idf_cosine,
                                          lexical_boost=lexical_boost)
    return {
        "hit": hits, "total": total, "rate": hits / total if total else 0,
        "nucleated": len(nuclei) if nuclei else 0, "facts": len(turns),
        "noise_hits": noise_hits,
    }


class TfidfBaseline:
    """零依赖 TF-IDF + 余弦向量基线（纯标准库，零 embedding / 零 HF / 零 LLM）。
    用于对照：向量检索范式下「单条噪声是否入池」——这正是 critique 指出的向量弱点。"""
    def __init__(self, ids: list, docs: list):
        self.ids = ids
        self.tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())
        df = {}
        self.doc_tok = [self.tok(d) for d in docs]
        for toks in self.doc_tok:
            for w in set(toks):
                df[w] = df.get(w, 0) + 1
        n = len(docs)
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}
        self.vecs = []
        for toks in self.doc_tok:
            tf = {}
            for w in toks:
                tf[w] = tf.get(w, 0) + 1
            v = {}
            norm = 0.0
            for w, c in tf.items():
                val = (1 + math.log(c)) * self.idf.get(w, 1.0)
                v[w] = val
                norm += val * val
            self.vecs.append((v, math.sqrt(norm) or 1.0))

    def query(self, q: str, k: int = 5):
        tf = {}
        for w in self.tok(q):
            tf[w] = tf.get(w, 0) + 1
        qv = {}
        qn = 0.0
        for w, c in tf.items():
            val = (1 + math.log(c)) * self.idf.get(w, 1.0)
            qv[w] = val
            qn += val * val
        qn = math.sqrt(qn) or 1.0
        scored = []
        for i, (v, norm) in enumerate(self.vecs):
            dot = 0.0
            if len(qv) <= len(v):
                for w, val in qv.items():
                    if w in v:
                        dot += val * v[w]
            else:
                for w, val in v.items():
                    if w in qv:
                        dot += qv[w] * val
            cos = dot / (qn * norm) if norm > 0 else 0.0
            scored.append((i, cos))
        scored.sort(key=lambda x: -x[1])
        return [(self.ids[i], cos) for i, cos in scored[:k]]


def run_tfidf(conv: dict, qa_items: list, noise: float = 0.0, seed: int = 0):
    """TF-IDF 向量基线跑单对话（与 liquid 同口径：turn→记忆，question→召回）。"""
    turns = collect_turns(conv)
    if noise > 0:
        rng = random.Random(seed)
        n = int(len(turns) * noise)
        for i in range(n):
            turns.append((f"N{i}", f"zxqw noise token {i} kjpw unrelated vytr"))
    ids = [t[0] for t in turns]
    docs = [t[1] for t in turns]
    idx = TfidfBaseline(ids, docs)
    hits, total, noise_hits = 0, 0, 0
    for q in qa_items:
        ev = q.get("evidence") or []
        if not ev:
            continue
        total += 1
        top = idx.query(q["question"], TOP_K)
        polluted = False
        for rid, _ in top:
            if rid.startswith("N"):
                polluted = True
            if rid in ev:
                hits += 1
                break
        if polluted:
            noise_hits += 1
    return {
        "hit": hits, "total": total, "rate": hits / total if total else 0,
        "noise_hits": noise_hits, "facts": len(turns),
    }


def main():
    data = json.load(open(DATA))
    alias_en = E5.AliasTable(path=ALIAS_EN)
    subset = os.environ.get("LOCOMO_SUBSET") == "1"
    convs = data[:1] if subset else data
    print("━━━ LoCoMo benchmark · 液环存活度 ━━━")
    print(f"conversations={len(convs)}  top_k={TOP_K}  fast_jaccard={FAST_J}")
    print(f"E5 英文别名表: {ALIAS_EN} ({'开' if not subset else '开'})\n")

    # 机制默认列 = liquid（零向量 IDF 余弦 + 实体/数字 booster，超越词频基线）。
    # liquid_cosine = 纯 tfidf 余弦（lexical_boost=False，追平基线）对照。
    agg = {k: [0, 0, 0] for k in ("liquid", "liquid_cosine", "liquid_jaccard", "liquid_e5",
                                   "liquid_idf", "s4", "tfidf", "tfidf_s4")}
    nucl_jac, nucl_e5, facts_total = 0, 0, 0
    for s in convs:
        qa = s["qa"]
        r_liq = run_conv(s["conversation"], qa, alias=None, idf_cosine=True, lexical_boost=True)
        r_cos = run_conv(s["conversation"], qa, alias=None, idf_cosine=True, lexical_boost=False)
        r_jac = run_conv(s["conversation"], qa, alias=None, idf_cosine=False)     # 纯 jaccard v1
        r_e5 = run_conv(s["conversation"], qa, alias=alias_en, idf_cosine=False)   # E5 英文
        r_idf = run_conv(s["conversation"], qa, alias=None, idf=True, idf_cosine=False)
        r_s4 = run_conv(s["conversation"], qa, alias=None, noise=0.2, idf_cosine=True,
                        lexical_boost=True)
        r_tf = run_tfidf(s["conversation"], qa, noise=0.0)
        r_tf_s4 = run_tfidf(s["conversation"], qa, noise=0.2)
        for key, r in (("liquid", r_liq), ("liquid_cosine", r_cos),
                       ("liquid_jaccard", r_jac), ("liquid_e5", r_e5),
                       ("liquid_idf", r_idf), ("s4", r_s4),
                       ("tfidf", r_tf), ("tfidf_s4", r_tf_s4)):
            agg[key][0] += r["hit"]
            agg[key][1] += r["total"]
            agg[key][2] += r["noise_hits"]
        nucl_jac += r_jac["nucleated"]
        nucl_e5 += r_e5["nucleated"]
        facts_total += r_liq["facts"]
        print(f"  [{s['sample_id']}] facts={r_liq['facts']:>4} "
              f"liquid={r_liq['rate']:.3f} cos={r_cos['rate']:.3f} jac={r_jac['rate']:.3f} "
              f"e5={r_e5['rate']:.3f} idf={r_idf['rate']:.3f} s4={r_s4['rate']:.3f} "
              f"tfidf={r_tf['rate']:.3f} tfidf_s4={r_tf_s4['rate']:.3f}(N{r_tf_s4['noise_hits']})")

    tot = agg["liquid"][1]
    print(f"\n━━━ 汇总（{tot} QA 题 / {facts_total} 条液态记忆）━━━")
    print(f"{'mode':<22}{'recall@K':>10}{'hits':>8}{'noise_topk':>12}{'成核数':>8}")
    print(f"{'none(无记忆)':<22}{'0.000':>10}{'0':>8}{'-':>12}{'-':>8}")
    print(f"{'liquid(默认·余弦+实体booster)':<22}{agg['liquid'][0]/tot:>10.3f}{agg['liquid'][0]:>8}"
          f"{'-':>12}{'-':>8}")
    print(f"{'liquid_cosine(纯余弦对照)':<22}{agg['liquid_cosine'][0]/tot:>10.3f}{agg['liquid_cosine'][0]:>8}"
          f"{'-':>12}{'-':>8}")
    print(f"{'liquid_jaccard(v1对照)':<22}{agg['liquid_jaccard'][0]/tot:>10.3f}{agg['liquid_jaccard'][0]:>8}"
          f"{'-':>12}{nucl_jac:>8}")
    print(f"{'liquid+E5(英文玩具)':<22}{agg['liquid_e5'][0]/tot:>10.3f}{agg['liquid_e5'][0]:>8}"
          f"{'-':>12}{nucl_e5:>8}")
    print(f"{'liquid+IDF(jaccard加权)':<22}{agg['liquid_idf'][0]/tot:>10.3f}{agg['liquid_idf'][0]:>8}"
          f"{'-':>12}{'-':>8}")
    print(f"{'s4(+20%噪声·默认)':<22}{agg['s4'][0]/tot:>10.3f}{agg['s4'][0]:>8}"
          f"{agg['s4'][2]:>12}{'-':>8}")
    print(f"{'tfidf(词频向量基线)':<22}{agg['tfidf'][0]/tot:>10.3f}{agg['tfidf'][0]:>8}"
          f"{'-':>12}{'-':>8}")
    print(f"{'tfidf_s4(+噪声)':<22}{agg['tfidf_s4'][0]/tot:>10.3f}{agg['tfidf_s4'][0]:>8}"
          f"{agg['tfidf_s4'][2]:>12}{'-':>8}")
    print("\n注：成核数极低属预期（单遍真实流无重复）→ 印证 critique 边界；")
    print("主战场=液态召回。liquid(默认) = 机制层零向量 IDF 余弦 + 实体/数字 booster"
          "（纯词频标量 + 精确命中 bonus，非 embedding）。")
    print("liquid(默认) > tfidf 即证明：召回缺口本质是词频归一化差异 + 稀有词稀释，"
          "零向量可**超越**词频向量基线，非需要语义向量。E5 为临时小样本。")
    print("tfidf 为**零依赖词频向量基线**（纯标准库，零 HF）：与液环同口径对照；")
    print("若 tfidf_s4 的 noise_topk > 0 即验证 critique「向量检索单条噪声入池」弱点。")


if __name__ == "__main__":
    main()
