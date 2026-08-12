"""Liquid Loop — Workspace Cognitive Runtime v1.8.4 (禁向量·活态液态神经网络：成核门槛/反证轨/老化回收/原理优先成核/因果演化/软取代supersede+治理查询state_at/impact/duplicates+程序性记忆层procedural_memory)"""
__version__ = "1.8.4"

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
