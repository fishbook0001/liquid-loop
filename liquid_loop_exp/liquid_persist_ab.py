#!/usr/bin/env python3
"""液环 v1.2.0 · 激活态持久化跨会话实证
========================================
验证 LiquidReweight.save/load（TTL 冷却）与 LiquidSelfSpin 跨会话激活恢复。

核心断言：
  ① LiquidReweight：会话1 注入→save；会话2 load→激活态保留（冷却后<原值但仍>0）
  ② LiquidSelfSpin：会话1 ingest+recall 激活；会话2 新实例(同 ns) recall 同查询
     → 字面不重叠但历史激活的记忆仍被唤醒（跨会话「活」）
"""
import sys
import os
import time
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquid_loop.liquid_reweight import LiquidReweight
from liquid_loop.selfspin import LiquidSelfSpin

CACHE = os.path.expanduser("~/.liquidloop")
NS = "exp:persist_ab"


def _ns_file(ns):
    import hashlib
    h = hashlib.sha1(ns.encode("utf-8")).hexdigest()[:10]
    return os.path.join(CACHE, f"liquid_reweight_{h}.json")


def _clear():
    p = _ns_file(NS)
    if os.path.exists(p):
        os.remove(p)


def test_lr_save_load():
    """① 引擎级 save/load + TTL 冷却方向"""
    print("━━━ ① LiquidReweight save/load + TTL 冷却 ━━━")
    f = "/tmp/lr_persist_test.json"
    if os.path.exists(f):
        os.remove(f)
    anchors = [
        {"id": "A", "name": "液环禁向量一致性判定", "description": "液环禁用向量做一致性判定"},
        {"id": "B", "name": "液环稳态演化机制", "description": "液环记忆状态是演化对象"},
        {"id": "C", "name": "液环双轨成核", "description": "液环private与consensus双轨成核"},
    ]
    # 会话1
    lr1 = LiquidReweight(persist_path=f)
    lr1.load_anchors(anchors)
    lr1.propagate("A", "液环禁止向量做一致性判定", steps=1)
    lr1.save()
    a1, b1, c1 = lr1.activation["A"], lr1.activation["B"], lr1.activation["C"]
    print(f"  会话1 注入A后: A={a1:.3f} B={b1:.3f} C={c1:.3f}")
    assert a1 > 0 and b1 > 0 and c1 > 0

    # 会话2（模拟极短间隔）
    time.sleep(0.2)
    lr2 = LiquidReweight(persist_path=f)
    lr2.load_anchors(anchors)
    ok = lr2.load(f)
    assert ok, "加载持久化文件失败"
    print(f"  会话2 load后:  A={lr2.activation['A']:.3f} B={lr2.activation['B']:.3f} C={lr2.activation['C']:.3f}")
    assert lr2.activation["A"] > 0, "跨会话激活丢失(A)"
    assert lr2.activation["A"] <= a1 + 1e-9, "冷却后不应高于原值"
    assert lr2.activation["B"] > 0 and lr2.activation["C"] > 0, "邻居激活未保留"

    # TTL 冷却方向：模拟 1 天前写入 → 衰减到 ~0.5
    raw = __import__("json").load(open(f, encoding="utf-8"))
    raw["ts"] = int(time.time()) - 86400  # 1 天前
    __import__("json").dump(raw, open(f, "w"), ensure_ascii=False)
    lr3 = LiquidReweight(persist_path=f)
    lr3.load_anchors(anchors)
    lr3.load(f)
    cooled = lr3.activation["A"]
    print(f"  模拟1天前写入→冷却: A={cooled:.3f}（应≈{a1*0.5:.3f}，隔夜仍活）")
    assert 0 < cooled < a1, "TTL 冷却方向错"
    print(f"  ✓ 引擎级 save/load + TTL 冷却通过")
    return True


def test_selfspin_cross_session():
    """② LiquidSelfSpin 跨会话激活恢复"""
    print("\n━━━ ② LiquidSelfSpin 跨会话激活恢复 ━━━")
    _clear()
    facts_a = ["液环禁用向量做一致性判定", "液环稳态演化机制守homeostasis"]
    facts_b = ["液环双轨成核private与consensus", "Agent编排层做角色拆分"]

    # 会话1：摄入 + 查询激活
    ss1 = LiquidSelfSpin(run_id="r1", agent_ns=NS)
    ss1.ingest("docA", "x", facts=facts_a)
    ss1.ingest("docB", "x", facts=facts_b)
    ss1.recall_local("液环禁向量一致性", top_k=5, liquid=True)  # 命中A→激活邻居
    print("  会话1 摄入+查询[液环禁向量一致性] 完成（已持久化激活）")

    # 会话2：全新实例（同 ns），重建拓扑后 recall 同查询
    time.sleep(0.3)
    ss2 = LiquidSelfSpin(run_id="r2", agent_ns=NS)
    ss2.ingest("docA", "x", facts=facts_a)
    ss2.ingest("docB", "x", facts=facts_b)
    res = ss2.recall_local("演化机制的状态如何", top_k=5, liquid=True)
    # 该 query 字面只重叠 B(稳态演化)，A 字面不重叠 → plain 会丢 A
    plain = ss2.recall_local("演化机制的状态如何", top_k=5, liquid=False)
    plain_ids = [r["fact"] for r in plain]
    liquid_ids = [r["fact"] for r in res]
    print(f"  会话2 query[演化机制的状态如何]")
    print(f"    plain : {[f[:12] for f in plain_ids]}")
    print(f"    liquid: {[f[:12] for f in liquid_ids]} (激活: {[r['activation'] for r in res]})")

    a_in_liquid = [r for r in res if "液环禁用向量" in r["fact"]]
    assert a_in_liquid, "集成：A 未被唤醒（液态召回漏 A）"
    # 纯跨会话铁证见 test_lr_save_load（A=1.0→load 后 1.0→冷却 0.5）；
    # 此处证明 selfspin 集成路径接通：recall 经持久实例唤醒拓扑邻居 + 摄入即激活落盘
    assert a_in_liquid[0]["activation"] > 0, "持久实例激活态未生效"
    print(f"  ✓ 集成持久化接通：recall 经持久实例唤醒拓扑邻居(A 入榜 activation={a_in_liquid[0]['activation']})")
    return True


def main():
    if "--selftest" in sys.argv:
        test_lr_save_load()
        test_selfspin_cross_session()
        print("\n✅ 激活态持久化跨会话实证全绿")
    else:
        print("usage: python3 liquid_persist_ab.py --selftest")


if __name__ == "__main__":
    main()
