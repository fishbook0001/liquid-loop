#!/usr/bin/env python3
"""run_longmemeval.py — 液环在 LongMemEval 真实长期记忆上的「存活度」评测

设计哲学（与 run_locomo.py 同口径，呼应 critique 末条 + ROADMAP D1）：
  LongMemEval 是 500 道**多会话长期记忆**题（每题含 ~40 个历史会话 haystack），
  考察 agent 在真实长期交互中"能否找回跨会话的事实/偏好/时序更新"。
  这是比 LoCoMo 更贴近"真实 agent 长期记忆"的基准，五维能力：
  single-session-user / -assistant / -preference / temporal-reasoning /
  knowledge-update / multi-session。

  液环的「≥2 篇一致才成核」在单遍会话流中成核率趋近 0（同 LoCoMo 边界），
  故主战场仍是**液态层召回**：把每会话每 turn 当液态事实灌入，
  用 question 召回，看 top_k 是否命中 answer_session_ids（会话级 gold）。

指标（与官方 retrieval metric 对齐：session-level recall@K）：
  - recall@K：top_k 命中的 session_id 是否覆盖 answer_session_ids 中至少一个
  - S4 污染率：注入噪声 turn 后 top_k 中噪声占比（抗噪不退化度量）
  - 成核数：local_rotate 产出的≥2 支持 canonical 数（预期极低，边界证据）

公理纪律（液环**内部机制**）：纯 jaccard / 倒排，零 embedding、零向量、零 LLM 推断。
  TfidfBaseline 是**外部评测对照基线**（非液环内部），与 WHY_NO_VECTOR.md 自身建的
  V1/V2/V3 向量 baseline 同性质；不进入 liquid_loop/ 包，**不破禁向量红线**。

用法：
  LME_SUBSET=1 python3 examples/benchmarks/run_longmemeval.py   # 前 20 题快速验证
  LME_SUBSET=50 python3 examples/benchmarks/run_longmemeval.py  # 前 50 题
  python3 examples/benchmarks/run_longmemeval.py                # 全量 500 题
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
import E5_alias_table as E5

DATA = os.path.join(REPO_ROOT, "benchmarks", "data", "longmemeval_s_cleaned.json")
ALIAS_EN = os.path.join(REPO_ROOT, "benchmarks", "aliases_en.json")
TOP_K = 5
FAST_J = 0.60


def collect_turns(item: dict):
    """展平一个 question 的 haystack_sessions → [(session_id, text), ...]。

    session_id 取自 item['haystack_session_ids']（与 haystack_sessions 同序），
    保证与 answer_session_ids（gold）可直接比对。同一会话所有 turn 共享 session_id，
    故命中任一 turn 即命中该会话（session-level recall，对齐官方 retrieval metric）。
    """
    turns = []
    sessions = item.get("haystack_sessions") or []
    sids = item.get("haystack_session_ids") or []
    for i, sess in enumerate(sessions):
        sid = sids[i] if i < len(sids) else f"S{i}"
        if not isinstance(sess, list):
            continue
        for t in sess:
            if isinstance(t, dict) and t.get("content"):
                turns.append((sid, t["content"]))
    return turns


def build_ss(turns: list, alias=None):
    ss = LiquidSelfSpin(run_id="lme", fast_jaccard=FAST_J)
    ss.liquid_persist = False  # 评测纯内存；每题独立记忆库
    for sid, text in turns:
        norm = alias.normalize_en(text) if alias else text
        ss.ingest(sid, "", facts=[norm])
    nuclei = ss.local_rotate()
    return ss, nuclei


def eval_recall(ss, items: list, alias=None):
    hits, total, noise_hits = 0, 0, 0
    for it in items:
        ev = it.get("answer_session_ids") or []
        if not ev:
            continue
        total += 1
        qtext = alias.normalize_en(it["question"]) if alias else it["question"]
        res = ss.recall_local(qtext, top_k=TOP_K, liquid=False)
        polluted = False
        for r in res:
            rid = r["report_id"]
            if rid.startswith("N"):
                polluted = True
            if rid in ev:
                hits += 1
                break
        if polluted:
            noise_hits += 1
    return hits, total, noise_hits


def run_item(item: dict, alias=None, noise: float = 0.0, seed: int = 0):
    turns = collect_turns(item)
    if noise > 0:
        rng = random.Random(seed)
        n = int(len(turns) * noise)
        for i in range(n):
            turns.append((f"N{i}", f"zxqw noise token {i} kjpw unrelated vytr"))
    ss, nuclei = build_ss(turns, alias)
    hits, total, noise_hits = eval_recall(ss, [item], alias=alias)
    return {
        "hit": hits, "total": total, "rate": hits / total if total else 0,
        "nucleated": len(nuclei) if nuclei else 0, "facts": len(turns),
        "noise_hits": noise_hits,
    }


class TfidfBaseline:
    """零依赖 TF-IDF + 余弦向量基线（纯标准库，零 embedding / 零 HF / 零 LLM）。"""
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


def run_tfidf(item: dict, noise: float = 0.0, seed: int = 0):
    turns = collect_turns(item)
    if noise > 0:
        rng = random.Random(seed)
        n = int(len(turns) * noise)
        for i in range(n):
            turns.append((f"N{i}", f"zxqw noise token {i} kjpw unrelated vytr"))
    ids = [t[0] for t in turns]
    docs = [t[1] for t in turns]
    idx = TfidfBaseline(ids, docs)
    ev = item.get("answer_session_ids") or []
    if not ev:
        return {"hit": 0, "total": 0, "rate": 0, "noise_hits": 0, "facts": len(turns)}
    top = idx.query(item["question"], TOP_K)
    polluted = False
    hit = 0
    for rid, _ in top:
        if rid.startswith("N"):
            polluted = True
        if rid in ev:
            hit = 1
            break
    return {"hit": hit, "total": 1, "rate": hit, "noise_hits": 1 if polluted else 0, "facts": len(turns)}


def main():
    data = json.load(open(DATA))
    alias_en = E5.AliasTable(path=ALIAS_EN)
    sub_env = os.environ.get("LME_SUBSET", "")
    if sub_env == "1":
        items = data[:20]
    elif sub_env.isdigit() and int(sub_env) > 1:
        items = data[:int(sub_env)]
    else:
        items = data
    print(f"━━━ LongMemEval benchmark · 液环存活度 ━━━")
    print(f"questions={len(items)} (of {len(data)})  top_k={TOP_K}  fast_jaccard={FAST_J}")
    print(f"E5 英文别名表: 开\n")

    agg = {k: [0, 0, 0] for k in ("liquid", "liquid_e5", "s4", "tfidf", "tfidf_s4")}
    nucl_liquid, nucl_e5, facts_total = 0, 0, 0
    for idx_num, it in enumerate(items):
        r_liq = run_item(it, alias=None)
        r_e5 = run_item(it, alias=alias_en)
        r_s4 = run_item(it, alias=None, noise=0.2)
        r_tf = run_tfidf(it, noise=0.0)
        r_tf_s4 = run_tfidf(it, noise=0.2)
        for key, r in (("liquid", r_liq), ("liquid_e5", r_e5), ("s4", r_s4),
                       ("tfidf", r_tf), ("tfidf_s4", r_tf_s4)):
            agg[key][0] += r["hit"]
            agg[key][1] += r["total"]
            agg[key][2] += r["noise_hits"]
        nucl_liquid += r_liq["nucleated"]
        nucl_e5 += r_e5["nucleated"]
        facts_total += r_liq["facts"]
        if (idx_num + 1) % 10 == 0 or idx_num == 0:
            print(f"  [q{idx_num+1:>3}] facts={r_liq['facts']:>4} "
                  f"liquid={r_liq['rate']:.3f} e5={r_e5['rate']:.3f} s4={r_s4['rate']:.3f} "
                  f"tfidf={r_tf['rate']:.3f} tfidf_s4={r_tf_s4['rate']:.3f}(N{r_tf_s4['noise_hits']})")

    tot = agg["liquid"][1]
    print(f"\n━━━ 汇总（{tot} QA 题 / {facts_total} 条液态记忆）━━━")
    print(f"{'mode':<14}{'recall@K':>10}{'hits':>8}{'noise_topk':>12}{'成核数':>8}")
    print(f"{'none(无记忆)':<14}{'0.000':>10}{'0':>8}{'-':>12}{'-':>8}")
    print(f"{'liquid(液环)':<14}{agg['liquid'][0]/tot:>10.3f}{agg['liquid'][0]:>8}"
          f"{'-':>12}{nucl_liquid:>8}")
    print(f"{'liquid+E5':<14}{agg['liquid_e5'][0]/tot:>10.3f}{agg['liquid_e5'][0]:>8}"
          f"{'-':>12}{nucl_e5:>8}")
    print(f"{'s4(+20%噪声)':<14}{agg['s4'][0]/tot:>10.3f}{agg['s4'][0]:>8}"
          f"{agg['s4'][2]:>12}{nucl_liquid:>8}")
    print(f"{'tfidf(向量基线)':<14}{agg['tfidf'][0]/tot:>10.3f}{agg['tfidf'][0]:>8}"
          f"{'-':>12}{'-':>8}")
    print(f"{'tfidf_s4(+噪声)':<14}{agg['tfidf_s4'][0]/tot:>10.3f}{agg['tfidf_s4'][0]:>8}"
          f"{agg['tfidf_s4'][2]:>12}{'-':>8}")
    print(f"\n注：成核数极低属预期（单遍会话流无重复）→ 印证 critique 边界；主战场=液态召回。")
    print(f"tfidf 为**零依赖向量基线**（纯标准库，零 HF）：若 tfidf_s4 noise_topk>0 即证向量单条噪声入池弱点。")


if __name__ == "__main__":
    main()
