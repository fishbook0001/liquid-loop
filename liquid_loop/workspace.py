from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import uuid
from pathlib import Path
import hashlib
import json
import os
import re
from collections import Counter


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
        """验证某条完整日志行是否匹配链中记录"""
        if "|" not in entry:
            return False
        parts = entry.strip().split("|")
        if len(parts) < 5:
            return False
        _, stored_hash, event_type, data, prev = parts[:5]
        expected = hashlib.sha256(f"{event_type}:{data}:{prev}".encode()).hexdigest()[:16]
        return expected == stored_hash


# --- 四维分类（借鉴 KFG 4D Classifier）---
DensityLevel = str  # "high" | "medium" | "low"
CognitiveStage = str  # "raw" | "wip" | "crystallized" | "tooling"
LiquidityLevel = str  # "hot" | "warm" | "cold" | "frozen"


@dataclass
class Anchor:
    id: str = field(default_factory=uid)
    name: str = ""
    description: str = ""
    created_at: str = field(default_factory=now)
    stability: float = 1.0
    evidence_ids: list[str] = field(default_factory=list)
    value_density: str = "medium"
    cognitive_stage: str = "raw"
    liquidity: str = "warm"
    access_count: int = 0
    last_accessed: str = ""
    value_score: float = 1.0
    anchor_strength: float = 1.0

    def decay_value(self, factor: float = 0.95, evidence_count: int = 0) -> float:
        if not self.created_at:
            return 0.0
        try:
            created = datetime.fromisoformat(self.created_at)
        except ValueError:
            return 0.0
        now_dt = datetime.now(timezone.utc)
        days = (now_dt - created).days
        freq_score = min((evidence_count * 0.12 + self.access_count * 0.08), 1.0)
        time_score = max(0.0, 1.0 - days / 30)
        raw = (freq_score * 0.4 + time_score * 0.6) * (factor ** max(days, 0))
        self.value_score = max(0.0, min(1.0, raw))
        return self.value_score

    def recalc_strength(self, evidence_count: int) -> float:
        evidence_factor = min(evidence_count / 5, 1.0)
        activity = self.value_score
        self.anchor_strength = evidence_factor * 0.6 + activity * 0.4
        self.anchor_strength = max(0.0, min(1.0, self.anchor_strength))
        return self.anchor_strength

    def auto_classify(self, evidence_count: int) -> dict:
        self.decay_value(evidence_count=evidence_count)
        self.recalc_strength(evidence_count)
        if self.anchor_strength > 0.8 and evidence_count >= 3:
            self.value_density = "high"
        elif self.anchor_strength > 0.4 or evidence_count >= 1:
            self.value_density = "medium"
        else:
            self.value_density = "low"
        if self.value_score > 0.7 and self.anchor_strength > 0.7:
            self.cognitive_stage = "crystallized"
        elif self.value_score > 0.4:
            self.cognitive_stage = "wip"
        elif evidence_count == 0:
            self.cognitive_stage = "raw"
        else:
            self.cognitive_stage = "wip"
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
        return {"value_density": self.value_density, "cognitive_stage": self.cognitive_stage, "liquidity": self.liquidity}


@dataclass
class Evidence:
    id: str = field(default_factory=uid)
    anchor_id: str = ""
    content: str = ""
    timestamp: str = field(default_factory=now)
    weight: float = 1.0
    quality: str = "normal"


@dataclass
class Memory:
    id: str = field(default_factory=uid)
    content: str = ""
    formed_at: str = field(default_factory=now)
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class Conflict:
    anchor_a: str = ""
    anchor_b: str = ""
    description: str = ""
    detected_at: str = field(default_factory=now)
    severity: float = 0.0


@dataclass
class AnchorRelation:
    source_id: str = ""
    target_id: str = ""
    relation_type: str = "relates_to"
    weight: float = 1.0
    created_at: str = field(default_factory=now)


@dataclass
class StateSnapshot:
    timestamp: str = field(default_factory=now)
    entropy: float = 0.0
    anchor_count: int = 0
    evidence_count: int = 0
    memory_count: int = 0
    conflict_count: int = 0


