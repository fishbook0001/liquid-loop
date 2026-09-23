"""蒸馏 #199 运行时健壮性：会话状态持久化 + 崩溃恢复 + 可控 abort。

Pi Harness v2 把简单 agent loop 升级为运行时：Session 拆分、多 Lane、操作记录落盘、
崩溃恢复、可控中止（写标记而非杀进程）、补齐缺失 tool results → 失败语义简洁、可续。

液环本仓库是记忆后端（无长任务 loop），本模块提供**通用原语**，供未来 agent loop /
8790 server 接入：会话状态落盘、abort 写标记（不杀进程）、崩溃后补齐缺失 tool results
重建一致失败态。零依赖：仅标准库。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _sid_dir(root: Path) -> Path:
    d = root / ".liquid" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionState:
    """一次长任务的运行时状态：可落盘、可重建、可 abort。"""

    def __init__(self, session_id: str, status: str = "running"):
        self.session_id = session_id
        self.status = status  # running | aborted | done
        self.tool_calls = []            # 已发起的 tool call 记录
        self.missing_tool_results = []  # 崩溃/abort 时缺失的 tool result（待补齐）
        self.checkpoint = {}            # 任意可序列化检查点
        self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "tool_calls": self.tool_calls,
            "missing_tool_results": self.missing_tool_results,
            "checkpoint": self.checkpoint,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SessionState:
        s = cls(d.get("session_id", ""), d.get("status", "running"))
        s.tool_calls = d.get("tool_calls", [])
        s.missing_tool_results = d.get("missing_tool_results", [])
        s.checkpoint = d.get("checkpoint", {})
        s.updated_at = d.get("updated_at", _now())
        return s


def save_session(state: SessionState, root: Path | None = None) -> Path:
    """会话状态落盘（崩溃后可重建）。

    root 缺省在**调用时**解析 cwd。原写法 `root: Path = Path.cwd()` 的默认值在
    **import 时求值一次**（定义期快照），import 之后进程 chdir 不会生效 →
    静默写到错误的目录。改哨兵值 + 调用期解析消除该 latent 缺陷（B008）。
    """
    root = root or Path.cwd()
    p = _sid_dir(root) / f"{state.session_id}.json"
    p.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def load_session(session_id: str, root: Path | None = None) -> SessionState | None:
    root = root or Path.cwd()
    p = _sid_dir(root) / f"{session_id}.json"
    if not p.exists():
        return None
    return SessionState.from_dict(json.loads(p.read_text(encoding="utf-8")))


def mark_abort(session_id: str, missing_tool_results: list | None = None,
               root: Path | None = None) -> SessionState:
    """可控 abort：写标记而非杀进程。

    记录缺失的 tool results（崩溃/中止时未返回的），供 recover 补齐 →
    用户看到一致失败态而非崩坏半成品。
    """
    root = root or Path.cwd()
    st = load_session(session_id, root) or SessionState(session_id)
    st.status = "aborted"
    st.missing_tool_results = missing_tool_results or []
    st.updated_at = _now()
    save_session(st, root)
    return st


def recover(session_id: str, root: Path | None = None) -> dict:
    """崩溃恢复：补齐缺失 tool results → 重建一致失败态。

    返回 {session_id, status, filled: 补齐条数, missing: 剩余缺失}。
    失败语义简洁：aborted 会话不丢半成品，补齐后标记为 done(失败一致)。
    """
    root = root or Path.cwd()
    st = load_session(session_id, root)
    if st is None:
        return {"session_id": session_id, "status": "missing", "filled": 0,
                "missing": 0}
    filled = 0
    still = []
    for item in st.missing_tool_results:
        # 简化补齐：标记为已回填（真实场景由 caller 提供 result）
        if isinstance(item, dict) and item.get("recovered"):
            filled += 1
        else:
            still.append(item)
    st.missing_tool_results = still
    if not still:
        st.status = "done"  # 失败一致态：补齐完毕
    st.updated_at = _now()
    save_session(st, root)
    return {"session_id": session_id, "status": st.status,
            "filled": filled, "missing": len(still)}
