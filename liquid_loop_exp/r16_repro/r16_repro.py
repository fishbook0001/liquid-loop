"""R16 复现：FTS5 主干 vs bigram TF-IDF 向量 vs OR+覆盖率重排（液环真实语料）。

独立复现 Marvis R16 实验（trust but verify）：
- tokenize 与液环 server 同源：中文单字 + 英文数字连续串（[一-鿿]|[a-zA-Z0-9]+）
- FTS5 unicode61：body 用空格分隔 token 串
- 查询：round-trip 抽 8-20 token 片段，15% 噪声（删除/随机替换）
- 方法：A 纯FTS5 AND / B 纯向量(bigram TF-IDF) / I OR召回+覆盖率×向量重排
- 指标：R@1 / R@5 / R@10 / MRR

隔离命名空间，只读液环语料，零依赖 stdlib + sqlite3。
"""
import json
import math
import random
import re
import sqlite3
from collections import Counter

# ── 语料（液环真实 state.json）──────────────────────────
STATE = "/Users/feixubuke/.liquidloop/memory/.liquid/state.json"

def load_corpus():
    d = json.load(open(STATE))
    evs = [e["content"] for e in d["evidences"] if e.get("content")]
    mems = [m["content"] for m in d["memories"] if m.get("content")]
    return [(f"ev{i}", c) for i, c in enumerate(evs)] + [(f"mem{i}", c) for i, c in enumerate(mems)]

# ── tokenize（与液环 server 同源）───────────────────────
_TOKEN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")

def tokenize(text):
    return _TOKEN.findall(text or "")

def fts_text(text):
    return " ".join(tokenize(text))

def fts_query(text, or_mode=False):
    toks = tokenize(text)
    if not toks:
        return None
    if or_mode:
        return " OR ".join('"%s"' % t for t in toks)
    return " ".join('"%s"' % t for t in toks)

# ── 查询集：round-trip + 噪声 ──────────────────────────
def make_queries(items, n=120, seed=42):
    rng = random.Random(seed)
    sampled = rng.sample(items, min(n, len(items)))
    queries = []
    for iid, content in sampled:
        toks = tokenize(content)
        if len(toks) < 10:
            continue
        seg_len = rng.randint(8, 20)
        start = rng.randint(0, max(0, len(toks) - seg_len))
        seg_toks = toks[start:start + seg_len]
        queries.append({"target": iid, "text": " ".join(seg_toks), "variant": "exact"})
        noisy = []
        for t in seg_toks:
            r = rng.random()
            if r < 0.08:
                continue
            elif r < 0.15:
                noisy.append(rng.choice("时间记忆液环证据因果锚点熵"))
            else:
                noisy.append(t)
        nt = " ".join(noisy).strip()
        if len(nt.split()) >= 5:
            queries.append({"target": iid, "text": nt, "variant": "noisy"})
    return queries

# ── FTS5 索引 ──────────────────────────────────────────
def build_fts(items):
    conn = sqlite3.connect(":memory:")
    conn.execute('CREATE VIRTUAL TABLE docs USING fts5(idx, body, tokenize="unicode61")')
    conn.executemany("INSERT INTO docs(idx, body) VALUES (?,?)",
                     [(i, fts_text(c)) for i, (_, c) in enumerate(items)])
    return conn