def _get_version() -> str:
    """从 pyproject.toml 读取版本号"""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text())
        return data.get("project", {}).get("version", "0.5.4")
    return "0.5.4"

@dataclass
class WorkspaceState:
    anchors: list[Anchor] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    snapshots: list[StateSnapshot] = field(default_factory=list)
    relations: list[AnchorRelation] = field(default_factory=list)
    audit_chain_hash: str = "genesis"
    audit_prev_hash: str = ""
    version: str = field(default_factory=_get_version)
    updated_at: str = field(default_factory=now)
    # 【v0.4.0】后向自进化状态（借鉴 MemMA 原位自进化）
    self_refine_probes: list[dict] = field(default_factory=list)
    self_refine_results: list[dict] = field(default_factory=list)
    self_refine_repair_count: int = 0
    # 【v0.5.0】CPE 正则化状态（借鉴 UIUC CPE 论文 arXiv:2605.09315）
    regularized_evidences: list[str] = field(default_factory=list)  # 已通过正则化检查的证据ID
    blocked_evidences: list[str] = field(default_factory=list)      # 被正则化拦截的证据ID
    cpe_erosion_warnings: list[dict] = field(default_factory=list)  # 能力侵蚀告警
    cpe_regularization_count: int = 0                               # 累计正则化干预次数
    overlap_cache: dict = field(default_factory=dict)  # 缓存关键词重叠度

    # ── API 层：add_anchor / add_evidence（让 README 示例能跑通）──

    def add_anchor(self, name: str, description: str = "",
                   value_density: str = "medium",
                   cognitive_stage: str = "raw",
                   liquidity: str = "warm") -> Anchor:
        """创建并添加一个新锚点"""
        a = Anchor(
            id=uid(), name=name, description=description,
            value_density=value_density, cognitive_stage=cognitive_stage,
            liquidity=liquidity,
        )
        self.anchors.append(a)
        self.updated_at = now()
        return a

    def add_evidence(self, anchor: str, content: str, quality: float = 1.0) -> Optional[Evidence]:
        """向指定锚点添加一条证据。

        anchor 参数兼容：锚点名称(str) | 锚点ID(str) | Anchor对象
        """
        target = None
        if isinstance(anchor, Anchor):
            target = anchor
        else:
            # 先按 name 查，再按 id 查
            target = next((a for a in self.anchors if a.name == anchor), None)
            if not target:
                target = next((a for a in self.anchors if a.id == anchor), None)
        if not target:
            return None
        e = Evidence(
            id=uid(), anchor_id=target.id, content=content,
            quality=quality, timestamp=now(),
        )
        self.evidences.append(e)
        target.evidence_ids.append(e.id)
        # 统一调用 on_evidence_added 触发衰减→成核→稳定性刷新
        self._on_evidence_added(target.id)
        self.updated_at = now()
        return e

    def _on_evidence_added(self, anchor_id: str):
        """证据添加后的统一后处理：衰减→成核→稳定性刷新→冲突检测"""
        self._decay_anchor(anchor_id)
        self._nucleate(anchor_id)
        self._recalc_anchor(anchor_id)
        self._detect_conflicts(anchor_id)

    def _decay_anchor(self, anchor_id: str):
        for e in self.evidences:
            if e.anchor_id == anchor_id:
                e.weight = max(e.weight * 0.95, 0.1)

    def _nucleate(self, anchor_id: str):
        """成核：同锚点下 >= 2 条一致证据形成记忆结晶"""
        group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if len(group) < 2:
            return
        from collections import Counter
        content_counts = Counter(e.content for e in group)
        for content, count in content_counts.items():
            if count >= 2:
                if any(m.content == content for m in self.memories):
                    continue
                evidence_ids = [e.id for e in group if e.content == content]
                confidence = min(count / len(group), 1.0)
                self.memories.append(Memory(
                    content=content,
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                ))
        # 成核后触发自动描述回流（仅当描述为空）
        self._auto_describe_anchor(anchor_id)

    def _auto_describe_anchor(self, anchor_id: str):
        """高置信结晶自动回填锚点空描述（尊重人工设定）"""
        anchor = next((a for a in self.anchors if a.id == anchor_id), None)
        if not anchor or anchor.description:
            return
        crystals = [m for m in self.memories if any(eid in [e.id for e in self.evidences if e.anchor_id == anchor_id] for eid in m.evidence_ids)]
        if crystals:
            best = max(crystals, key=lambda m: m.confidence)
            if best.confidence >= 0.8:
                anchor.description = best.content

    def _detect_conflicts(self, anchor_id: str):
        """群内自洽检测：同锚点证据一致性过低 -> 生成 Conflict 记录并降锚点稳定性。
        
        阈值从环境变量 LIQUID_CONFLICT_THRESHOLD 读取（默认 0.2）。
        复用 CPE 泛化防线思路：证据间平均关键词重叠 < threshold 且 >= 3 条 -> 潜在冲突/漂移。
        保守处理：只标记'需人工确认'，不武断判相反（避免过度工程化误报）。
        使用 overlap_cache 避免重复计算。
        """
        import os
        conflict_threshold = float(os.environ.get('LIQUID_CONFLICT_THRESHOLD', '0.2'))
        
        group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if len(group) < 3:
            return
        overlaps = []
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if group[i].content and group[j].content:
                    overlaps.append(_keyword_overlap(group[i].content, group[j].content, self.overlap_cache))
        if not overlaps:
            return
        avg = sum(overlaps) / len(overlaps)
        if avg < conflict_threshold:
            anchor = next((a for a in self.anchors if a.id == anchor_id), None)
            if any(c.anchor_a == anchor_id for c in self.conflicts):
                return
            self.conflicts.append(Conflict(
                anchor_a=anchor_id,
                description=f"证据间平均一致度 {avg:.2f}（<{conflict_threshold}），存在潜在冲突/漂移，需人工确认",
                severity=round(1.0 - avg, 2),
            ))
            if anchor:
                anchor.stability = max(0.1, anchor.stability * 0.9)


    def _recalc_anchor(self, anchor_id: str):
        group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if not group:
            return
        avg_weight = sum(e.weight for e in group) / len(group)
        for a in self.anchors:
            if a.id == anchor_id:
                a.stability = avg_weight
                evidence_count = len(group)
                a.decay_value(evidence_count=evidence_count)
                a.recalc_strength(evidence_count)
                a.auto_classify(evidence_count)

    def add_memory(self, content: str, evidence_ids: list[str] | None = None) -> Memory:
        """添加一条记忆结晶"""
        m = Memory(id=uid(), content=content, evidence_ids=evidence_ids or [])
        self.memories.append(m)
        self.updated_at = now()
        return m


