#!/usr/bin/env python3
"""液环 v1.0 · 液态召回真实语料 A/B 实证
=========================================
目的：在**真实语料**（军师调研 50 篇索引，天然成簇：向量/编排/上下文/UI）上，
对比 plain_recall（纯字面） vs liquid_recall（液态唤醒），
量化液态重排的增益与风险，作为 v1.1.0 发版决策依据。

指标：
  breadth        = top-k 内不同锚点数（液态应 ≥ 字面）
  wake_precision = 液态新增锚点中「拓扑相邻于某字面命中」的比例（相关唤醒）
  false_wake     = 液态新增锚点中既非字面命中、也非拓扑相邻者（噪声唤醒）
  coherence      = top-k 锚点间平均 containment（聚类内聚度）
  activation_max = 单锚点激活峰值（应 ≤1.0，黏滞封顶成立）
  activation_entropy = 激活分布熵（GREEN 若受控，未爆炸）

用法：
  python3 liquid_recall_ab.py            # 跑真实语料 A/B
  python3 liquid_recall_ab.py --selftest # 合成 3 节点快验
"""
import os
import re
import sys
import glob
import math
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from liquid_loop.liquid_reweight import LiquidReweight, _containment, _keyword_overlap

REPORT_DIR = os.path.expanduser("~/WorkBuddy/Claw/output/军师调研")
K = 5


def _load_real_anchors() -> list:
    """从军师调研报告抽真实锚点（标题 + 首段摘要），天然成簇。"""
    anchors = []
    files = sorted(glob.glob(os.path.join(REPORT_DIR, "军师调研_*.md")))
    for fp in files:
        try:
            txt = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), None)
        if not title:
            continue
        # 首段有意义正文（非标题/表/引用/列表/图片）
        desc = ""
        for l in lines:
            if l.startswith("#") or l.startswith("|") or l.startswith(">") \
               or l.startswith("-") or l.startswith("!") or l.startswith("http"):
                continue
            if len(re.sub(r"[#*\s]", "", l)) >= 20:
                desc = re.sub(r"[#*`]", "", l).strip()
                break
        if not desc:
            desc = title
        rid = os.path.basename(fp).replace("军师调研_", "").replace(".md", "")
        anchors.append({"id": rid, "name": title, "description": desc[:200]})
    return anchors


# 多轮查询（混合直接 + 拓扑间接），模拟真实「对话式」累积激活
QUERIES = [
    ("Q1 直接·编排",       "Agent 多角色编排怎么落地实现"),
    ("Q2 间接·审批",       "多 agent 协作的审批闸门怎么设计"),
    ("Q3 直接·向量",       "本地一致性判定怎么禁用向量"),
    ("Q4 间接·去冗余",     "embedding 之外的去冗余记忆方案"),
    ("Q5 直接·上下文",     "上下文工程怎么分层裁剪"),
    ("Q6 间接·推理",       "长文本推理怎么不重算历史"),
    ("Q7 直接·UI",         "桌面应用怎么不改源码换肤"),
    ("Q8 间接·定制",       "Agent 自主 UI 定制范式怎么做"),
]


def _coherence(ids, lr: LiquidReweight) -> float:
    if len(ids) < 2:
        return 0.0
    tot, n = 0.0, 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = lr._anchors.get(ids[i]), lr._anchors.get(ids[j])
            if a and b:
                tot += _containment(lr._topo_text(a), lr._topo_text(b))
                n += 1
    return tot / n if n else 0.0


