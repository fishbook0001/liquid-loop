"""Liquid Loop — Workspace Cognitive Runtime v0.9.2 (Layer-1 修复: 结构化投影层 + 冲突惩罚持久化 + 有效迭代 τ)"""
__version__ = "0.9.2"

from .workspace import (
    WorkspaceState, Anchor, Evidence, Memory, Conflict,
    AuditChain, CPERegularizer, SelfRefineEngine,
    rhythmic_retrieve,
)
from .storage import load, save, locked_state
from .entropy import calculate, calculate_detail, calculate as calculate_entropy
