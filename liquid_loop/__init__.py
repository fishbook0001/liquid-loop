"""Liquid Loop — Workspace Cognitive Runtime v0.8.0 (反证轨 + 时间动力学, CPE-fused, SEAL dual-opt, dual-track multi-agent, MESH v2 integration)"""
__version__ = "0.8.0"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
    rhythmic_retrieve,
)
from .storage import load, save, locked_state
from .entropy import calculate, calculate_detail, calculate as calculate_entropy
