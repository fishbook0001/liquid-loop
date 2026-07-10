from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid
from collections import Counter


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Liquid Loop Core Chain ──────────────────────────────────

def _nucleate(state: "WorkspaceState", anchor_id: str):
    """检查指定 Anchor 下的 Evidence，2+ 条则成核为 Memory"""
    group = [e for e in state.evidences if e.anchor_id == anchor_id]
    if len(group) < 2:
        return
    content_counts = Counter(e.content for e in group)
    for content, count in content_counts.items():
        if count >= 2:
            existing = [m for m in state.memories if m.content == content]
            if existing:
                continue
            evidence_ids = [e.id for e in group if e.content == content]
            confidence = min(count / len(group), 1.0)
            memory = Memory(
                content=content,
                evidence_ids=evidence_ids,
                confidence=confidence,
            )
            state.memories.append(memory)


def _decay_evidence(state: "WorkspaceState", anchor_id: str):
    """Stability decay: 每次添加 Evidence 时，同 Anchor 下的旧 Evidence 权重衰减"""
    for e in state.evidences:
        if e.anchor_id == anchor_id:
            e.weight = max(e.weight * 0.95, 0.1)


def _recalc_stability(state: "WorkspaceState", anchor_id: str):
    """基于证据权重更新 Anchor 稳定性"""
    anchor = next((a for a in state.anchors if a.id == anchor_id), None)
    if not anchor:
        return
    evs = [e for e in state.evidences if e.anchor_id == anchor_id]
    if not evs:
        anchor.stability = 1.0
        return
    # 稳定性 = 平均权重（权重越高越稳定）
    anchor.stability = sum(e.weight for e in evs) / len(evs)


@dataclass
class Anchor:
    id: str = field(default_factory=uid)
    name: str = ""
    description: str = ""
    created_at: str = field(default_factory=now)
    stability: float = 1.0       # 0.0=完全漂移, 1.0=完全稳定
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Evidence:
    id: str = field(default_factory=uid)
    anchor_id: str = ""
    content: str = ""
    timestamp: str = field(default_factory=now)
    weight: float = 1.0          # 证据权重, 默认1.0


@dataclass
class Memory:
    id: str = field(default_factory=uid)
    content: str = ""
    formed_at: str = field(default_factory=now)
    evidence_ids: list[str] = field(default_factory=list)  # 成核来源
    confidence: float = 0.0      # 0.0-1.0, 基于证据一致性


@dataclass
class Conflict:
    anchor_a: str = ""
    anchor_b: str = ""
    description: str = ""
    detected_at: str = field(default_factory=now)
    severity: float = 0.0        # 0.0-1.0


@dataclass
class StateSnapshot:
    timestamp: str = field(default_factory=now)
    entropy: float = 0.0
    anchor_count: int = 0
    evidence_count: int = 0
    memory_count: int = 0
    conflict_count: int = 0


@dataclass
class WorkspaceState:
    anchors: list[Anchor] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    snapshots: list[StateSnapshot] = field(default_factory=list)
    version: str = "0.1.0"
    updated_at: str = field(default_factory=now)

    def add_anchor(self, name: str, description: str = "") -> str:
        """添加锚点，返回 anchor_id"""
        anchor = Anchor(name=name, description=description)
        self.anchors.append(anchor)
        return anchor.id

    def add_evidence(self, anchor_id: str, content: str) -> str:
        """添加证据到指定锚点，触发液环自动链：成核→衰减→稳定性重算"""
        evidence = Evidence(anchor_id=anchor_id, content=content)
        self.evidences.append(evidence)
        # Liquid Loop 自动链
        _nucleate(self, anchor_id)
        _decay_evidence(self, anchor_id)
        _recalc_stability(self, anchor_id)
        return evidence.id