def fts_and(db, qtext, k=24):
    q = fts_query(qtext, or_mode=False)
    if not q:
        return []
    try:
        rows = db.execute("SELECT idx FROM docs WHERE body MATCH ? ORDER BY bm25(docs) LIMIT ?",
                          (q, k)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []

def fts_or(db, qtext, k=100):
    q = fts_query(qtext, or_mode=True)
    if not q:
        return []
    try:
        rows = db.execute("SELECT idx FROM docs WHERE body MATCH ? ORDER BY bm25(docs) LIMIT ?",
                          (q, k)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []

# ── bigram TF-IDF 向量 ─────────────────────────────────
def bigrams(text):
    """R16 同款：单 token 集 + 相邻拼接（非纯 bigram，保证与 token 集有交集）。"""
    toks = tokenize(text)
    grams = set(toks)
    for a, b in zip(toks, toks[1:]):
        grams.add(a + b)
    return grams

def build_vector(items):
    doc_grams = [bigrams(c) for _, c in items]
    df = Counter()
    for g in doc_grams:
        for bg in g:
            df[bg] += 1
    n = len(items)
    idf = {bg: math.log((n + 1) / (f + 1)) + 1 for bg, f in df.items()}
    vecs = []
    for g in doc_grams:
        w = {x: idf.get(x, 0) for x in g}
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        vecs.append((w, norm))
    return vecs, idf, doc_grams

def vec_search(qtext, vecs, idf, k=24):
    qg = bigrams(qtext)
    if not qg:
        return []
    qw = {x: idf.get(x, 0) for x in qg}
    qn = math.sqrt(sum(v * v for v in qw.values())) or 1.0
    scored = []
    for i, (w, norm) in enumerate(vecs):
        inter = qw.keys() & w.keys()
        if not inter:
            continue
        s = sum(qw[x] * w[x] for x in inter) / (qn * norm)
        scored.append((s, i))
    scored.sort(reverse=True)
    return [(i, s) for s, i in scored[:k]]

# ── 覆盖率 ─────────────────────────────────────────────
def cov(doc_grams, idx, qset):
    return len(qset & doc_grams[idx]) / len(qset) if qset else 0.0

# ── 评估 ───────────────────────────────────────────────
def evaluate(fn, queries, target_idx):
    hits = {k: 0 for k in (1, 5, 10)}
    rr = 0.0
    n = 0
    for q in queries:
        target = target_idx.get(q["target"])
        if target is None:
            continue
        ranked = fn(q["text"])
        n += 1
        for k in (1, 5, 10):
            if target in ranked[:k]:
                hits[k] += 1
        pos = ranked.index(target) + 1 if target in ranked else 0
        if pos:
            rr += 1.0 / pos
    return {k: hits[k] / n for k in (1, 5, 10)}, rr / n, n

def main(seed=42):
    items = load_corpus()
    target_idx = {iid: i for i, (iid, _) in enumerate(items)}
    db = build_fts(items)
    vecs, idf, doc_grams = build_vector(items)
    queries = make_queries(items, n=120, seed=seed)
    exact = [q for q in queries if q["variant"] == "exact"]
    noisy = [q for q in queries if q["variant"] == "noisy"]
    print(f"语料={len(items)} 查询 exact={len(exact)} noisy={len(noisy)}")

    # A: 纯 FTS5 AND
    def method_a(qtext):
        return fts_and(db, qtext, k=24)
    # B: 纯向量
    def method_b(qtext):
        return [i for i, _ in vec_search(qtext, vecs, idf)]  # vec_search 返回 [(s,i)]

    # I: OR 召回 + 覆盖率×向量重排
    def method_i(qtext):
        f = fts_or(db, qtext, k=100)
        v = dict(vec_search(qtext, vecs, idf))
        pool = set(f) | set(v.keys())
        score = {idx: cov(doc_grams, idx, set(tokenize(qtext))) * v.get(idx, 0.0)
                 for idx in pool}
        return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])[:24]]

    methods = {"A_纯FTS5(AND)": method_a, "B_纯向量": method_b, "I_OR+覆盖率×向量": method_i}
    print(f"\n{'方法':<22}{'变体':<8}{'R@1':<8}{'R@5':<8}{'R@10':<8}{'MRR':<8}")
    for name, fn in methods.items():
        for variant, qset in (("exact", exact), ("noisy", noisy)):
            r, mrr, n = evaluate(fn, qset, target_idx)
            print(f"{name:<22}{variant:<8}{r[1]:<8.3f}{r[5]:<8.3f}{r[10]:<8.3f}{mrr:<8.3f}")

    # noisy OR 召回命中率
    or_hit = sum(1 for q in noisy
                 if target_idx[q["target"]] in fts_or(db, q["text"], k=100))
    print(f"\nnoisy OR 召回(top100) 正确目标命中率: {or_hit/len(noisy):.4f}")

if __name__ == "__main__":
    main()