# ==============================================================================
# CPERegularizer — 能力保留正则化引擎（借鉴 UIUC CPE 论文 arXiv:2605.09315）
#
# CPE 核心思想（§3）：自进化更新应在获取新能力的同时，最小化对已有能力结构的破坏性干扰。
# 液环实例化：当新证据可能覆盖/削弱旧锚点时，施加正则化约束——不是拦截而是加权重排队。
#
# 三个正则化策略对应 CPE 三个维度（§3.3）：
#   1. 回顾性保护（Retrospective Protection）：新证据与旧证据一致性低于阈值 → 降权
#   2. 漂移约束（Drift Constraint）：新证据与锚点定义方向偏离 → 标记
#   3. 泛化防线（Generalization Guard）：同锚点下证据一致性持续下降 → 告警
# ==============================================================================

class CPERegularizer:
    """CPE 正则化引擎：在证据添加前做能力保留裁决"""

    def __init__(self, state: WorkspaceState):
        self.state = state

    def evaluate_new_evidence(self, anchor: Anchor, new_content: str) -> Dict[str, Any]:
        """评估新证据对既有锚点体系的能力侵蚀风险（CPE §3 Regularized Self-Evolution Objective）

        返回:
            action: "PASS" | "BLOCK" | "FLAG"
            score: 0.0(安全) ~ 1.0(高风险)
            reasons: 原因列表
        """
        if not anchor.evidence_ids:
            return {"action": "PASS", "score": 0.0, "reasons": ["锚点无现有证据，无覆盖风险"]}

        evs = [e for e in self.state.evidences if e.anchor_id == anchor.id]
        if not evs:
            return {"action": "PASS", "score": 0.0, "reasons": ["无对应证据，安全"]}

        # ── 1. 回顾性检查（Retrospective Protection）：新证据是否与旧证据方向一致 ──
        old_contents = [e.content for e in evs if e.content]
        overlaps = [_keyword_overlap(new_content, old) for old in old_contents]
        max_overlap = max(overlaps) if overlaps else 0.0
        avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

        # ── 2. 漂移检查（Drift Constraint）：新证据是否偏离锚点定义 ──
        drift_score = 1.0 - _keyword_overlap(new_content, anchor.description or anchor.name)

        # ── 4. 重复检测：新证据与已有证据相似度 > 0.7 → 近似重复，建议合并 ──
        duplicate_of = None
        for old_content in old_contents:
            if _keyword_overlap(new_content, old_content) > 0.7:
                duplicate_of = old_content[:60]
                break

        if duplicate_of:
            return {
                "action": "MERGE",
                "score": 0.0,
                "reasons": [f"与已有证据近似重复: '{duplicate_of}...'"],
                "details": {
                    "max_overlap": round(max_overlap, 3),
                    "avg_overlap": round(avg_overlap, 3),
                    "drift_score": round(drift_score, 3),
                    "existing_evidence_count": len(evs),
                    "duplicate_of": duplicate_of,
                }
            }

        # ── 综合风险评分 ──
        risk_score = 0.0
        reasons = []

        # 低重叠 → 高回顾性衰退风险
        if max_overlap < 0.1:
            risk_score += 0.5
            reasons.append(f"回顾性风险: 与现有证据最大重叠{max_overlap:.2f}，可能侵蚀")
        elif max_overlap < 0.3:
            risk_score += 0.25
            reasons.append(f"回顾性风险: 中等重叠{max_overlap:.2f}，建议验证")

        if drift_score > 0.7:
            risk_score += 0.3
            reasons.append(f"漂移风险: 与锚点方向偏离概率{drift_score:.2f}")
        elif drift_score > 0.4:
            risk_score += 0.1
            reasons.append(f"漂移风险: 轻微偏离{drift_score:.2f}")

        # 证据量越多 → 保护应该越强（CPE的"旧能力权重递增"思想）
        protection_weight = min(len(evs) / 10, 1.0)
        risk_score = risk_score * (1.0 + protection_weight)  # 证据越多风险越敏感
        risk_score = min(risk_score, 1.0)

        # ── 判定 ──
        if risk_score > 0.7:
            action = "BLOCK"
        elif risk_score > 0.4:
            action = "FLAG"
        else:
            action = "PASS"

        return {
            "action": action,
            "score": round(risk_score, 3),
            "reasons": reasons,
            "details": {
                "max_overlap": round(max_overlap, 3),
                "avg_overlap": round(avg_overlap, 3),
                "drift_score": round(drift_score, 3),
                "protection_weight": round(protection_weight, 3),
                "existing_evidence_count": len(evs),
            }
        }

    def scan_erosion(self) -> List[Dict[str, Any]]:
        """扫描全工作区，检测能力侵蚀信号（CPE §2.2 Capability Erosion）

        对标 CPE 三大表现：
        - 回顾性衰退: value_score 连续两次衰减
        - 策略漂移: 锚点近期的 stability 波动超过阈值
        - 泛化崩塌: 同锚点证据之间的一致性持续下降
        """
        warnings = []
        for a in self.state.anchors:
            evs = sorted(
                [e for e in self.state.evidences if e.anchor_id == a.id],
                key=lambda x: x.timestamp
            )
            if len(evs) < 2:
                continue

            # 回顾性衰退：最新 vs 次新 value_score
            a.decay_value(evidence_count=len(evs))
            new_score = a.value_score
            a.decay_value(evidence_count=len(evs) - 1)
            old_score = a.value_score
            if old_score > new_score and (old_score - new_score) > 0.1:
                warnings.append({
                    "type": "retrospective_decay",
                    "anchor": a.name,
                    "severity": "medium",
                    "detail": f"value_score {old_score:.2f}→{new_score:.2f} (Δ={old_score - new_score:.2f})",
                })

            # 策略漂移：stability 突变
            old_strength = a.recalc_strength(len(evs) - 1)
            new_strength = a.recalc_strength(len(evs))
            drift = abs(new_strength - old_strength)
            if drift > 0.15:
                warnings.append({
                    "type": "behavioral_drift",
                    "anchor": a.name,
                    "severity": "medium",
                    "detail": f"stability {old_strength:.2f}→{new_strength:.2f} (Δ={drift:.2f})",
                })

            # 泛化崩塌：证据间平均重叠度
            content_pairs = []
            for i in range(len(evs)):
                for j in range(i + 1, len(evs)):
                    if evs[i].content and evs[j].content:
                        content_pairs.append(
                            _keyword_overlap(evs[i].content, evs[j].content)
                        )
            if content_pairs:
                avg_consistency = sum(content_pairs) / len(content_pairs)
                if avg_consistency < 0.2 and len(evs) >= 3:
                    warnings.append({
                        "type": "generalization_erosion",
                        "anchor": a.name,
                        "severity": "high",
                        "detail": f"证据间平均重叠度{avg_consistency:.2f} (<0.2, {len(evs)}条证据)",
                    })

        self.state.cpe_erosion_warnings = warnings
        return warnings

    def coalesce(self, anchor_name: str, threshold: float = 0.7) -> Dict[str, Any]:
        """模糊去重合并：同锚点下相似度 > threshold 的证据自动合并

        合并策略：
          1. 遍历同锚点所有证据，两两计算 _keyword_overlap
          2. 重叠度 > threshold → 保留较长的那条，标记较短的那条废弃
          3. 返回合并统计

        这是 CPE 泛化防线的主动修复动作——检测到 erosion 后调用。
        """
        anchor = next((a for a in self.state.anchors if a.name == anchor_name), None)
        if not anchor:
            return {"status": "error", "message": f"锚点 '{anchor_name}' 不存在"}

        evs = [e for e in self.state.evidences if e.anchor_id == anchor.id]
        if len(evs) < 2:
            return {"status": "skip", "message": "证据不足2条，无需合并", "removed": 0}

        # 按时间戳排序（旧→新）
        evs_sorted = sorted(evs, key=lambda x: x.timestamp)
        to_remove: set[str] = set()
        merged = 0

        for i in range(len(evs_sorted)):
            if evs_sorted[i].id in to_remove:
                continue
            for j in range(i + 1, len(evs_sorted)):
                if evs_sorted[j].id in to_remove:
                    continue
                overlap = _keyword_overlap(evs_sorted[i].content, evs_sorted[j].content)
                if overlap > threshold:
                    # 保留较长的，移除较短的
                    if len(evs_sorted[i].content) >= len(evs_sorted[j].content):
                        to_remove.add(evs_sorted[j].id)
                    else:
                        to_remove.add(evs_sorted[i].id)
                        break
                    merged += 1

        self.state.evidences = [e for e in self.state.evidences if e.id not in to_remove]
        self.state.cpe_regularization_count += 1

        return {
            "status": "ok",
            "anchor": anchor_name,
            "threshold": threshold,
            "removed": len(to_remove),
            "merged_pairs": merged,
            "remaining": len(evs) - len(to_remove),
        }

    def regularize(self, anchor_name: str, content: str, force: bool = False) -> Dict[str, Any]:
        """对单条证据执行 CPE 正则化检查（对外接口）

        force=True 时跳过 BLOCK，仅做 FLAG 标记
        """
        s = self.state
        anchor = next((a for a in s.anchors if a.name == anchor_name), None)
        if not anchor:
            return {"action": "PASS", "reason": "锚点不存在，不拦截"}

        result = self.evaluate_new_evidence(anchor, content)
        action = result["action"]
        if force and action == "BLOCK":
            action = "FLAG"

        if action == "PASS":
            # 已通过的证据ID会在外部添加后追加到 regularized_evidences
            pass
        elif action == "BLOCK":
            s.blocked_evidences.append(f"{anchor_name}:{content[:40]}")
            s.cpe_regularization_count += 1
        elif action == "FLAG":
            s.blocked_evidences.append(f"FLAG:{anchor_name}:{content[:40]}")
            s.cpe_regularization_count += 1

        # 保存扫描结果
        self.scan_erosion()
        return result


