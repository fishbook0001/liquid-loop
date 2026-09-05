"""Liquid Loop — Workspace Cognitive Runtime v2.0.1 (禁向量·活态液态神经网络：成核门槛/反证轨/老化回收/原理优先成核/因果演化/软取代supersede+治理查询state_at/impact/duplicates+程序性记忆层procedural_memory+PerceptionGate因果共生门控)"""
__version__ = "2.0.4"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
)
from .storage import load, save, locked_state, StateRegressionGuardError
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
from .guard import should_escalate, confirm_gate, CapabilityMenu, PerceptionGate
from .dsh_reversible import ReversibleRegistry  # 可逆副作用底座（蒸馏 DeepSeek Harness #204，2026-08-28 接活）
from .session import SessionState, mark_abort, recover, save_session, load_session
from .recall_filter import adaptive_recall
from .rar import RARIndex, build_or_cache  # 公共检索 API（原 workspace 局部 import）
# TTL 回收入口：evict_expired 是 WorkspaceState 实例方法（见 workspace.py:254），
# 已随 WorkspaceState 一并导出；cli.prune 经 state.evict_expired() 调用。

# 5大洞察工程落地（2026-09-02 基于自主学习D527-D546批次蒸馏提炼）
from .generative_recall import GenerativeRecall, generative_recall  # 洞察一：生成式记忆检索
from .hebbian_association import HebbianAssociation, update_hebbian_associations  # 洞察二：赫布关联引擎
from .regeneration_metrics import RegenerationMetrics, check_regeneration_health  # 洞察三：记忆再生抗衰
from .dual_engine_monitor import DualEngineMonitor, dual_engine_monitor_decision  # 洞察四：双引擎监控
# 洞察五：LNN设计原则文档见 docs/LNN_DESIGN_PRINCIPLES.md
