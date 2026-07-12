"""Liquid Loop — Workspace Cognitive Runtime v0.5.1 (CPE-fused, proactive)"""
__version__ = "0.5.2"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
)
from .storage import load, save
from .entropy import calculate, calculate_detail, calculate as calculate_entropy