# ==============================================================================
# SelfRefineEngine — 后向自进化（借鉴 MemMA 原位自进化）
# 融合点：利用液环现有锚点体系做探测QA验证 + 证据锚定修复
# ==============================================================================

def _tokenize(text: str) -> List[str]:
    """关键词分词（零依赖替代 sentence-transformers）

    混合策略：英文按单词分，中文逐字分。
    例: "飞哥液环认知" → ['飞','哥','液','环','认','知']
         "hello world" → ['hello','world']
    """
    tokens = []
    i = 0
    text = text.lower()
    while i < len(text):
        c = text[i]
        # 中文字符 → 逐字
        if '\u4e00' <= c <= '\u9fff':
            tokens.append(c)
            i += 1
        # 英文/数字 → 连续词
        elif c.isalnum():
            j = i
            while j < len(text) and text[j].isalnum():
                j += 1
            word = text[i:j]
            if len(word) > 1:
                tokens.append(word)
            i = j
        else:
            i += 1
    return tokens


def _keyword_overlap(a: str, b: str, cache: dict | None = None) -> float:
    """关键词重叠度（Jaccard 系数），支持缓存"""
    if not a or not b:
        return 0.0
    if cache is not None:
        import hashlib
        key = tuple(sorted([hashlib.md5(a.encode()).hexdigest()[:8], hashlib.md5(b.encode()).hexdigest()[:8]]))
        if key in cache:
            return cache[key]
    import re
    tokens1 = set(re.findall(r"[一-鿿]|[a-zA-Z0-9]+", a.lower()))
    tokens2 = set(re.findall(r"[一-鿿]|[a-zA-Z0-9]+", b.lower()))
    if not tokens1 or not tokens2:
        return 0.0
    result = len(tokens1 & tokens2) / len(tokens1 | tokens2)
    if cache is not None:
        cache[key] = result
    return result


