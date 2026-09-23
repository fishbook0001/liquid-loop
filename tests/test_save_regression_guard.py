"""save() 灾难性回退护栏验证（2026-08-29 根治）。

独立可运行: python tests/test_save_regression_guard.py
也可被 pytest 收集(函数名 test_*)。
"""
import sys
import tempfile
from pathlib import Path

# 确保从本地源码导入(而非已安装副本)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须先 insert 源码路径再 import（否则会导入已安装副本）→ E402 为有意为之
from liquid_loop import WorkspaceState, load, save  # noqa: E402
from liquid_loop.storage import StateRegressionGuardError, GUARD_MIN, GUARD_RATIO  # noqa: E402


def _seed(state, n):
    for i in range(n):
        state.add_anchor(f"a{i}", f"anchor {i}")  # 副作用是断言前提，赋值无用
        state.add_evidence(f"a{i}", f"evidence content {i}", agent_id="agent_test")


def test_guard_blocks_catastrophic_regression():
    tmp = Path(tempfile.mkdtemp())
    st = WorkspaceState()
    _seed(st, 200)
    save(st, tmp)
    assert len(load(tmp).evidences) == 200, "seed failed"

    # 灾难性部分态(2 条)覆盖全量(200) → 必须被拒
    partial = WorkspaceState()
    partial.add_anchor("x", "x")
    partial.add_evidence("x", "only one", agent_id="agent_test")
    try:
        save(partial, tmp)
        raise AssertionError("FAIL: 护栏未拦截灾难性回退")
    except StateRegressionGuardError:
        pass
    # 磁盘不变, 仍是 200
    assert len(load(tmp).evidences) == 200, "FAIL: 磁盘被部分态覆盖"
    print("[PASS] 护栏拦截灾难性回退, 磁盘保留全量")


def test_guard_allows_normal_growth():
    tmp = Path(tempfile.mkdtemp())
    st = WorkspaceState()
    _seed(st, 200)
    save(st, tmp)
    st2 = load(tmp)
    st2.add_anchor("new", "new")
    st2.add_evidence("new", "added", agent_id="agent_test")
    save(st2, tmp)  # 正常增量, 不得抛
    assert len(load(tmp).evidences) == 201, "FAIL: 正常增量被误拦"
    print("[PASS] 正常增量写入放行(201)")


def test_guard_skips_small_workspace():
    tmp = Path(tempfile.mkdtemp())
    st = WorkspaceState()
    st.add_anchor("a", "a")
    st.add_evidence("a", "e", agent_id="x")
    save(st, tmp)  # 磁盘=1 < GUARD_MIN → 护栏跳过
    assert len(load(tmp).evidences) == 1, "FAIL: 小工作区写入异常"
    print(f"[PASS] 小工作区(<GUARD_MIN={GUARD_MIN})放行")


def test_guard_ratio_constant():
    # 2026-09-21 同步：GUARD_MIN 由 50 降为 10（storage.py 坑37修复"保护小工作区"），
    # 常量语义 = 「磁盘全量达此值才启用护栏」，值越小护栏启用越早（对小库更安全）。
    assert GUARD_RATIO == 0.1 and GUARD_MIN == 10
    print("[PASS] 阈值常量 GUARD_RATIO=0.1 GUARD_MIN=10")


if __name__ == "__main__":
    test_guard_ratio_constant()
    test_guard_skips_small_workspace()
    test_guard_allows_normal_growth()
    test_guard_blocks_catastrophic_regression()
    print("\nALL GUARD TESTS PASSED ✓")
