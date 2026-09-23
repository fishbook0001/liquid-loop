#!/usr/bin/env python3
"""E4 — 向量方案 vs 液环：事实更新与噪声注入下的记忆漂移对照

    python3 examples/experiments/vector_vs_liquid_drift.py

问题
────
"记忆漂移"是液环的立项前提。此前 E1/E2/E3 都是液环**自证**实验（只测液环自己
收不收敛），从未与向量方案同台对照。本实验补这个缺口，并刻意把 baseline 做强：
如果液环赢不了强 baseline，这个结论必须如实写出来。

被测系统（同一份输入流、同一组查询、同一随机种子）
────────────────────────────────────────────
  V1  naive vector          : feature-hashing 向量 + cosine top-1
  V2  vector + recency      : cosine × 时间衰减（生产级 RAG 常用）
  V3  vector + recency+slot : 再加 metadata 过滤（**最强 baseline**，等价于液环的 anchor 结构）
  LL  Liquid Loop           : 精确匹配成核(≥2 条一致) + 反证轨 stability = s/(s+2c+1)

五个场景
────────
  S1 简单更新   旧×3 → 新×3                      检验：baseline 是否够强
  S2 旧值高频   旧×8 → 新×2                      检验：频次压制
  S3 反复变更   A×3 → B×3 → A×3（3 个检查点）      检验：跟随能力
  S4 噪声注入   真值×5 → 单条错误值×1              检验：最新 ≠ 正确 ← 核心区分场景
  S5 陈旧有效   真值×3 → 其它 slot 大量刷屏×30      检验：长期不提及是否被冲走

指标
────
  stale_rate  返回过期值/被单条噪声带偏的比例（越低越好）
  noise_rate  串到其它 slot 的比例（越低越好）
  traceable   能否给出"为何是这个结论"的可核验证据链

公平性声明（勿跳过）
──────────────────
  · 三类系统看到**完全相同**的陈述流与查询，种子固定 20260808。
  · V2/V3 的 recency 半衰期在 {2,4,8,16,32} 网格搜索，取**全场景合计最优**
    （不允许每场景单独调参——那是过拟合，非真实部署）。
  · V3 拥有与液环 anchor 等价的 slot 过滤能力，消除"结构化定位"的接口优势。
  · 液环每写入一轮后 step(dt=1)，与向量方案一样承受时间衰减。
"""
from __future__ import annotations

import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from liquid_core import LiquidCore  # noqa: E402

DIM = 256
SEED = 20260808


