"""E2 — 错误记忆恢复实验（v0.8 核心实验）。

问题：系统能否主动遗忘错误记忆并恢复正确记忆？
（区别于"带衰减的数据库"——衰减只是被动流失，纠错需要反证轨主动降稳。）

设计：
  Phase 1 (80% 错误): 注入 8 条错误 claim 的 support 证据 -> 错误 memory 高稳定结晶
  Phase 2 (20% 真实): 注入 2 条正确 claim 的 support 证据 -> 正确 memory 低稳定结晶
  Phase 3 (纠错):     持续注入正确 support + 对错误 memory 的 contradiction 反证
  Phase 4 (时间推进): step() 多次，无新错误支持 -> 错误 memory 应衰减

判定：
  - 错误 memory 的 stability 在纠错后应显著低于正确 memory
  - 错误 memory stability 跌破遗忘阈值(0.3) 或 < 正确 memory stability
  - 正确 memory 成为主导(最高 stability)
"""
from __future__ import annotations
from liquid_loop import WorkspaceState
from common import make_state, leis, memory_summary, VerdictCollector, seed


def run() -> dict:
    seed(20260715)
    s = make_state()
    a = s.add_anchor("模型参数规模", "某开源模型的参数量事实")

    WRONG = "该模型参数量为 70 亿"
    TRUE = "该模型参数量为 700 亿"

    # Phase 1: 80% 错误
    for _ in range(8):
        s.add_evidence(a, WRONG)
    # Phase 2: 20% 真实
    for _ in range(2):
        s.add_evidence(a, TRUE)

    wrong_mem = next((m for m in s.memories if m.content == WRONG), None)
    true_mem = next((m for m in s.memories if m.content == TRUE), None)
    assert wrong_mem and true_mem, "两记忆均应已结晶"

    after_inject = {
        "wrong_stability": round(wrong_mem.stability, 3),
        "true_stability": round(true_mem.stability, 3),
        "wrong_support": wrong_mem.support_count,
        "true_support": true_mem.support_count,
    }

    # Phase 3: 纠错——强化真实 + 反证错误
    for _ in range(6):
        s.add_evidence(a, TRUE)  # 强化真实
    for _ in range(6):
        s.add_evidence(a, TRUE, relation="contradiction", target_memory_id=wrong_mem.id)

    # Phase 4: 时间推进（无新错误支持）
    for _ in range(5):
        s.step(dt=5, decay_rate=0.05)

    trail = []
    for _ in range(8):
        s.step(dt=5, decay_rate=0.05)
        trail.append(round(wrong_mem.stability, 3))

    vc = VerdictCollector()
    vc.check(wrong_mem.stability < true_mem.stability,
             "错误记忆稳定性低于正确记忆",
             f"wrong={wrong_mem.stability:.3f} < true={true_mem.stability:.3f}")
    vc.check(wrong_mem.stability < 0.3,
             "错误记忆跌破遗忘阈值(0.3)",
             f"wrong_stability={wrong_mem.stability:.3f}")
    vc.check(true_mem.stability >= 0.6,
             "正确记忆保持高稳定(主导)",
             f"true_stability={true_mem.stability:.3f}")
    vc.check(trail[-1] <= trail[0],
             "时间推进下错误记忆单调不增",
             f"trail {trail[0]}->{trail[-1]}")

    return {
        "experiment": "E2-error-memory-recovery",
        "after_inject": after_inject,
        "final": {
            "wrong_stability": round(wrong_mem.stability, 3),
            "true_stability": round(true_mem.stability, 3),
            "wrong_contradiction": wrong_mem.contradiction_count,
            "true_support": true_mem.support_count,
        },
        "leI_band": leis(s)["band"],
        "verdict": vc.report(),
        "all_pass": vc.passed() == vc.total(),
    }


if __name__ == "__main__":
    r = run()
    print("=== E2 错误记忆恢复 ===")
    print(f"注入后: {r['after_inject']}")
    print(f"最终:   {r['final']}")
    print(f"LEI 等级: {r['leI_band']}")
    print(r["verdict"])
