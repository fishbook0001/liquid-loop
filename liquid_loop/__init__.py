"""Liquid Loop — Workspace Cognitive Runtime v0.6.1 (CPE-fused, proactive, rhythmic sampling)"""
__version__ = "0.6.1"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
    rhythmic_retrieve,
)
from .storage import load, save
from .entropy import calculate, calculate_detail, calculate as calculate_entropy
