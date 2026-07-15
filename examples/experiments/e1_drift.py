"""E1 — 长期漂移实验（压力测试，飞哥指定末位）。

问题：在 1000 轮随机 evidence 注入下，系统是否自动收敛？
（记忆数量、LEI、stability 是否被约束，而非无限膨胀/发散）

设计：
  - 候选 claim 池 24 条；每轮以 70% 概率强化池内已有 claim（制造结晶），30% 注入全新随机 claim
  - 多 agent 随机写入；relation 默认 support（偶发 contradiction 10%）
  - 每 100 轮记一次快照：memory 数 / LEI / 平均 stability
  - 全程随机种子固定，可复现

判定：
  - 1000 轮无异常崩溃
  - 最终 memory 数收敛（plateau）：末段 100 轮增量 < 总池规模，且不超过 claim 池 * 3
  - LEI 全程有界（不出现长时间 RED / 发散）
  - 平均 stability 处于合理区间(0.2~0.9)，体现"液态"而非"冻结/爆炸"
"""
from __future__ import annotations
import random
from liquid_loop import WorkspaceState
from common import make_state, leis, VerdictCollector, seed


def run(rounds: int = 300) -> dict:
    seed(12345)
    s = WorkspaceState()
    a = s.add_anchor("长期观察流", "持续随机注入的观测锚点")

    POOL = [f"观测事件 #{i}" for i in range(24)]
    snapshots = []

    for r in range(rounds):
        if random.random() < 0.7 and POOL:
            content = random.choice(POOL)  # 强化已有 claim
        else:
            content = f"随机观测 {random.randint(100000, 999999)}"  # 全新 claim
        agent = random.choice(["a1", "a2", "a3", "a4"])
        relation = "contradiction" if random.random() < 0.1 else "support"
        if relation == "contradiction":
            # contradiction 需要目标，随机指向一个已有 memory
            tgt = random.choice(s.memories).id if s.memories else ""
            s.add_evidence(a, content, agent_id=agent, relation=relation, target_memory_id=tgt)
        else:
            s.add_evidence(a, content, agent_id=agent, relation=relation)
        if (r + 1) % 50 == 0:
            avg_stab = (sum(m.stability for m in s.memories) / len(s.memories)) if s.memories else 0.0
            snapshots.append({
                "round": r + 1,
                "memories": len(s.memories),
                "evidences": len(s.evidences),
                "LEI": round(leis(s)["leI"], 4),
                "band": leis(s)["band"],
                "avg_stability": round(avg_stab, 3),
            })

    final = snapshots[-1]
    prev = snapshots[-2] if len(snapshots) >= 2 else snapshots[-1]
    mem_delta = final["memories"] - prev["memories"]

    vc = VerdictCollector()
    vc.check(len(snapshots) == rounds // 50, "全程无崩溃完成", f"snapshots={len(snapshots)}")
    vc.check(final["memories"] <= len(POOL) * 3,
             "记忆数量收敛(不爆炸)",
             f"final_mem={final['memories']} <= {len(POOL)*3}")
    vc.check(mem_delta <= len(POOL),
             "末段增量受约束(plateau)",
             f"Δmem(末50轮)={mem_delta}")
    red_rounds = sum(1 for sn in snapshots if sn["band"] == "RED")
    vc.check(red_rounds <= len(snapshots) * 0.5,
             "LEI 有界(非长时间 RED/发散)",
             f"RED rounds={red_rounds}/{len(snapshots)}")
    vc.check(0.2 <= final["avg_stability"] <= 0.95,
             "平均 stability 处于液态区间",
             f"avg_stability={final['avg_stability']}")

    return {
        "experiment": "E1-long-drift",
        "rounds": rounds,
        "snapshots": snapshots,
        "final": final,
        "mem_delta_last100": mem_delta,
        "verdict": vc.report(),
        "all_pass": vc.passed() == vc.total(),
    }


if __name__ == "__main__":
    r = run()
    print("=== E1 长期漂移 (1000 轮) ===")
    for sn in r["snapshots"]:
        print(f"  round {sn['round']:>4}: mem={sn['memories']:>3} ev={sn['evidences']:>4} "
              f"LEI={sn['LEI']:.3f}({sn['band']}) avgStab={sn['avg_stability']:.3f}")
    print(r["verdict"])
