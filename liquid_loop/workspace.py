"""workspace — 薄壳门面（2026-09-24 从 1912 行拆为 models + state + ops×3）。

**薄壳符号集等价铁律**：`from .workspace import X` 零改动。
  实体：workspace_models（常量/dataclass） · workspace_state（WorkspaceState）
        · workspace_ops_{1,2,3}（方法分片 Mixin）
验证：split 前后 `dir(liquid_loop.workspace)` 冻结对比（57 名逐字一致）。

**本文件是唯一的 re-export 汇聚点**：原 workspace.py 的模块级命名空间全部由这里转发，
实体文件只保留自身实际引用的导入（依赖方向单向：facade → 实体，实体之间互不回头）。
F401/F403 在本文件由 pyproject `per-file-ignores` 豁免——它就是设计上的转发面。
"""
from __future__ import annotations

# ── 数据模型主表面：models 自有定义（dataclass / 枚举 / 常量）──
from .workspace_models import *  # noqa: F403
# `import *` 不带下划线名 → 显式补
from .workspace_models import (
    _CONSENSUS_PARTY_CACHE,
    _derive_lifecycle_thresholds,
    _load_consensus_parties,
)

# ── 主体类 ──
from .workspace_state import WorkspaceState

# ── 兼容性 re-export（薄壳符号集等价铁律）──────────────────────────────────
# 以下名字不参与门面逻辑，仅为「原 workspace.py 命名空间」的既有消费方保留。
# 实测契约（全仓 `from .workspace import`）：
#   tests/{test_workspace,test_lifecycle,test_anchor_lifecycle,test_self_evolve,
#          test_seal_persistence,test_peek_seal,test_auth_guard,test_entropy,
#          test_replay_pressure,test_semantica_borrow,test_attention_gain,
#          test_consensus_expansion,test_layer1_persistence,test_perception_gate,
#          test_nucleate_dual_track}.py
#   liquid_loop/{__init__,cli,storage}.py · liquid_loop/self_refine.py · examples/
# ⚠️ 删除任何一行前，必须同步核对上述契约消费方。
from .textutil import (
    now, uid, _get_version, _tokenize, _keyword_overlap, _judge_answer,
    _dissolve_votes_path, _load_dissolve_votes, _save_dissolve_votes,
)
from .audit import AuditChain
from .cpe import AGGREGATE_ANCHOR_NAMES, CPERegularizer
from .self_refine import SelfRefineEngine, meta_thinker_evaluate, meta_thinker_advice
from .guard import validate_content
from .cognitive_budget import CognitiveBudgetStabilizer

# ── 原文件遗留的标准库级命名（保持命名空间逐字等价，非契约依赖）──
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
# ⚠️ UP035 豁免：Dict/List 在此**不是**待现代化的旧注解，而是 57 名命名空间冻结集
#    的成员（原单体 workspace.py 的 dir() 含它们）。删除会破坏薄壳符号集等价铁律。
from typing import Any, Dict, List, Optional  # noqa: UP035
import hashlib
import json
import logging
import os
import re
import sys
import uuid
