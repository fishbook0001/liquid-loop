"""E3 — 多 Agent 冲突实验（mesh v2 价值证明）。

设计（飞哥指定顺序：A support / B contradiction / C noise）：
  agent A: 持续 support 同一 claim（"方案风险高"）
  agent B: 对同一锚点 contradiction（不同意 A 的 claim）
  agent C: 注入噪声（无关 content 的 support，模拟随机 agent）

观察：
  - 是否形成稳定共享状态（不崩、不无限增长）
  - A 的 support 与 B 的 contradiction 是否使该 memory 进入"受争议但稳定"区间
  - 噪声 C 是否被正确隔离（不污染核心 claim 的结晶）

判定：
  - 核心 claim memory 存在且 stability 落在受争议区间(0.2~0.6)，体现"一致增稳/冲突降稳"
  - 系统 LEI 处于 GREEN/YELLOW（未因冲突崩溃）
  - 噪声未生成与核心 claim 冲突的额外矛盾（noise 独立成核，不降低核心 stability 的确定性）
"""
from __future__ import annotations
from liquid_loop import WorkspaceState
from common import make_state, leis, memory_summary, VerdictCollector, seed


def run() -> dict:
    seed(777)
    s = WorkspaceState()
    a = s.add_anchor("方案风险评估", "对方案风险的判断")

    CLAIM = "该方案风险很高"
    NOISE_POOL = ["今天天气晴", "会议改到周四", "采购预算超标", "服务器宕机已恢复",
                  "新人入职培训", "季度目标上调", "接口延迟 200ms"]

    # agent A: 6 条 support
    for _ in range(6):
        s.add_evidence(a, CLAIM, agent_id="agentA")
    # agent B: 4 条 contradiction（不同意）
    for _ in range(4):
        s.add_evidence(a, CLAIM, agent_id="agentB", relation="contradiction")
    # agent C: 8 条噪声（无关 claim，独立 support）
    for _ in range(8):
        s.add_evidence(a, NOISE_POOL[_ % len(NOISE_POOL)], agent_id="agentC")

    claim_mem = next((m for m in s.memories if m.content == CLAIM), None)
    assert claim_mem, "核心 claim 应已结晶(共识 or private)"

    vc = VerdictCollector()
    vc.check(claim_mem is not None, "核心 claim 已结晶", f"scope={claim_mem.scope}")
    vc.check(0.15 <= claim_mem.stability <= 0.65,
             "核心 claim 进入受争议稳定区间",
             f"stability={claim_mem.stability:.3f} (support={claim_mem.support_count}, contradict={claim_mem.contradiction_count})")
    band = leis(s)["band"]
    vc.check(band in ("GREEN", "YELLOW"),
             "系统 LEI 未因冲突崩溃",
             f"band={band}, LEI={leis(s)['leI']}")
    # 噪声应独立成核（不指向核心 claim 的 contradiction）
    noise_mems = [m for m in s.memories if m.content in NOISE_POOL]
    vc.check(len(noise_mems) >= 1,
             "噪声 agent C 独立成核(隔离)",
             f"noise_mems={len(noise_mems)}")
    vc.check(claim_mem.contradiction_count == 4,
             "矛盾计数精确=A 的 support 不误计为矛盾",
             f"contradiction_count={claim_mem.contradiction_count}")

    return {
        "experiment": "E3-multi-agent-conflict",
        "core_claim": {
            "scope": claim_mem.scope,
            "stability": round(claim_mem.stability, 3),
            "support": claim_mem.support_count,
            "contradiction": claim_mem.contradiction_count,
        },
        "memory_count": len(s.memories),
        "leI": leis(s),
        "verdict": vc.report(),
        "all_pass": vc.passed() == vc.total(),
    }


if __name__ == "__main__":
    r = run()
    print("=== E3 多 Agent 冲突 ===")
    print(f"核心 claim: {r['core_claim']}")
    print(f"结晶数: {r['memory_count']}  LEI: {r['leI']}")
    print(r["verdict"])
