"""Liquid Loop — Workspace Cognitive Runtime v1.1.0 (v1.0.0 + 液态重排引擎 Liquid Reweight: τ(x)黏滞唤醒 + 液态召回, 实证 97% 精度)"""
__version__ = "1.1.0"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
    rhythmic_retrieve,
)
from .storage import load, save, locked_state
from .entropy import calculate, calculate_detail, calculate as calculate_entropy
from .selfspin import LiquidSelfSpin
