"""workspace_state — WorkspaceState 主体：dataclass 字段 + 三个 Mixin 组合。

2026-09-24 从 workspace.py 拆分。方法体分片见 workspace_ops_{1,2,3}.py，
Mixin 内 `self.xxx` 跨片调用由 Python MRO 在运行期解析，语义与单体类一致。
"""
from __future__ import annotations

from .textutil import (
    now,
    _get_version,
)


from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable







from .workspace_models import (
    Anchor,
    AnchorRelation,
    Conflict,
    Evidence,
    Memory,
    StateSnapshot,
    WorkingMemoryItem,
)
from .workspace_ops_1 import WorkspaceOps1
from .workspace_ops_2 import WorkspaceOps2
from .workspace_ops_3 import WorkspaceOps3

@dataclass
class WorkspaceState(WorkspaceOps1, WorkspaceOps2, WorkspaceOps3):
    anchors: list[Anchor] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    snapshots: list[StateSnapshot] = field(default_factory=list)
    relations: list[AnchorRelation] = field(default_factory=list)
    audit_chain_hash: str = "genesis"
    audit_prev_hash: str = ""
    version: str = field(default_factory=_get_version)
    updated_at: str = field(default_factory=now)
    # 【v0.4.0】后向自进化状态（借鉴 MemMA 原位自进化）
    self_refine_probes: list[dict] = field(default_factory=list)
    self_refine_results: list[dict] = field(default_factory=list)
    self_refine_repair_count: int = 0
    # 【v2.0.5】即时觉醒模块：越界事件实时检测与日志
    instant_events: list[dict] = field(default_factory=list)  # 即时越界事件日志（最近100条）
    instant_awareness_stats: dict = field(default_factory=lambda: {"total_events": 0, "detected": 0, "awareness_rate": 0.0})
    instant_recent_ops: list[dict] = field(default_factory=list)  # 短期操作记忆（用于重复事件检测，最近50条）
    # 【v0.5.0】CPE 正则化状态（借鉴 UIUC CPE 论文 arXiv:2605.09315）
    regularized_evidences: list[str] = field(default_factory=list)  # 已通过正则化检查的证据ID
    blocked_evidences: list[str] = field(default_factory=list)      # 被正则化拦截的证据ID
    cpe_erosion_warnings: list[dict] = field(default_factory=list)  # 能力侵蚀告警
    cpe_regularization_count: int = 0                               # 累计正则化干预次数
    overlap_cache: dict = field(default_factory=dict)  # 缓存关键词重叠度
    _iteration: int = 0  # 有效状态更新次数（τ = Effective Iteration）；时间动力学以之为时间变量而非墙钟
    canon_fn: Callable[..., Any] | None = None  # 可替换 Projection Layer（Layer-1 修复）：注入后冲突检测复用此投影，None=旧关键词重叠回退
    # 【v2.1.0】L1工作记忆层：短期任务上下文缓冲，TTL+容量上限，可提升为长期记忆
    working_memory: list[WorkingMemoryItem] = field(default_factory=list)
    working_memory_capacity: int = 50  # L1工作记忆容量上限
    working_memory_default_ttl_hours: int = 24  # 默认TTL（小时）
