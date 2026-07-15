"""液环 v0.8 实验公共工具。

提供：
- make_state(): 干净的 WorkspaceState
- leis(state): 返回当前 LEI(entropy) 与状态等级(GREEN/YELLOW/RED)
- memory_summary(state): 列出所有结晶 memory 的 stability/contradiction
- verdict(cond, name): 收集 PASS/FAIL 判定
"""
from __future__ import annotations
import random
from liquid_loop import WorkspaceState, calculate


def make_state() -> WorkspaceState:
    return WorkspaceState()


def leis(state: WorkspaceState) -> dict:
    """Liquid Entropy Index（非物理熵，系统稳定流形偏离度）。"""
    e = calculate(state)
    if e < 0.3:
        band = "GREEN"
    elif e < 0.6:
        band = "YELLOW"
    else:
        band = "RED"
    return {"leI": round(e, 4), "band": band}


def memory_summary(state: WorkspaceState) -> list:
    out = []
    for m in state.memories:
        out.append({
            "content": m.content[:40],
            "scope": m.scope,
            "confidence": round(m.confidence, 3),
            "stability": round(m.stability, 3),
            "support": m.support_count,
            "contradiction": m.contradiction_count,
        })
    return out


class VerdictCollector:
    def __init__(self) -> None:
        self.results: list = []

    def check(self, cond: bool, name: str, detail: str = "") -> bool:
        self.results.append({"name": name, "pass": bool(cond), "detail": detail})
        return bool(cond)

    def passed(self) -> int:
        return sum(1 for r in self.results if r["pass"])

    def total(self) -> int:
        return len(self.results)

    def report(self) -> str:
        lines = []
        for r in self.results:
            mark = "PASS" if r["pass"] else "FAIL"
            lines.append(f"  [{mark}] {r['name']} — {r['detail']}")
        lines.append(f"  汇总: {self.passed()}/{self.total()} 通过")
        return "\n".join(lines)


def seed(seed_val: int = 42) -> None:
    random.seed(seed_val)
