"""蒸馏 #197/#198/#199/#203 落地实测：守卫原语 / 会话崩溃恢复 / 自适应 RAID 召回。

覆盖：
  - guard.py   : should_escalate / CapabilityMenu / confirm_gate（#197+#198）
  - session.py : SessionState / save/load / mark_abort / recover（#199）
  - recall_filter.adaptive_recall : 低载冗余验证 ↔ 高载分工扩容（#203）
零依赖；不依赖 8790 server，纯单元测试验证语义正确性。
"""
import json
from pathlib import Path

from liquid_loop.guard import (
    should_escalate,
    CapabilityMenu,
    confirm_gate,
    RISK_LEVELS,
)
from liquid_loop.session import (
    SessionState,
    save_session,
    load_session,
    mark_abort,
    recover,
)
from liquid_loop.recall_filter import adaptive_recall


# ───────────────────────── guard.py (#197 + #198) ─────────────────────────

def test_should_escalate_red_lines():
    # 三条红线任一命中即升级
    assert should_escalate(confidence=0.2) is True          # 低置信
    assert should_escalate(irreversible=True) is True       # 不可逆
    assert should_escalate(over_budget=True) is True        # 超预算
    # 全部安全 → 不升级
    assert should_escalate(confidence=0.9, irreversible=False,
                           over_budget=False) is False
    # 边界：置信=0.5 不触发低置信红线
    assert should_escalate(confidence=0.5) is False


def test_capability_menu_declare_and_risk():
    menu = CapabilityMenu()
    menu.declare("read_file", risk="low")
    menu.declare("delete_index", risk="high",
                 description="删除整个检索索引")
    assert menu.risk_of("read_file") == "low"
    assert menu.risk_of("delete_index") == "high"
    assert menu.risk_of("unknown") is None
    # high 风险需确认闸
    assert menu.requires_confirm("delete_index") is True
    assert menu.requires_confirm("read_file") is False
    # 菜单可序列化展示（协议层提示语，非沙箱）
    as_menu = menu.as_menu()
    assert as_menu["delete_index"]["risk"] == "high"


def test_capability_menu_bad_risk():
    menu = CapabilityMenu()
    try:
        menu.declare("x", risk="critical")
        raise AssertionError("应拒绝非法 risk")
    except ValueError:
        pass


def test_confirm_gate_intercepts_high_by_default():
    # high 风险默认拦截（交回人）
    assert confirm_gate("delete_index", risk="high") is False
    # auto_approve 用于 dry-run / 测试放行
    assert confirm_gate("delete_index", risk="high", auto_approve=True) is True
    # low/medium 风险直接放行
    assert confirm_gate("read", risk="low") is True
    assert confirm_gate("edit", risk="medium") is True
    # 未知风险按最高处置（拦截）
    assert confirm_gate("mystery") is False


def test_risk_levels_constant():
    assert RISK_LEVELS == ("low", "medium", "high")


# ───────────────────────── session.py (#199) ─────────────────────────

def test_session_roundtrip(tmp_path: Path):
    st = SessionState("s1", status="running")
    st.tool_calls.append({"tool": "remember", "args": {"k": "v"}})
    st.checkpoint = {"step": 3}
    p = save_session(st, root=tmp_path)
    assert p.exists()
    loaded = load_session("s1", root=tmp_path)
    assert loaded is not None
    assert loaded.session_id == "s1"
    assert loaded.tool_calls[0]["tool"] == "remember"
    assert loaded.checkpoint == {"step": 3}


def test_load_missing_session_returns_none(tmp_path: Path):
    assert load_session("nope", root=tmp_path) is None


def test_mark_abort_writes_flag_not_kill(tmp_path: Path):
    # 运行态
    save_session(SessionState("s2"), root=tmp_path)
    # 可控 abort：记录缺失 tool results，状态改 aborted，但进程照常
    st = mark_abort("s2", missing_tool_results=[{"tool": "recall", "id": "r1"}],
                    root=tmp_path)
    assert st.status == "aborted"
    assert st.missing_tool_results == [{"tool": "recall", "id": "r1"}]
    # 落盘可见
    raw = json.loads((tmp_path / ".liquid" / "sessions" / "s2.json").read_text(
        encoding="utf-8"))
    assert raw["status"] == "aborted"


def test_recover_fills_missing_and_reaches_done(tmp_path: Path):
    mark_abort("s3", missing_tool_results=[{"tool": "recall", "id": "r1",
                                            "recovered": True}],
               root=tmp_path)
    rep = recover("s3", root=tmp_path)
    assert rep["session_id"] == "s3"
    assert rep["filled"] == 1
    assert rep["missing"] == 0
    assert rep["status"] == "done"  # 补齐完毕 → 一致失败态


def test_recover_keeps_unrecovered(tmp_path: Path):
    mark_abort("s4", missing_tool_results=[{"tool": "recall", "id": "r1"}],
               root=tmp_path)
    rep = recover("s4", root=tmp_path)
    assert rep["filled"] == 0
    assert rep["missing"] == 1
    assert rep["status"] == "aborted"  # 仍有缺失 → 保持 aborted


def test_recover_missing_session(tmp_path: Path):
    rep = recover("ghost", root=tmp_path)
    assert rep["status"] == "missing"
    assert rep["filled"] == 0


# ────────────────── adaptive_recall (#203 自适应 RAID 路由) ──────────────────

def _cands():
    return [
        {"memory_id": "a", "content": "液环 禁 向量 记忆 系统", "score": 0.9},
        {"memory_id": "b", "content": "苹果 香蕉 西瓜", "score": 0.8},
        {"memory_id": "c", "content": "液环 自检 环节 审计", "score": 0.7},
        {"memory_id": "d", "content": "完全不同的主题 区块链", "score": 0.6},
        {"memory_id": "e", "content": "液环 向量 相关 内容", "score": 0.5},
    ]


def test_adaptive_low_load_redundant_verify():
    # load<0.5 → 冗余交叉验证，三路投票聚合
    out, rep = adaptive_recall("液环 向量", _cands(), load=0.2, k=5)
    assert rep["mode"] == "redundant_verify"
    # 相关候选(a)应被加权共识推到前面
    assert out[0]["memory_id"] == "a"
    # 每条带 raid_* 字段
    assert "raid_consensus" in out[0]
    assert "raid_fused" in out[0]


def test_adaptive_high_load_specialized_capacity():
    # load>=0.5 → 单路快排 top-k，按原 score 排序（不交叉验证）
    out, rep = adaptive_recall("液环 向量", _cands(), load=0.8, k=3)
    assert rep["mode"] == "specialized_capacity"
    assert [r["memory_id"] for r in out] == ["a", "b", "c"]  # 按 score 降序
    # 高载路径不附加 raid_* 字段（省算力）
    assert "raid_fused" not in out[0]


def test_adaptive_empty_input():
    out, rep = adaptive_recall("q", [], load=0.3)
    assert out == []
    assert rep["mode"] == "empty"


def test_adaptive_fail_open_on_bad_content():
    # 候选 content 非字符串 → 主分支 _tokens 抛 TypeError；fail-open 兜底只取 score 不受影响
    bad = [
        {"memory_id": "x", "content": ["not", "a", "string"], "score": 0.9},
        {"memory_id": "y", "content": "正常文本 液环", "score": 0.1},
    ]
    out, rep = adaptive_recall("液环 向量", bad, load=0.2, k=2)
    assert rep["mode"] == "fail_open"
    assert [r["memory_id"] for r in out] == ["x", "y"]
