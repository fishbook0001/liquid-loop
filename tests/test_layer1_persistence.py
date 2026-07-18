"""Layer-1 修复回归测试：有效迭代 τ 持久化 + 可替换结构化投影层 + 冲突惩罚持久化。

验证 v0.9.2 引入的修复在 save/load 往返后不丢失状态，且不破坏向后兼容。
"""
import pytest
from pathlib import Path

from liquid_loop.workspace import WorkspaceState
from liquid_loop.storage import save, load


def test_effective_iteration_persists_across_reload(tmp_path: Path):
    """τ=有效迭代计数必须随 save/load 往返；否则重启后归零→强化门控恒真→时间衰减失效。"""
    ws = WorkspaceState()
    a = ws.add_anchor("x")
    ws.add_evidence(a, "e1 support")
    ws.add_evidence(a, "e2 support")
    ws.step(dt=1)
    ws.step(dt=1)
    ws.step(dt=1)
    assert ws._iteration == 3, f"_iteration 应推进到 3，实为 {ws._iteration}"

    save(ws, tmp_path)
    loaded = load(tmp_path)
    assert loaded._iteration == 3, f"重载后 _iteration 丢失，实为 {loaded._iteration}"


def test_canon_fn_structured_projection_triggers_true_conflict():
    """canon_fn 注入后按 CP 精确分组：同 CP 内 support+contradiction 异 relation → 真冲突。"""
    ws = WorkspaceState()
    # 结构化投影：取首词为 CP（离散键，非向量/embedding——守住 North-Star 禁向量约束）
    ws.canon_fn = lambda c: c.strip().split()[0]
    a = ws.add_anchor("claim")
    ws.add_evidence(a, "苹果很好吃 因为甜", relation="support")
    ws.add_evidence(a, "苹果很好吃 因为脆", relation="contradiction")  # 同 CP 异 relation

    assert len(ws.conflicts) == 1, f"应检测到 1 条真冲突，实为 {len(ws.conflicts)}"
    assert "Projection Layer" in ws.conflicts[0].description
    assert ws.anchors[0].conflict_penalty == 0.9, "冲突后惩罚乘子应降为 0.9"
    # 惩罚乘入 stability（非直接改 stability，故 recalc 后不丢）
    assert ws.anchors[0].stability < 1.0


def test_conflict_penalty_persists_across_reload(tmp_path: Path):
    """conflict_penalty 累积乘子必须持久化；旧实现直接改 stability 会被 recalc 覆盖。"""
    ws = WorkspaceState()
    ws.canon_fn = lambda c: c.strip().split()[0]
    a = ws.add_anchor("claim")
    ws.add_evidence(a, "猫很可爱 因为黏人", relation="support")
    ws.add_evidence(a, "猫很可爱 因为高冷", relation="contradiction")

    penalty_before = ws.anchors[0].conflict_penalty
    assert penalty_before == 0.9

    save(ws, tmp_path)
    loaded = load(tmp_path)
    assert loaded.anchors[0].conflict_penalty == 0.9, "重载后冲突惩罚丢失"
    assert len(loaded.conflicts) == 1, "重载后冲突记录丢失"
    # canon_fn 是运行时注入项，save 前应被 pop，重载后回退 None（向后兼容路径）
    assert loaded.canon_fn is None, "canon_fn 不应被持久化（运行时注入项）"


def test_canon_fn_none_falls_back_to_keyword_overlap():
    """canon_fn=None → 回退旧关键词重叠行为，保证生产向后兼容（不崩、能检测冲突）。"""
    ws = WorkspaceState()  # canon_fn 默认 None
    a = ws.add_anchor("topic")
    ws.add_evidence(a, "量子计算利用叠加态加速", relation="support")
    ws.add_evidence(a, "古典吉他音色温暖动人", relation="support")
    # 两组关键词零重叠 → 平均一致度 < 默认阈值 0.2 → 触发保守冲突标记
    assert len(ws.conflicts) == 1, "回退路径应仍能检测低重叠潜在冲突"
    assert "一致度" in ws.conflicts[0].description