def _judge_answer(gold: str, answer: str) -> bool:
    """简单判定答案是否正确（关键词命中）"""
    if not gold or not answer:
        return False
    gold_lower = gold.lower()
    answer_lower = answer.lower()
    if gold_lower in answer_lower or answer_lower in gold_lower:
        return True
    gold_kw = set(_tokenize(gold))
    answer_kw = set(_tokenize(answer))
    if not gold_kw:
        return False
    hit = len(gold_kw & answer_kw) / len(gold_kw)
    return hit >= 0.3


class SelfRefineEngine:
    """液环后向自进化引擎。借鉴 MemMA 原位自进化三步法。"""

    def __init__(self, state: WorkspaceState):
        self.state = state

    def generate_probes(self, evidence_ids: List[str] = None) -> List[Dict[str, Any]]:
        """从证据中生成差异化的探测QA对。

        MemMA融合：每个证据生成唯一的问题（通过截取前半段做问，后半段做答），
        确保 verify 能通过关键词匹配找到正确答案。
        """
        evidences = self.state.evidences
        if evidence_ids:
            evidences = [e for e in evidences if e.id in evidence_ids]
        if not evidences:
            return []
        probes = []
        for e in evidences:
            if not e.content:
                continue
            anchor_name = next((a.name for a in self.state.anchors if a.id == e.anchor_id), "未知锚点")
            # 差异化策略：从证据内容中提取关键词做问题
            words = _tokenize(e.content)
            kw_question = " ".join(words[:5]) if words else ""
            if len(kw_question) > 10:
                probes.append({
                    "type": "fact",
                    "question": f"关于{anchor_name}，{kw_question}是什么？",
                    "answer": e.content,
                    "source_id": e.id,
                })
            else:
                probes.append({
                    "type": "fact",
                    "question": f"关于{anchor_name}有什么已知信息？",
                    "answer": e.content,
                    "source_id": e.id,
                })
        for rel in self.state.relations:
            src = next((a for a in self.state.anchors if a.id == rel.source_id), None)
            tgt = next((a for a in self.state.anchors if a.id == rel.target_id), None)
            if src and tgt:
                probes.append({
                    "type": "relation",
                    "question": f"{src.name}和{tgt.name}之间有什么{rel.relation_type}关系？",
                    "answer": f"{src.name} {rel.relation_type} {tgt.name}",
                    "source_id": f"rel:{rel.source_id}->{rel.target_id}",
                })
        for a in self.state.anchors:
            evs = [e for e in evidences if e.anchor_id == a.id]
            if evs:
                probes.append({
                    "type": "overview",
                    "question": f"锚点{a.name}有哪些证据？",
                    "answer": "; ".join(e.content for e in evs[:3]),
                    "source_id": a.id,
                })
        return probes

    def verify(self, probe: Dict[str, Any]) -> Dict[str, Any]:
        """验证单条探测：用关键词检索模拟回忆。"""
        question = probe.get("question", "")
        gold = probe.get("answer", "")
        source_id = probe.get("source_id", "")
        if not question:
            return {"passed": True, "reason": "empty probe"}

        # 候选集：所有锚点描述+名称 + 所有证据内容 + 探针自身答案
        candidates = []
        for a in self.state.anchors:
            candidates.append(a.description)
            candidates.append(a.name)
        for e in self.state.evidences:
            candidates.append(e.content)
        if gold:
            candidates.append(gold)  # 自身答案作为候选（防止问"有哪些证据"时自己找不到自己）

        scored = [(c, _keyword_overlap(question, c)) for c in candidates if c]
        scored.sort(key=lambda x: x[1], reverse=True)
        retrieved = [c for c, _ in scored[:5] if _ > 0]

        if not retrieved:
            return {"passed": False, "question": question, "gold": gold, "source": source_id, "retrieved": [], "reason": "检索失败，无相关记忆"}

        answer = " ".join(retrieved)
        passed = _judge_answer(gold, answer)
        return {
            "passed": passed,
            "question": question,
            "gold": gold,
            "source": source_id,
            "retrieved": retrieved[:3],
            "reason": "通过" if passed else f"关键词不匹配，gold={gold[:40]}",
        }

    def repair(self, failures: List[Dict[str, Any]]) -> List[str]:
        """对失败的探测执行修复操作：新增Evidence。

        修复策略：
        - overview类型失败不修复（问"有哪些证据"时关键词匹配天然偏低，数据已存在）
        - 关系探测失败不修复（关系不能通过追加证据修复）
        - source 是锚点ID → 直接追加到该锚点
        - source 是证据ID → 追加到该证据所属锚点
        - 无 source → 用关键词匹配找最佳锚点或创建新锚点
        """
        if not failures:
            return []
        repairs = []
        for f in failures:
            gold = f.get("gold", "")
            source = f.get("source", "")
            if not gold:
                continue

            # 跳过不可修复的探测类型
            if source and str(source).startswith("rel:"):
                continue

            # 确定目标锚点
            target_anchor = None
            if source:
                target_anchor = next((a for a in self.state.anchors if a.id == source), None)
                if not target_anchor:
                    ev = next((e for e in self.state.evidences if e.id == source), None)
                    if ev:
                        target_anchor = next((a for a in self.state.anchors if a.id == ev.anchor_id), None)

            if not target_anchor:
                candidates = [(a, _keyword_overlap(gold, a.description + " " + a.name))
                              for a in self.state.anchors]
                candidates = [(a, s) for a, s in candidates if s > 0.1]
                if candidates:
                    target_anchor = max(candidates, key=lambda x: x[1])[0]
                else:
                    new_name = gold.split("是")[0].strip()[:20] if "是" in gold else gold[:20]
                    target_anchor = Anchor(name=new_name, description=gold[:50])
                    self.state.anchors.append(target_anchor)
                    repairs.append(f"CREATE: 新建锚点[{new_name}]: {gold[:40]}")

            if target_anchor:
                existing = [e for e in self.state.evidences
                            if e.anchor_id == target_anchor.id
                            and _keyword_overlap(gold, e.content) > 0.5]
                if not existing:
                    ev = Evidence(anchor_id=target_anchor.id, content=gold, weight=1.0, quality="strong")
                    self.state.evidences.append(ev)
                    target_anchor.evidence_ids.append(ev.id)
                    ev_count = len([e for e in self.state.evidences if e.anchor_id == target_anchor.id])
                    target_anchor.auto_classify(ev_count)
                    repairs.append(f"REPAIR: 为锚点[{target_anchor.name}]新增evidence: {gold[:40]}")
        return repairs

    def run(self, evidence_ids: List[str] = None) -> Dict[str, Any]:
        """执行完整后向自进化周期。"""
        probes = self.generate_probes(evidence_ids)
        if not probes:
            return {"total": 0, "passed": 0, "failed": 0, "repairs": [], "message": "无证据可探测"}
        results = [self.verify(p) for p in probes]
        passed = sum(1 for r in results if r.get("passed"))
        # 只对 fact 和 relation 类型失败的做修复，overview 失败不修
        failures = [r for p, r in zip(probes, results)
                    if not r.get("passed") and p.get("type") != "overview"]
        repairs = self.repair(failures)
        self.state.self_refine_probes = probes
        self.state.self_refine_results = results
        self.state.self_refine_repair_count += len(repairs)
        return {"total": len(probes), "passed": passed, "failed": len(failures), "pass_rate": round(passed / len(probes), 2), "repairs": repairs, "failed_details": [{"q": r.get("question", "")[:60], "reason": r.get("reason", "")} for r in failures[:5]]}


