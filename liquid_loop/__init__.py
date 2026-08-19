"""Liquid Loop — Workspace Cognitive Runtime v1.9.0 (禁向量·活态液态神经网络：成核门槛/反证轨/老化回收/原理优先成核/因果演化/软取代supersede+治理查询state_at/impact/duplicates+程序性记忆层procedural_memory+PerceptionGate因果共生门控)"""
__version__ = "1.9.0"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
)
from .storage import load, save, locked_state
from .entropy import calculate, calculate_detail, calculate as calculate_entropy
from .selfspin import LiquidSelfSpin
from .liquid_reweight import LiquidReweight
from .context_compress import ExtractiveCondenser, compress_context, CondenseReport, structured_note
from .self_eval import consensus_self_check, diverg_beta, majority_vote, u_opsd_step
from .recall_filter import recall_content_filter, content_aware_filter, cosine_dict
from .procedural_memory import (
    ProceduralMemory, ProceduralRegistry, procmem_recall, procmem_admit,
)
# 蒸馏落地的公共原语（零侵入，供 cli / server / agent loop 接入）
from .guard import should_escalate, confirm_gate, CapabilityMenu
from .session import SessionState, mark_abort, recover, save_session, load_session
from .recall_filter import adaptive_recall
from .rar import RARIndex, build_or_cache  # 公共检索 API（原 workspace 局部 import）
# TTL 回收入口：evict_expired 是 WorkspaceState 实例方法（见 workspace.py:254），
# 已随 WorkspaceState 一并导出；cli.prune 经 state.evict_expired() 调用。
