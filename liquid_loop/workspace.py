from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid
import hashlib
import json
import os


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid.uuid4().hex[:12]


# ========== 链式哈希审计（借鉴 KFG MemoryGovernance）==========

class AuditChain:
    """轻量级审计链：每次变更追加 SHA256 链式哈希"""

    def __init__(self, audit_path: str):
        self._path = audit_path
        self._chain: list[str] = []
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            with open(self._path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        parts = line.split("|")
                        if len(parts) >= 2:
                            self._chain.append(parts[1])

    def append(self, event_type: str, data: str) -> str:
        prev = self._chain[-1] if self._chain else "genesis"
        chain_hash = hashlib.sha256(f"{event_type}:{data}:{prev}".encode()).hexdigest()[:16]
        self._chain.append(chain_hash)
        ts = now()
        with open(self._path, "a") as f:
            f.write(f"{ts}|{chain_hash}|{event_type}|{data}|{prev}\n")
        return chain_hash

    @property
    def root(self) -> str:
        return self._chain[-1] if self._chain else "genesis"

    def verify(self, entry: str) -> bool:
        """验证某条完整日志行是否匹配链中记录

        格式: timestamp|chain_hash|event_type|data|prev_hash
        """
        if "|" not in entry:
            return False
        parts = entry.strip().split("|")
        if len(parts) < 5:
            return False
        _, stored_hash, event_type, data, prev = parts[:5]
        expected = hashlib.sha256(f"{event_type}:{data}:{prev}".encode()).hexdigest()[:16]
        return expected == stored_hash


# --- 四维分类（借鉴 KFG 4D Classifier）---

# 价值密度等级
DensityLevel = str  # "high" | "medium" | "low"

# 认知阶段
CognitiveStage = str  # "raw" | "wip" | "crystallized" | "tooling"

# 流动性等级
LiquidityLevel = str  # "hot" | "warm" | "cold" | "frozen"


@dataclass
class Anchor:
    id: str = field(default_factory=uid)
    name: str = ""
    description: str = ""
    created_at: str = field(default_factory=now)

    # 稳定性（原）
    stability: float = 1.0       # 0.0=完全漂移, 1.0=完全稳定
    evidence_ids: list[str] = field(default_factory=list)

    # 【新增】四维分类标签（借鉴 KFG）
    value_density: str = "medium"  # high | medium | low
    cognitive_stage: str = "raw"   # raw | wip | crystallized | tooling
    liquidity: str = "warm"        # hot | warm | cold | frozen

    # 【新增】价值衰减指标（借鉴 KFG Factor.value 计算）
    access_count: int = 0          # 被引用/访问次数
    last_accessed: str = ""        # 最后访问时间
    value_score: float = 1.0       # 衰减后价值（0.0~1.0）

    # 【新增】锚定强度（基于证据支持度 + 访问活跃度）
    anchor_strength: float = 1.0   # 0.0~1.0

    def decay_value(self, factor: float = 0.95, evidence_count: int = 0) -> float:
        """价值衰减计算（双因模型，借鉴 KFG MemoryGovernance.check_decay）

        公式: score = freq_score × 0.4 + time_score × 0.6
        - freq_score: 使用频率（证据数+访问次数），上限1.0
        - time_score: 时间衰减（30天从1.0→0.0）
        """
        if not self.created_at:
            return 0.0
        try:
            created = datetime.fromisoformat(self.created_at)
        except ValueError:
            return 0.0
        now_dt = datetime.now(timezone.utc)
        days = (now_dt - created).days

        # 双因模型
        freq_score = min((evidence_count * 0.12 + self.access_count * 0.08), 1.0)
        time_score = max(0.0, 1.0 - days / 30)
        raw = (freq_score * 0.4 + time_score * 0.6) * (factor ** max(days, 0))
        self.value_score = max(0.0, min(1.0, raw))
        return self.value_score

    def recalc_strength(self, evidence_count: int) -> float:
        """锚定强度 = 证据支持度 × 活跃度"""
        evidence_factor = min(evidence_count / 5, 1.0)  # 5条证据满
        activity = self.value_score
        self.anchor_strength = evidence_factor * 0.6 + activity * 0.4
        self.anchor_strength = max(0.0, min(1.0, self.anchor_strength))
        return self.anchor_strength

    def auto_classify(self, evidence_count: int) -> dict:
        """自动推断四维分类标签（借鉴 KFG 4D Classifier 逻辑）

        根据证据数量、锚定强度、活跃度自动调整：
        - 价值密度: evidence多+强锚定 → high, 否则 low
        - 认知阶段: 强锚定+高价值 → crystallized, 弱→raw
        - 流动性: 最近有活动→hot, 否则→cold/frozen
        """
        self.decay_value(evidence_count=evidence_count)
        self.recalc_strength(evidence_count)

        # 价值密度
        if self.anchor_strength > 0.8 and evidence_count >= 3:
            self.value_density = "high"
        elif self.anchor_strength > 0.4 or evidence_count >= 1:
            self.value_density = "medium"
        else:
            self.value_density = "low"

        # 认知阶段
        if self.value_score > 0.7 and self.anchor_strength > 0.7:
            self.cognitive_stage = "crystallized"
        elif self.value_score > 0.4:
            self.cognitive_stage = "wip"
        elif evidence_count == 0:
            self.cognitive_stage = "raw"
        else:
            self.cognitive_stage = "wip"

        # 流动性
        if self.last_accessed:
            try:
                last = datetime.fromisoformat(self.last_accessed)
                gap_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
                if gap_hours < 24:
                    self.liquidity = "hot"
                elif gap_hours < 168:
                    self.liquidity = "warm"
                elif gap_hours < 720:
                    self.liquidity = "cold"
                else:
                    self.liquidity = "frozen"
            except ValueError:
                pass

        return {
            "value_density": self.value_density,
            "cognitive_stage": self.cognitive_stage,
            "liquidity": self.liquidity,
        }


@dataclass
class Evidence:
    id: str = field(default_factory=uid)
    anchor_id: str = ""
    content: str = ""
    timestamp: str = field(default_factory=now)
    weight: float = 1.0          # 证据权重, 默认1.0

    # 【新增】证据质量标签
    quality: str = "normal"      # strong | normal | weak


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


# 【新增】锚点关系（借鉴 KFG Relation + 传递闭包）
@dataclass
class AnchorRelation:
    source_id: str = ""
    target_id: str = ""
    relation_type: str = "relates_to"  # depends_on | relates_to | supersedes | conflicts_with
    weight: float = 1.0               # 0.0~1.0
    created_at: str = field(default_factory=now)


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
    # 【新增】锚点关系
    relations: list[AnchorRelation] = field(default_factory=list)
    # 【新增】审计链哈希（每次 save 自动更新）
    audit_chain_hash: str = "genesis"
    audit_prev_hash: str = ""
    version: str = "0.3.0"
    updated_at: str = field(default_factory=now)