"""R16-I 方案 vs 液环现有 RAR：对比评估（同语料同查询集，落地决策依据）。

问题：R16 是否值得落地为 RAR 的检索增强？
方法：1087 条液环真实语料，round-trip 查询(exact/noisy)，对比
  RAR(现生产) / A_纯FTS5 / B_纯向量 / I_OR+覆盖率×向量
指标：R@1 / R@5 / MRR
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # r16_repro/ 目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # liquid-loop/
import r16_repro as R   # r16_repro.py 文件模块
from liquid_loop.rar import RARIndex

def build_cands(items):
    cands = []
    for i, (iid, c) in enumerate(items):
        cands.append({
            "id": iid,
            "content": c,
            "category": "evidence" if iid.startswith("ev") else "memory",
            "type": "evidence" if iid.startswith("ev") else "memory",
            "timestamp": "",
            "owner": "",
            "scope": "",
            "contributors": [],
        })
    return cands

def main(seed=42):
    items = R.load_corpus()
    target_idx = {iid: i for i, (iid, _) in enumerate(items)}
    db = R.build_fts(items)
    vecs, idf, doc_grams = R.build_vector(items)
    queries = R.make_queries(items, n=120, seed=seed)
    exact = [q for q in queries if q["variant"] == "exact"]
    noisy = [q for q in queries if q["variant"] == "noisy"]

    # RAR（现生产）
    rar = RARIndex.build(build_cands(items), version="cmp")
    def method_rar(qtext):
        hits = rar.recall(qtext, top_k=24)
        return [target_idx[mid] for mid, _sc, _m in hits if mid in target_idx]

    # A / B / I（复用 r16_repro）
    def method_a(qtext):
        return R.fts_and(db, qtext, k=24)
    def method_b(qtext):
        return [i for i, _ in R.vec_search(qtext, vecs, idf)]
    def method_i(qtext):
        f = R.fts_or(db, qtext, k=100)
        v = dict(R.vec_search(qtext, vecs, idf))
        pool = set(f) | set(v.keys())
        score = {idx: R.cov(doc_grams, idx, set(R.tokenize(qtext))) * v.get(idx, 0.0)
                 for idx in pool}
        return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])[:24]]

    # F: RAR ∪ I 池 + 覆盖率×向量重排（双通道融合）
    def method_f(qtext):
        rar_set = [target_idx[mid] for mid, _sc, _m in rar.recall(qtext, top_k=24) if mid in target_idx]
        i_set = method_i(qtext)
        pool = set(rar_set) | set(i_set)
        v = dict(R.vec_search(qtext, vecs, idf))
        score = {idx: R.cov(doc_grams, idx, set(R.tokenize(qtext))) * v.get(idx, 0.0)
                 for idx in pool}
        return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])[:24]]

    methods = {"RAR(现生产)": method_rar, "A_纯FTS5(AND)": method_a,
               "B_纯向量": method_b, "I_OR+覆盖率×向量": method_i,
               "F_RAR∪I融合": method_f}
    print(f"语料={len(items)} 查询 exact={len(exact)} noisy={len(noisy)}")
    print(f"\n{'方法':<20}{'变体':<8}{'R@1':<8}{'R@5':<8}{'MRR':<8}")
    results = {}
    for name, fn in methods.items():
        for variant, qset in (("exact", exact), ("noisy", noisy)):
            r, mrr, n = R.evaluate(fn, qset, target_idx)
            results.setdefault(name, {})[variant] = (r[1], r[5], mrr)
            print(f"{name:<20}{variant:<8}{r[1]:<8.3f}{r[5]:<8.3f}{mrr:<8.3f}")

    # 结论判定
    print("\n=== 落地决策 ===")
    for variant in ("exact", "noisy"):
        rar_v = results["RAR(现生产)"][variant]
        i_v = results["I_OR+覆盖率×向量"][variant]
        diff = i_v[0] - rar_v[0]
        verdict = "I 优于 RAR" if diff > 0.02 else ("I 持平 RAR" if abs(diff) <= 0.02 else "I 劣于 RAR")
        print(f"  {variant}: I R@1={i_v[0]:.3f} vs RAR R@1={rar_v[0]:.3f} (Δ={diff:+.3f}) → {verdict}")

if __name__ == "__main__":
    main()