def run_ab(anchors: list, live: bool = False):
    lr = LiquidReweight(beta=0.6, topo_thresh=0.20)
    lr.load_anchors(anchors)
    print(f"锚点数: {len(anchors)}  拓扑边: {lr.snapshot()['topo_edges']}")

    agg = dict(q=0, b_plain=0, b_liquid=0, added=0, rel=0, false=0,
               coh_p=0.0, coh_l=0.0, amax=0.0, ent=0.0)
    print(f"\n{'查询':<16} | {'plain':>6} {'liquid':>6} | {'+新增':>5} {'相关':>4} {'噪声':>4} | coh_p  coh_l")
    print("-" * 78)

    for label, q in QUERIES:
        plain = lr.plain_recall(q, top_k=K)
        liquid = lr.liquid_recall(q, top_k=K)  # 用当前累积激活
        p_ids = [r["anchor_id"] for r in plain]
        l_ids = [r["anchor_id"] for r in liquid]
        added = [a for a in l_ids if a not in p_ids]
        # 相关唤醒 = 新增锚点拓扑相邻于某字面命中
        rel = sum(1 for a in added if any(
            _containment(lr._topo_text(lr._anchors[a]), lr._topo_text(lr._anchors[p]))
            >= lr.topo_thresh for p in p_ids))
        false = len(added) - rel
        cp = _coherence(p_ids, lr)
        cl = _coherence(l_ids, lr)

        agg["q"] += 1
        agg["b_plain"] += len(p_ids)
        agg["b_liquid"] += len(l_ids)
        agg["added"] += len(added)
        agg["rel"] += rel
        agg["false"] += false
        agg["coh_p"] += cp
        agg["coh_l"] += cl
        agg["amax"] = max(agg["amax"], max((r["activation"] for r in liquid), default=0.0))

        print(f"{label:<16} | {len(p_ids):>6} {len(l_ids):>6} | {len(added):>5} {rel:>4} {false:>4} | {cp:.3f}  {cl:.3f}")

        # 模拟「本查询触达的字面命中被注入」→ 累积激活（真实使用场景）
        for r in plain:
            lr.propagate(r["anchor_id"], lr._anchor_text(lr._anchors[r["anchor_id"]]), steps=1)

    # 激活熵（受控性）
    acts = [v for v in lr.activation.values() if v > 0]
    if acts:
        s = sum(acts)
        agg["ent"] = -sum((a / s) * math.log(a / s + 1e-12) for a in acts) if s > 0 else 0.0

    print("-" * 78)
    n = agg["q"]
    wp = agg["rel"] / (agg["rel"] + agg["false"]) if (agg["rel"] + agg["false"]) else 0.0
    print(f"聚合({n}轮):")
    print(f"  平均广度   plain={agg['b_plain']/n:.2f}  liquid={agg['b_liquid']/n:.2f}  "
          f"Δ=+{agg['b_liquid']/n - agg['b_plain']/n:.2f}")
    print(f"  唤醒精度   relevant={agg['rel']}  false={agg['false']}  wake_precision={wp:.2%}")
    print(f"  聚类内聚   coh_plain={agg['coh_p']/n:.3f}  coh_liquid={agg['coh_l']/n:.3f}")
    print(f"  黏滞封顶   activation_max={agg['amax']:.3f} (应≤1.0=cap生效)")
    print(f"  激活熵     {agg['ent']:.3f} (受控=GREEN，未爆炸)")
    if live:
        res = lr.snapshot_to_8790(run_id="ab", category="liquid-ab-realcorpus")
        print(f"  → 快照写8790: {res['ok']} (agent_id={res['agent_id']})")
    return wp


def _selftest():
    anchors = [
        {"id": "A", "name": "液环禁向量一致性判定", "description": "液环禁用向量embedding做一致性判定与成核"},
        {"id": "B", "name": "液环稳态演化机制", "description": "液环记忆状态是演化对象而非被管理数据"},
        {"id": "C", "name": "液环双轨成核", "description": "液环private与consensus双轨成核机制"},
    ]
    lr = LiquidReweight()
    lr.load_anchors(anchors)
    lr.propagate("A", "液环禁止向量做一致性判定", steps=1)
    q = "演化机制的状态如何"
    plain = lr.plain_recall(q, 3)
    liquid = lr.liquid_recall(q, 3)
    assert "A" not in [r["anchor_id"] for r in plain]
    assert "A" in [r["anchor_id"] for r in liquid]
    print("selftest OK: 液态唤醒已激活但字面缺席的 A")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--live", action="store_true", help="写8790隔离ns")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    anchors = _load_real_anchors()
    if not anchors:
        print("未加载到真实语料，检查 REPORT_DIR")
        sys.exit(1)
    run_ab(anchors, live=args.live)


if __name__ == "__main__":
    main()
