"""
Liquid Loop — Self-Organizing Cognitive Memory for AI Agents

A zero-LLM-dependency cognitive runtime implementing the Liquid Loop theory:
Anchor → Evidence → Memory Nucleation with four-dimensional entropy monitoring.

Core Concepts:
- Anchor: Cognitive focus points with stability tracking
- Evidence: Observations that accumulate under anchors (exponential decay)
- Memory: Crystallized knowledge from 2+ consistent evidences
- Entropy: Four-dimensional cognitive health metric (drift/conflict/fragmentation/gap)

Usage:
    from liquid_loop import WorkspaceState, Anchor, Evidence, Memory
    from liquid_loop import load, save, calculate_entropy
    from liquid_loop.cli import main as cli_main

    # Quick start
    state = WorkspaceState()
    anchor_id = state.add_anchor("核心使命", "系统的核心目标")
    state.add_evidence(anchor_id, "用户偏好简洁输出")
    state.add_evidence(anchor_id, "用户偏好简洁输出")  # 2次一致 -> 结晶
    print(f"结晶记忆: {len(state.memories)} 条")

CLI:
    python -m liquid_loop init
    python -m liquid_loop anchor_add "项目目标" "完成液环论文"
    python -m liquid_loop evidence_add "项目目标" "已完成实证数据收集"
    python -m liquid_loop status
"""

from liquid_loop.workspace import (
    WorkspaceState,
    Anchor,
    Evidence,
    Memory,
    Conflict,
    StateSnapshot,
)

from liquid_loop.storage import load, save, LIQUID_DIR, STATE_FILE

from liquid_loop.entropy import calculate_entropy

__version__ = "0.1.0"
__author__ = "Fei Ge"
__license__ = "MIT"

__all__ = [
    # Core data models
    "WorkspaceState",
    "Anchor",
    "Evidence",
    "Memory",
    "Conflict",
    "StateSnapshot",
    # Persistence
    "load",
    "save",
    "LIQUID_DIR",
    "STATE_FILE",
    # Entropy
    "calculate_entropy",
    "EntropyComponents",
    # CLI
    "cli_main",
]

# Lazy import CLI to avoid click dependency at import time
def cli_main():
    from liquid_loop.cli import main
    return main()