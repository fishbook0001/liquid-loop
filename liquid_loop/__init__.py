"""Liquid Loop — Workspace Cognitive Runtime v0.7.0 (CPE-fused, SEAL dual-opt, dual-track multi-agent, MESH v2 integration)"""
__version__ = "0.7.0"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
    rhythmic_retrieve,
)
from .storage import load, save, locked_state
from .entropy import calculate, calculate_detail, calculate as calculate_entropy
