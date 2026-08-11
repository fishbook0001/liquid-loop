"""Liquid Loop — Workspace Cognitive Runtime v1.8.1 (禁向量·活态液态神经网络：成核门槛/反证轨/老化回收/原理优先成核/因果演化/软取代supersede)"""
__version__ = "1.8.1"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
)
from .storage import load, save, locked_state
from .entropy import calculate, calculate_detail, calculate as calculate_entropy
from .selfspin import LiquidSelfSpin
from .liquid_reweight import LiquidReweight