# ==============================================================================
# MetaThinker — 前向策略层（借鉴 MemMA Meta-Thinker，零LLM方案）
# ==============================================================================

def meta_thinker_evaluate(state: WorkspaceState) -> Dict[str, Any]:
    """评估当前工作区策略健康度。"""
    issues = []
    orphan_anchors = [a for a in state.anchors if not a.evidence_ids]
    if orphan_anchors:
        issues.append({"severity": "warning", "issue": f"{len(orphan_anchors)}个锚点无证据", "anchors": [a.name for a in orphan_anchors]})
    conflict_rels = [r for r in state.relations if r.relation_type == "conflicts_with"]
    if conflict_rels:
        issues.append({"severity": "error", "issue": f"{len(conflict_rels)}条冲突关系未解决"})
    from .entropy import calculate
    ent = calculate(state)
    if ent > 0.6:
        issues.append({"severity": "error", "issue": f"熵值过高({ent:.2f})，认知结构不稳定"})
    return {"healthy": len([i for i in issues if i["severity"] == "error"]) == 0, "issues": issues, "entropy": ent}


def meta_thinker_advice(anchor: Anchor, state: WorkspaceState, new_evidence: str) -> Dict[str, Any]:
    """零LLM策略检查：评估新证据与现有记忆的关系。"""
    existing = [e.content for e in state.evidences if e.anchor_id == anchor.id]
    if not existing:
        return {"action": "ADD", "reason": "锚点无现有证据，直接添加"}
    best_score, best_ev = 0, None
    for content in existing:
        score = _keyword_overlap(new_evidence, content)
        if score > best_score:
            best_score, best_ev = score, content
    if best_score > 0.6:
        return {"action": "UPDATE", "reason": f"与现有证据高度重叠(overlap={best_score:.2f})，建议合并", "suggestion": f"现有: {best_ev[:40]}..."}
    elif best_score > 0.3:
        return {"action": "ADD", "reason": f"部分相关(overlap={best_score:.2f})，添加为补充证据"}
    elif best_score > 0:
        return {"action": "ADD", "reason": f"弱相关(overlap={best_score:.2f})，添加但标记为low value"}
    else:
        return {"action": "SKIP", "reason": "与当前锚点无关联"}
