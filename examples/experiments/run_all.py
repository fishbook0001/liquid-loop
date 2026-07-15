"""液环 v0.8 三实验运行器（飞哥指定顺序：E2 → E3 → E1）。

运行：python3 examples/experiments/run_all.py
产物：examples/experiments/REPORT_v0.8.json + 控制台摘要
"""
from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))  # 仓库根，确保可 import liquid_loop

from e2_recovery import run as e2
from e3_conflict import run as e3
from e1_drift import run as e1


def main():
    print("#" * 60)
    print("# 液环 v0.8 实验套件：E2(错误恢复) → E3(多体冲突) → E1(长期漂移)")
    print("#" * 60)

    r2 = e2()
    print(f"\n[E2] all_pass={r2['all_pass']}  wrong={r2['final']['wrong_stability']} "
          f"true={r2['final']['true_stability']}  band={r2['leI_band']}")

    r3 = e3()
    print(f"\n[E3] all_pass={r3['all_pass']}  core_stab={r3['core_claim']['stability']} "
          f"mem={r3['memory_count']}  LEI={r3['leI']['leI']}({r3['leI']['band']})")

    r1 = e1()
    print(f"\n[E1] all_pass={r1['all_pass']}  rounds={r1['rounds']}  final_mem={r1['final']['memories']} "
          f"LEI={r1['final']['LEI']}({r1['final']['band']})")

    report = {
        "suite": "liquid-loop v0.8 experiments",
        "order": ["E2-error-recovery", "E3-multi-agent-conflict", "E1-long-drift"],
        "E2": r2,
        "E3": r3,
        "E1": {k: v for k, v in r1.items() if k != "snapshots"},  # snapshots 单独存
        "E1_snapshots": r1["snapshots"],
    }
    out = os.path.join(os.path.dirname(__file__), "REPORT_v0.8.json")
    with open(out, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {out}")

    overall = r2["all_pass"] and r3["all_pass"] and r1["all_pass"]
    print(f"\n=== 总判定: {'ALL PASS ✅' if overall else 'SOME FAIL ❌'} ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