# ══════════════════════════════════════════════════════════════════
# 向量实现：feature hashing（确定性；真实工业技术，非玩具）
# ══════════════════════════════════════════════════════════════════
def embed(text: str, dim: int = DIM) -> list[float]:
    v = [0.0] * dim
    s = f"  {text}  "
    for i in range(len(s) - 2):
        h = int(hashlib.md5(s[i:i + 3].encode()).hexdigest()[:8], 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class VectorStore:
    def __init__(self, recency_halflife: float | None = None, slot_filter: bool = False):
        self.items: list[dict] = []
        self.hl = recency_halflife
        self.slot_filter = slot_filter
        self.tick = 0
        self.traceable = False      # 只有一个 cosine 标量，给不出证据链

    def add(self, slot: str, text: str) -> None:
        self.tick += 1
        self.items.append({"slot": slot, "text": text, "vec": embed(text), "t": self.tick})

    def query(self, slot: str, qtext: str) -> dict | None:
        pool = [i for i in self.items if i["slot"] == slot] if self.slot_filter else self.items
        if not pool:
            return None
        qv = embed(qtext)
        best, bs = None, -1e9
        for it in pool:
            s = cosine(qv, it["vec"])
            if self.hl:
                s *= 0.5 ** ((self.tick - it["t"]) / self.hl)
            if s > bs:
                best, bs = it, s
        return best


class LiquidStore:
    """slot → anchor；同 slot 下不同取值自动标 contradiction（结构化规则，零 LLM）。"""

    def __init__(self):
        self.core = LiquidCore()
        self.traceable = True

    def add(self, slot: str, text: str) -> None:
        for m in self.core.recall(anchor=slot):
            if m.content != text:
                self.core.add(slot, text, agent="s", relation="contradiction", target=m.id)
        self.core.add(slot, text, agent="s")
        self.core.step(dt=1)                       # 与向量方案同样承受时间流逝

    def query(self, slot: str, qtext: str = "") -> dict | None:
        got = self.core.recall(anchor=slot)
        return {"slot": slot, "text": got[0].content, "_mid": got[0].id} if got else None

    def explain(self, mid: str) -> dict:
        return self.core.explain(mid)


# ══════════════════════════════════════════════════════════════════
SLOTS = [
    ("常用编辑器", "VSCode", "Neovim"),
    ("部署环境", "Docker", "裸机systemd"),
    ("主力语言", "Python", "Rust"),
    ("数据库", "MySQL", "PostgreSQL"),
    ("回复偏好", "详细展开", "极简结论"),
    ("构建工具", "Webpack", "Vite"),
]
SCENARIOS = ("S1", "S2", "S3", "S4", "S5")


def stmt(slot: str, val: str) -> str:
    return f"{slot}是{val}"


def run_scenario(store, name: str) -> dict:
    stale = noise = checks = 0

    for idx, (slot, old, new) in enumerate(SLOTS):
        if name == "S1":
            plan, cps = [(old, 3), (new, 3)], {1: (new, [old])}
        elif name == "S2":
            plan, cps = [(old, 8), (new, 2)], {1: (new, [old])}
        elif name == "S3":
            plan, cps = [(old, 3), (new, 3), (old, 3)], {0: (old, [new]), 1: (new, [old]), 2: (old, [new])}
        elif name == "S4":
            # 真值稳固后，注入单条错误值 —— "最新"不等于"正确"
            plan, cps = [(old, 5), (new, 1)], {1: (old, [new])}
        else:  # S5 陈旧但有效：建立后长期不再提及，期间其它 slot 刷屏
            plan, cps = [(old, 3)], {0: (old, [new])}

        for i, (val, n) in enumerate(plan):
            for _ in range(n):
                store.add(slot, stmt(slot, val))
            if name == "S5" and i == 0:
                for jdx, (oslot, oval, _) in enumerate(SLOTS):   # 其它 slot 刷屏
                    if jdx == idx:
                        continue
                    for _ in range(6):
                        store.add(oslot, stmt(oslot, oval))
            if i in cps:
                truth, stale_vals = cps[i]
                r = store.query(slot, stmt(slot, "?"))
                checks += 1
                if r is None:
                    stale += 1
                elif r["text"] == stmt(slot, truth):
                    pass
                elif any(r["text"] == stmt(slot, sv) for sv in stale_vals):
                    stale += 1
                else:
                    noise += 1
    return {"checks": checks, "stale": stale, "noise": noise}


def tune_halflife(slot_filter: bool) -> float:
    """全场景合计最优半衰期（不许每场景单独调参 —— 那是过拟合）。"""
    best_hl, best_err = None, 1e9
    for hl in (2, 4, 8, 16, 32):
        s = n = c = 0
        for sc in SCENARIOS:
            r = run_scenario(VectorStore(hl, slot_filter), sc)
            s += r["stale"]; n += r["noise"]; c += r["checks"]
        err = (s + n) / c
        if err < best_err:
            best_hl, best_err = hl, err
    return best_hl


def main() -> None:
    print(__doc__)
    print("=" * 78)
    hl2, hl3 = tune_halflife(False), tune_halflife(True)
    print(f"\n[baseline 调优] V2 最优半衰期={hl2}   V3 最优半衰期={hl3}\n")

    systems = [
        ("V1 naive vector", lambda: VectorStore(None, False)),
        (f"V2 vector+recency({hl2})", lambda: VectorStore(hl2, False)),
        ("V3 +slot过滤(最强)", lambda: VectorStore(hl3, True)),
        ("LL Liquid Loop", LiquidStore),
    ]

    print(f"{'系统':<26}" + "".join(f"{s:>9}" for s in SCENARIOS) + f"{'合计错误率':>12}")
    print("-" * 78)
    detail: dict = {}
    for label, factory in systems:
        cells, s_t = [], 0
        c_t = n_t = 0
        per = {}
        for sc in SCENARIOS:
            r = run_scenario(factory(), sc)
            bad = r["stale"] + r["noise"]
            per[sc] = r
            cells.append(f"{bad}/{r['checks']}")
            s_t += r["stale"]; n_t += r["noise"]; c_t += r["checks"]
        detail[label] = (s_t, n_t, c_t, per)
        print(f"{label:<26}" + "".join(f"{c:>9}" for c in cells) + f"{(s_t + n_t) / c_t:>11.1%}")
    print("-" * 78)
    print("（单元格 = 错误数/查询数，错误 = 返回过期值 或 串到其它 slot）")

    print(f"\n{'系统':<26}{'stale_rate':>12}{'noise_rate':>12}{'可解释':>10}")
    print("-" * 78)
    for label, factory in systems:
        s, n, c, _ = detail[label]
        print(f"{label:<26}{s / c:>11.1%}{n / c:>12.1%}{'是' if factory().traceable else '否':>10}")

    # ── 核心区分点 ──
    print("\n【S4 噪声注入 — 核心区分场景】真值说 5 次，单条错误值最后出现：")
    for label, factory in systems:
        r = detail[label][3]["S4"]
        verdict = "被单条噪声带偏" if r["stale"] else "守住真值"
        print(f"    {label:<26} 错误 {r['stale']}/{r['checks']}   {verdict}")
    print("    机制：向量方案无「成核门槛」，单条陈述即可参与检索并因最新而胜出；")
    print("          液环要求 ≥2 条一致才结晶，单条噪声无法形成记忆。")

    print("\n【可解释性实证】液环给出结论依据（S4 同款输入）：")
    ls = LiquidStore()
    for _ in range(5):
        ls.add("常用编辑器", stmt("常用编辑器", "VSCode"))
    ls.add("常用编辑器", stmt("常用编辑器", "Neovim"))
    ex = ls.explain(ls.query("常用编辑器")["_mid"])
    print(f"    结论: {ex['content']}")
    print(f"    公式: {ex['formula']}")
    print(f"    支撑 {len(ex['supported_by'])} 条 / 反驳 {len(ex['contradicted_by'])} 条 —— 每条都有 id 可回溯")
    print("    → 向量方案在此处只能给出一个 cosine 标量，无法回答'为何是它'")

    print("""
LIMITATIONS（如实声明，勿省略）
──────────────────────────────
1. 向量用 feature hashing，非 OpenAI/BGE 真实 embedding。真 embedding 在**深层语义改写**
   （换核心词，如"红色"→"crimson"）场景显著更强，绝对数值会变；但**表层改写**
   （换标点/语序/虚词/共享核心词）液环靠 selfspin 的确定性 token 重叠聚类已能合并，
   无需 embedding。S4 的失守仍是**机制性**的——向量库没有成核门槛，任何嵌入质量都
   不改变"单条陈述即可参与检索"这一事实。
2. 液环低错误率有前提成本：需要 slot schema 判定"哪些陈述互斥"。本实验用结构化
   规则自动完成（零 LLM），但**开放域无 schema 时液环无法自动标注 contradiction**
   —— 这是液环真实的适用边界。
3. 合成数据，非公开 benchmark。LoCoMo / LongMemEval 对比仍在 roadmap。
4. 正确读法："在有结构化互斥语义的场景，液环的成核门槛+显式反证比向量检索更抗
   噪声注入，且结论可回溯" —— 而非"液环全面优于向量检索"。语义相似性检索、
   开放域模糊召回仍是向量的主场。
""")


if __name__ == "__main__":
    main()
