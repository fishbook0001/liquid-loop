from __future__ import annotations

from .textutil import (
    now, uid, _derive_lifecycle_thresholds, _get_version,
    _tokenize, _keyword_overlap, _judge_answer,
    _dissolve_votes_path, _load_dissolve_votes, _save_dissolve_votes,
)
from .audit import AuditChain
from .cpe import CPERegularizer
from .self_refine import SelfRefineEngine, meta_thinker_evaluate, meta_thinker_advice
from .guard import validate_content


from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import uuid
from pathlib import Path
import hashlib
import json
import os
import re
import logging
from collections import Counter, defaultdict

from .cognitive_budget import CognitiveBudgetStabilizer






# ========== 链式哈希审计（借鉴 KFG MemoryGovernance）==========



# --- 三维锚点分类（密度 / 认知阶段 / 流动性）+ 一维证据质量 ---
DensityLevel = str  # "high" | "medium" | "low"
CognitiveStage = str  # "raw" | "wip" | "crystallized" | "tooling"
LiquidityLevel = str  # "hot" | "warm" | "cold" | "frozen"

# ── v1.7 证据老化回收（督办项警示①落地：append-only 长期会崩）──
# 警示①（上交+清华 Agent-Native Memory 横评）："many append-only stores collapse on long
# horizons as evidence ages" → 液环须主动 lifecycle，而非仅 append。
# 机制：长期未被召回 + 权重冷却到地板 + 非结晶来源 + 非冲突证据 → 冷归档(archived=True)。
# 守铁律：零丢失(archived≠删) / 可审计(archived_at) / 保留时序(timestamp完整) / 禁向量。
#
# ── v1.7.1 理论基点（增益自适应 efficient-coding 映射，2026-08-10 深挖）──
# 论文 Prat-Carrabin et al. (Nature Communications 2026, s41467-026-73032-0)：
#   生物大脑自适应不靠改连接(重连/重训)，靠调增益(gain modulation)，目标函数
#   cost = α·解码准确性 + β·放电成本 —— 单一 objective 统一"prior attraction/adapter
#   repulsion"两个矛盾现象。这与液环"调权重 > 重写证据"同构。
# 液环映射：
#   decoding_accuracy(回忆准确性)  ↔ 保留高权重/近期证据（防误归档丢有用信息）
#   memory_entropy_cost(记忆熵增成本) ↔ 激活集膨胀/检索噪声/存储负担（由老化回收抑制）
#   gain modulation(增益调节)        ↔ evidence.weight 衰减（lifecycle 调权重冷归档，不删结构）
# 双判据推导（见 _derive_lifecycle_thresholds）：
#   floor_weight = β   （熵增成本权重直接作归档地板：越怕熵增越激进清）
#   ttl_eps      = HORIZON（回忆效用半衰期：accuracy 侧容忍上限，与 rar.py 180d decay 对齐）




# 导出常量（沿用现状默认值，向后兼容测试与实战零误冻）
LIFECYCLE_FLOOR_WEIGHT, LIFECYCLE_TTL_EPS = _derive_lifecycle_thresholds()


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
    seal_adjust: float = 0.0  # SEAL 自评层增量；_recalc 合成进 stability，不被覆盖（解 v0.6.3 假落地）
    conflict_penalty: float = 1.0  # 冲突惩罚累积乘子（Layer-1 修复：持久化，recalc/step/load 后保留）
    # ── v1.3 因果演化循环成核：因果边（符号化，守禁向量）──
    causal: dict = field(default_factory=dict)  # {causes,caused_by,enables,contradicts} -> list[node_id]

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
    archived: bool = False  # 预算超额冷归档标记（PEEK 落地，零丢失）
    agent_id: str = ""  # 多智能体共用：写入者标识（共用机制，缺省=legacy/单实例）
    relation: str = "support"  # 反证轨：evidence 与 memory 的关系 "support"(一致) | "contradiction"(冲突) | ""(legacy 视为 support)
    target_memory_id: str = ""  # relation=contradiction 时，可选显式指向被反驳的 memory（缺省=同锚点最近结晶）
    added_iter: int = 0  # 证据写入时的有效状态更新序号（τ=Effective Iteration）；强化门控据此判"自上次 step 以来是否有新 support 抵达"
    # ── v1.3 因果演化循环成核：因果边（符号化，守禁向量）──
    causal: dict = field(default_factory=dict)  # {causes,caused_by,enables,contradicts} -> list[node_id]
    # ── v1.4 原理优先成核（MSM 反哺）：证据所例证的「原理/why」──
    principle: str = ""  # 该证据例证的原理陈述（非空→参与原理优先成核；守禁向量=结构化文本非 embedding）
    # ── v1.5 注意力增益（#115 神经科学实证：多看几眼=价值升）──
    recall_hits: int = 0  # 被召回命中次数（gaze 增益信号源；黏滞升稳由 register_recall 驱动）
    last_recall_at: str = ""  # 最近被召回时间戳（老化回收判据源；空=从未召回）
    archived_at: str = ""  # 被 lifecycle 冷归档的时间戳（审计展示用，零丢失）
    # ── v1.8 软取代（supersede）原语：显式语义取代（区别于 lifecycle 自动老化归档）──
    # 与 archived 正交：archived=熵增清理(低权+久未召回)；superseded_by=被另一条更优/更新
    # 证据显式取代(语义冗余)。二者皆零丢失可解冻；active recall 退出活跃，list 全量审计可见。
    superseded_by: str = ""  # 本证据被哪条 winner 证据取代（空=未被取代）
    supersedes: list = field(default_factory=list)  # 本证据取代过的 loser 证据 id 列表（反向指针/血缘）
    superseded_at: str = ""  # 被取代/取代发生的时间戳（审计展示用）


@dataclass
class Memory:
    id: str = field(default_factory=uid)
    content: str = ""
    formed_at: str = field(default_factory=now)
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    scope: str = "private"  # 结晶来源: private(同 agent≥2一致) / consensus(跨 distinct owner≥2一致)
    contributors: list[str] = field(default_factory=list)  # 参与成核的 agent_id 列表
    # ── v0.8 反证轨 + 时间动力学 ──
    stability: float = 1.0  # 反证轨稳定性：一致增稳、冲突降稳（0~1）
    support_count: int = 0  # 支持该 memory 的证据数
    contradiction_count: int = 0  # 反驳该 memory 的证据数
    last_reinforced: str = ""  # 最近一次被 support 证据强化的时间戳（审计展示用）
    last_reinforced_iter: int = 0  # 最近一次被 support 强化的迭代序号（τ=Effective Iteration，非墙钟；强化门控以此为准）
    # ── v1.3 因果演化循环成核：因果边（符号化，守禁向量）──
    causal: dict = field(default_factory=dict)  # {caused_by,contradicts,...} -> list[node_id]；caused_by=成核血缘（证据id）
    # ── v1.4 原理优先成核（MSM 反哺）──
    principle: str = ""          # 该记忆例证的原理/why（非空=携带原理）
    principle_grounded: bool = False  # True=由共享 principle 的证据结晶（why 先于 how 的首类核心）
    attn_bonus: float = 0.0  # #115 注意力增益：被反复召回证据的 support 加成（仅增 s，不削 c，守反证轨）
    replay_pressure: float = 0.0  # #116 重放压力感知：over-replay 的 contradiction 累计超额召回次数（≥0）
    # ── 蒸馏 #202 记忆外置·分级存储 + 生命周期(TTL) ──
    tier: str = "fact"  # 记忆分级: fact(事实)/preference(偏好)/temp(临时)。temp 可被 TTL 回收
    expires_at: str = ""  # ISO 时间戳；非空且已过期 → evict_expired 回收（仅 temp）。空=永不过期


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
    _iteration: int = 0  # 有效状态更新次数（τ = Effective Iteration）；时间动力学以之为时间变量而非墙钟
    canon_fn: object = None  # 可替换 Projection Layer（Layer-1 修复）：注入后冲突检测复用此投影，None=旧关键词重叠回退

    # ── 蒸馏 #202 记忆外置·生命周期(TTL) 回收 ──
    def evict_expired(self, ref_dt=None) -> int:
        """按 TTL 回收过期临时记忆（记忆外置四项挑战之'可靠删除'）。

        仅清理 tier='temp' 且 expires_at 已过期的 Memory；
        锚点/证据不受影响（证据不可删，符合反证轨不可篡改性）。
        返回回收条数。向后兼容：无 expires_at 的 Memory 永不过期。
        """
        if not self.memories:
            return 0
        from datetime import datetime, timezone
        ref = ref_dt or datetime.now(timezone.utc)
        kept, evicted = [], 0
        for m in self.memories:
            if m.tier == "temp" and m.expires_at:
                try:
                    exp = datetime.fromisoformat(m.expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp <= ref:
                        evicted += 1
                        continue
                except ValueError:
                    pass
            kept.append(m)
        self.memories = kept
        return evicted

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

    def add_evidence(self, anchor, content: str, quality: float = 1.0, agent_id: str = "",
                     dedup: bool = False, relation: str = "support",
                     target_memory_id: str = "", principle: str = "") -> Optional[Evidence]:
        """向指定锚点添加一条证据。

        anchor 参数兼容：锚点名称(str) | 锚点ID(str) | Anchor对象
        agent_id：多智能体共用写入者标识（缺省空=legacy/单实例）
        dedup：幂等去重开关（默认 False，核心语义允许重复写入以触发结晶）。
        relation：反证轨关系 "support"(一致, 默认) | "contradiction"(冲突) —— 见 v0.8 反证轨。
        target_memory_id：relation="contradiction" 时可选显式指向被反驳的 memory。
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
        # 08-18 工程化修复：内容质量 ValidationRule 下沉库层（堵直调绕过通道）。
        # 与 server ll_remember 共用 guard.validate_content，单点维护。
        v_err = validate_content(content)
        if v_err:
            raise ValueError(v_err)
        # 幂等去重（仅当 dedup=True）：同锚点+同内容+同 agent 已存在则复用
        if dedup:
            for ex in self.evidences:
                if ex.anchor_id == target.id and ex.content == content and ex.agent_id == agent_id:
                    return ex
        e = Evidence(
            id=uid(), anchor_id=target.id, content=content,
            quality=quality, timestamp=now(), agent_id=agent_id,
            relation=relation, target_memory_id=target_memory_id,
            added_iter=self._iteration, principle=principle,
        )
        self.evidences.append(e)
        target.evidence_ids.append(e.id)
        # 统一调用 on_evidence_added 触发衰减→成核→稳定性刷新
        self._on_evidence_added(target.id)
        self.updated_at = now()
        return e

    def _on_evidence_added(self, anchor_id: str):
        """证据添加后的统一后处理：衰减→成核→反证轨稳定性→冲突检测→预算稳态"""
        self._decay_anchor(anchor_id)
        self._nucleate(anchor_id)
        self._update_memory_stability(anchor_id)
        self._recalc_anchor(anchor_id)
        self._detect_conflicts(anchor_id)
        self._stabilize_budget()
        self._lifecycle_sweep()  # v1.7 老化回收：写入时顺带清理过期证据

    def _stabilize_budget(self):
        """认知预算稳态（PEEK 落地）：超预算时冷归档最低价值证据。默认无预算不动作。"""
        result = CognitiveBudgetStabilizer(self).stabilize()
        if result.get("evicted", 0) > 0:
            self.updated_at = now()

    def _decay_anchor(self, anchor_id: str):
        for e in self.evidences:
            if e.anchor_id == anchor_id:
                e.weight = max(e.weight * 0.95, 0.1)

    def _nucleate(self, anchor_id: str):
        """双轨成核（多智能体共用机制）：

        - private 轨：同一 agent_id 下 >= 2 条一致证据 -> scope=private 结晶
        - consensus 轨：同一 content 被 >= 2 个 distinct agent_id 证据支持 -> scope=consensus 结晶

        复合键 (anchor_id, content, scope) 防跨锚点/跨轨污染。
        兼容 legacy：agent_id 为空视为单实例私有（走 private 轨，contributors=["legacy"]）。
        """
        group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if len(group) < 2:
            return
        # 已结晶键集合（复合键判定，杜绝跨锚点/跨轨污染）
        # 优化：用本锚点证据 id 集合直接判定 memory 是否引用本锚点，
        # 避免对每条 evidence_id 做 next() 线性扫描（原 O(M·E_m·E) → O(M·E_m)）
        group_ev_ids = {e.id for e in group}
        crystallized_keys: set = {
            (m.content, m.scope)
            for m in self.memories
            if group_ev_ids.intersection(m.evidence_ids)
        }
        # ── private 轨：按 agent_id 分组，组内同 content >= 2 ──
        by_agent: dict = defaultdict(list)
        for e in group:
            by_agent[e.agent_id if e.agent_id else "legacy"].append(e)
        for owner, evs in by_agent.items():
            content_counts = Counter(e.content for e in evs)
            for content, count in content_counts.items():
                if count >= 2 and (content, "private") not in crystallized_keys:
                    evidence_ids = [e.id for e in evs if e.content == content]
                    confidence = min(count / len(evs), 1.0)
                    # v1.3 因果演化循环成核：血缘注册（caused_by=成核证据链；contradicts=反证指向）
                    causal = {"caused_by": list(evidence_ids), "causes": [], "enables": [], "contradicts": []}
                    contra = [e.target_memory_id for e in evs if e.content == content
                              and e.relation == "contradiction" and e.target_memory_id]
                    if contra:
                        causal["contradicts"] = contra
                    self.memories.append(Memory(
                        content=content,
                        evidence_ids=evidence_ids,
                        confidence=confidence,
                        scope="private",
                        contributors=[owner],
                        causal=causal,
                    ))
        # ── consensus 轨：跨 distinct owner 同 content >= 2 ──
        owners_by_content: dict = defaultdict(set)
        for e in group:
            if not e.agent_id:
                continue  # legacy 不参与共识轨（无法跨主体）
            owners_by_content[e.content].add(e.agent_id)
        for content, owners in owners_by_content.items():
            if len(owners) < 2:
                continue
            # 已存在的共识结晶：把新一致方并入 contributors（动态扩展，支撑三方+ CCI 计量）
            existing = next((m for m in self.memories if m.scope == "consensus" and m.content == content), None)
            if existing is not None:
                merged = set(existing.contributors) | owners
                if merged != set(existing.contributors):
                    existing.contributors = sorted(merged)
                continue
            if (content, "consensus") not in crystallized_keys:
                evidence_ids = [e.id for e in group if e.content == content and e.agent_id in owners]
                confidence = min(len(owners) / 2.0, 1.0)
                # v1.3 因果演化循环成核：血缘注册（caused_by=成核证据链；contradicts=反证指向）
                causal = {"caused_by": list(evidence_ids), "causes": [], "enables": [], "contradicts": []}
                contra = [e.target_memory_id for e in group if e.content == content
                          and e.relation == "contradiction" and e.target_memory_id]
                if contra:
                    causal["contradicts"] = contra
                self.memories.append(Memory(
                    content=content,
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                    scope="consensus",
                    contributors=sorted(owners),
                    causal=causal,
                ))
        # ── v1.4 原理优先成核（MSM 反哺：先教 why 再教 how）──
        # 共享 principle 的证据（即便 surface content 不同）结晶为「原理记忆」：
        # 原理成为首类结晶核心，表层事实经 causal.caused_by 挂到原理之下。
        # 对应 MSM 发现——先教原理(why)再教规则(how)使行为在分布漂移下仍稳健
        # （Anthropic MSM：Qwen 失控率 54%→7% / 68%→5%）。principle_grounded 标记
        # 供下游抗衰减加权（P2）。守禁向量：principle 为结构化文本，非 embedding。
        prin_groups: dict = defaultdict(list)
        for e in group:
            if e.principle:
                prin_groups[e.principle].append(e)
        for prin, evs_p in prin_groups.items():
            if len(evs_p) < 2:
                continue
            existing_p = next((m for m in self.memories
                               if m.principle_grounded and m.content == prin), None)
            if existing_p is not None:
                merged = set(existing_p.evidence_ids) | {e.id for e in evs_p}
                if merged != set(existing_p.evidence_ids):
                    existing_p.evidence_ids = sorted(merged)
                continue
            if (prin, "private") not in crystallized_keys:
                evidence_ids = [e.id for e in evs_p]
                confidence = min(len(evs_p) / 2.0, 1.0)
                # 原理记忆稳定性封顶为 1.0（principle_grounded 标记驱动下游抗衰减加权）
                causal = {"caused_by": list(evidence_ids), "causes": [],
                          "enables": [], "contradicts": []}
                self.memories.append(Memory(
                    content=prin,
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                    scope="private",
                    contributors=sorted({e.agent_id for e in evs_p if e.agent_id}),
                    principle=prin,
                    principle_grounded=True,
                    stability=1.0,
                    causal=causal,
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
        """群内自洽检测（Layer-1 修复：复用可替换 Projection Layer，惩罚持久化）。

        旧实现用关键词重叠（Jaccard）近似"语义冲突"——本质是 Lexical Proxy，
        会导致：① 对真异事实（关键词不重叠）误触发；② 对近重复（关键词高重叠）
        反而不触发（反相关）；③ 自建一套不可替换的投影启发式（Hidden Projection Layer）。

        修复：若 WorkspaceState.canon_fn 已注入，则冲突判定改用同一 Projection Layer——
        两条 distinct content 证据，若投影到【同一 CP】但 relation 相反（一 support 一
        contradiction）-> 真冲突；若投影同 CP 同 relation -> 近重复（不冲突）；
        若投影不同 CP -> 即使关键词重叠也不算冲突（语义独立）。

        惩罚持久化：锚点稳定性惩罚写入锚点字段 conflict_penalty（累积乘子），
        _recalc_anchor 在合成 stability 时乘入，因此 load / step 重算后惩罚不丢失
        （旧实现在 stability 字段上直接 *=0.9，被后续 recalc 覆盖，故不持久）。

        canon_fn 为 None 时回退旧 _keyword_overlap 行为，保证生产向后兼容。
        """
        import os
        conflict_threshold = float(os.environ.get('LIQUID_CONFLICT_THRESHOLD', '0.2'))

        group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if len(group) < 2:
            # 08-18 工程化修复：锚点证据已不足 → 清除历史冲突残留（防 stale conflict 累积）
            self.conflicts = [c for c in self.conflicts if c.anchor_a != anchor_id]
            return
        # ── 聚合型锚点豁免（2026-08-17 审计修复）────────────────────────
        # distill/research_asset/principle 等锚点设计上聚合多主题资产，
        # keyword-overlap 平均一致度天然偏低，会系统性误报冲突/漂移。
        _agg_anchor = next((a for a in self.anchors if a.id == anchor_id), None)
        if _agg_anchor and _agg_anchor.name in {
            "distill", "research_asset", "tool_ref", "principle",
            "harness-factor", "pattern", "metacontrol_flag", "pending_action",
        }:
            self.conflicts = [c for c in self.conflicts if c.anchor_a != anchor_id]
            return
        distinct: list = []
        seen: set = set()
        for e in group:
            if e.content and e.content not in seen:
                seen.add(e.content)
                distinct.append(e.content)

        # ── 回退路径：未注入 Projection Layer -> 旧关键词重叠行为（向后兼容）──
        if self.canon_fn is None:
            if len(distinct) < 2:
                # 08-18 工程化修复：去重后样本不足 → 清除历史冲突残留
                self.conflicts = [c for c in self.conflicts if c.anchor_a != anchor_id]
                return
            overlaps = []
            for i in range(len(distinct)):
                for j in range(i + 1, len(distinct)):
                    overlaps.append(_keyword_overlap(distinct[i], distinct[j], self.overlap_cache))
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
                    anchor.conflict_penalty = max(0.1, anchor.conflict_penalty * 0.9)
            return

        # ── Layer-1 路径：复用同一可替换 Projection Layer ──
        # 按 CP 分组 distinct content；同 CP 内若同时有 support 与 contradiction -> 真冲突
        by_cp: dict = {}
        rel_by_content: dict = {}
        for e in group:
            cp = self.canon_fn(e.content)
            by_cp.setdefault(cp, []).append(e.content)
            rel_by_content.setdefault(e.content, set()).add(e.relation or "support")
        triggered = False
        for cp, contents in by_cp.items():
            if len(contents) < 2:
                continue
            distinct_rels = set()
            for c in contents:
                distinct_rels |= (rel_by_content.get(c) or {"support"})
            if "support" in distinct_rels and "contradiction" in distinct_rels:
                triggered = True
                break
        if triggered:
            anchor = next((a for a in self.anchors if a.id == anchor_id), None)
            if not any(c.anchor_a == anchor_id for c in self.conflicts):
                self.conflicts.append(Conflict(
                    anchor_a=anchor_id,
                    description="Projection Layer 检测到同 CP 内存在 support 与 contradiction 异 relation 证据，判定为语义冲突",
                    severity=conflict_threshold,
                ))
            if anchor:
                anchor.conflict_penalty = max(0.1, anchor.conflict_penalty * 0.9)


    def _recalc_anchor(self, anchor_id: str, group: Optional[list] = None):
        if group is None:
            group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if not group:
            return
        avg_weight = sum(e.weight for e in group) / len(group)
        for a in self.anchors:
            if a.id == anchor_id:
                # base = 证据权重均值；stability = base + SEAL 自评层（seal_adjust 不被覆盖）
                base = avg_weight
                a.base_stability = base
                a.stability = round(min(1.0, max(0.1, base * a.conflict_penalty + a.seal_adjust)), 3)
                evidence_count = len(group)
                a.decay_value(evidence_count=evidence_count)
                a.recalc_strength(evidence_count)
                a.auto_classify(evidence_count)

    def add_memory(self, content: str, evidence_ids: list[str] | None = None,
                   validate_refs: bool = True) -> Memory | None:
        """添加一条记忆结晶。

        v2.0.2 修复：增加 evidence_ids 存在性校验（validate_refs=True 默认开启），
        防止创建引用不存在 evidence 的孤儿记忆（历史根因：marvis fact batch 45条
        占位记忆引用了675条不存在的 evidence_id，导致引用完整性仅49.1%）。
        校验失败返回 None，不创建记忆。如需绕过校验（如先建记忆后补证据），传 validate_refs=False。
        """
        eids = evidence_ids or []
        if validate_refs and eids:
            existing = {e.id for e in self.evidences}
            missing = [eid for eid in eids if eid not in existing]
            if missing:
                return None
        m = Memory(id=uid(), content=content, evidence_ids=eids)
        self.memories.append(m)
        self.updated_at = now()
        return m

    # ─────────────────────────────────────────────────────────────
    # v0.8 反证轨 + 时间动力学（液态循环核心）
    # ─────────────────────────────────────────────────────────────

    def _update_memory_stability(self, anchor_id: str, group: Optional[list] = None):
        """反证轨：一致证据(support)增稳，冲突证据(contradiction)降稳。

        稳定性 = support / (support + CONTRADICTION_WEIGHT * contradiction + 1)
        - 同锚点下 content 相同的 support 证据计为支持
        - relation="contradiction" 的证据计为反驳（显式 target_memory_id 或同锚点无指定则作用于该锚点结晶）
        - 有 support 证据则刷新 last_reinforced（时间动力学强化信号）

        group: 可选预过滤的该锚点证据列表（step 预分桶传入，避免每个锚点重复全量扫描
               evidences；缺省时内部构建，保持单锚点调用方行为不变）。
        """
        CONTRADICTION_WEIGHT = 2.0
        ATTENTION_GAIN_WEIGHT = 0.15  # #115：被召回证据的 support 加成权重（温和，守反证轨）
        _ATTN_HIT_CAP = 10            # 召回次数封顶（防无限增益）
        # #116 重放压力感知 + 反证轨局部阻尼：负向记忆(contradiction)重放超频 → 局部降温
        REPLAY_PRESSURE_THRESHOLD = 10  # 与 _ATTN_HIT_CAP 对齐：recall 超此阈值视为病理超频
        REPLAY_DAMP_RATE = 0.05         # 每超 1 次命中，降该 contradiction 对分母 c 的贡献权
        REPLAY_DAMP_FLOOR = 0.3         # 阻尼地板：局部降温不归零（守零丢失，contradiction 仍计数）
        if group is None:
            group = [e for e in self.evidences if e.anchor_id == anchor_id]
        group = [e for e in group if not e.archived]  # 统一过滤：无论传入/构建均排除归档证据
        group_ids = {e.id for e in group}
        anchor_memories = [m for m in self.memories
                           if any(eid in group_ids for eid in m.evidence_ids)]
        for m in anchor_memories:
            supports = [e for e in group
                        if e.relation in ("support", "")
                        and (e.content == m.content
                             or (m.principle_grounded and e.principle == m.content)
                             or e.id in m.evidence_ids)]  # v2.0.2 手动成核回退：引用即支撑（ll_crystallize 的摘要 content 与证据原始 content 不逐字匹配，但 evidence_ids 明确指向支撑证据）
            if m.id:
                contradicts = [e for e in group
                               if e.relation == "contradiction"
                               and (e.target_memory_id == m.id or not e.target_memory_id)]
            else:
                contradicts = [e for e in group
                               if e.relation == "contradiction" and not e.target_memory_id]
            s = len(supports)
            c_raw = len(contradicts)
            # #115 注意力增益：被反复召回的证据黏滞升稳（多看几眼=价值升）
            # 仅对 support 证据累加，且只增 s 不削 c → 不破坏反证轨 stability 公式
            attn_bonus = sum(ATTENTION_GAIN_WEIGHT * min(e.recall_hits, _ATTN_HIT_CAP)
                             for e in supports)
            s_eff = s + attn_bonus
            # #116 反证轨局部阻尼：被反复召回(recall_hits 超阈)的 contradiction 局部降温，
            # 防单一印痕超频重放碎片化整体稳定性；常态(recall_hits≤阈)阻尼=1→等价原公式 s/(s+2c+1)。
            c_eff = 0.0
            pressure = 0.0
            for e in contradicts:
                hits = e.recall_hits
                if hits > REPLAY_PRESSURE_THRESHOLD:
                    damp = max(REPLAY_DAMP_FLOOR,
                               1.0 - REPLAY_DAMP_RATE * (hits - REPLAY_PRESSURE_THRESHOLD))
                    pressure += (hits - REPLAY_PRESSURE_THRESHOLD)
                else:
                    damp = 1.0
                c_eff += CONTRADICTION_WEIGHT * damp
            m.support_count = s
            m.attn_bonus = round(attn_bonus, 3)
            m.contradiction_count = c_raw
            m.replay_pressure = round(pressure, 3)
            m.stability = round(min(1.0, max(0.0,
                                s_eff / (s_eff + c_eff + 1))), 3)
            if supports:
                m.last_reinforced = max(e.timestamp for e in supports)
                m.last_reinforced_iter = max(e.added_iter for e in supports)

    def step(self, dt: int = 1, decay_rate: float = 0.05):
        """显式时间动力学步（v0.8 液态循环核心）：

            M(t+1) = M(t) + reinforcement − decay − contradiction_penalty

        - 每条证据按 (1−decay_rate)^dt 衰减权重（无强化则价值流失，下限 0.05）
        - 每个 memory 按 support/contradiction 计数重算固有稳定性（矛盾惩罚持续作用）；
          若本轮有 support 证据 → 稳定性恢复到固有值（强化）；否则按时间衰减（无强化流失）
        - 重算锚点稳定性与审计更新时间
        """
        prev_iter = self._iteration  # 上次 step 的迭代序号（τ 时间变量，非墙钟）
        # 1. 证据权重时间衰减
        for e in self.evidences:
            if not e.archived:
                e.weight = max(e.weight * ((1 - decay_rate) ** dt), 0.05)
        # 预分桶（全量，O(E)）：供下方两方法复用，消除原实现各自对每个锚点全量扫描
        # evidences 的 O(A·E) 开销。_update_memory_stability 内部会过滤归档证据，
        # _recalc_anchor 保留全量（与原语义一致）。
        ev_by_anchor: dict = defaultdict(list)
        for e in self.evidences:
            ev_by_anchor[e.anchor_id].append(e)
        # 2. 记忆稳定性：固有(计数驱动) + 时间衰减/强化
        for a in self.anchors:
            self._update_memory_stability(a.id, group=ev_by_anchor.get(a.id, []))
        for m in self.memories:
            intrinsic = m.stability  # _update_memory_stability 已写入固有稳定性
            # 强化 = 自上次 step（prev_iter）以来有新 support 证据抵达（last_reinforced_iter >= prev_iter）
            # 以 τ=Effective Iteration 为时间变量，非墙钟，跨算力可比
            reinforced = (m.last_reinforced_iter >= prev_iter)
            if reinforced:
                m.stability = intrinsic  # 被强化：恢复到固有稳定性
            else:
                # 无新强化：时间衰减，且不超过固有上限
                m.stability = round(max(0.0, min(intrinsic,
                                    m.stability * ((1 - decay_rate) ** dt))), 3)
        # 3. 重算锚点稳定性
        for a in self.anchors:
            self._recalc_anchor(a.id, group=ev_by_anchor.get(a.id, []))
        self._iteration += dt  # 推进有效状态更新计数（τ）
        self.updated_at = now()

    # ── v1.5 注意力增益（#115 神经科学实证：多看几眼=价值升）──
    def register_recall(self, evidence_ids):
        """记录证据被召回命中 → 黏滞升稳（gaze 增益↔τ(x) 黏滞吸收）。

        #115 实证：人类面对多选项系统性低估中间项，但注意力（多看几眼）像增益旋钮
        因果重塑主观价值（关键窗口在奖励揭晓前）。映射到液环：被反复召回的证据黏滞升稳，
        对应三轨成核门槛≥2 + active recall 反复读取的离散版「升权」。

        守关键窗口（证据吸收须在 crystallization 前）：仅对未归档证据升权；
        已归档(archived)证据不升（零丢失，仅降权不增权）。
        """
        ATTENTION_GAIN_K = 0.15  # 黏滞系数：每次召回把 weight 向 1.0 渐进逼近
        ids = set(evidence_ids) if evidence_ids else set()
        for e in self.evidences:
            if e.id in ids and not e.archived and not e.superseded_by:
                e.recall_hits += 1
                e.last_recall_at = now()  # 记录召回时间，供 lifecycle 老化判据
                # 黏滞增益：gaze 增益饱和曲线（多看一次不立即封顶，黏滞累积）
                e.weight = min(1.0, e.weight + ATTENTION_GAIN_K * (1.0 - e.weight))

    # ── v1.8 软取代（supersede）原语：显式语义取代（区别于 lifecycle 自动老化归档）──
    def supersede_evidence(self, loser_id: str, winner_id: str) -> dict:
        """软取代：loser 被 winner 显式取代（语义冗余）。

        零丢失（不删 loser，仅标 superseded_by + 时间戳）；winner 反向记录 supersedes 血缘指针，
        使"我取代了谁"可审计。与 archived 正交：archived=熵增自动清理；superseded_by=治理动作显式取代。
        二者皆退出 active recall（见 register_recall / ll_recall 过滤）、list 全量可见供审计。

        调用方负责 save()（与 archive / _lifecycle_sweep 之外治理动作一致）。
        防呆：
          - loser==winner → 拒绝（自取代无意义）
          - winner 不存在 → 拒绝（取代者须先存在，否则丧失血缘根基）
          - 重复取代同一 winner → 幂等（supersedes 去重，不堆叠）
          - v1.8.3 consensus 保护（Palantir owned 细化）：loser 若是 consensus 结晶的证据，
            禁止单边软取代——共识证据属于全体贡献者，只能走全员 dissolve（与 delete_as 同语义）。
        """
        if loser_id == winner_id:
            return {"ok": False, "error": "self_supersede_rejected", "loser_id": loser_id}
        by_id = {e.id: e for e in self.evidences}
        if loser_id not in by_id:
            return {"ok": False, "error": "loser_not_found", "loser_id": loser_id}
        if winner_id not in by_id:
            return {"ok": False, "error": "winner_not_found", "winner_id": winner_id}
        # v1.8.3 consensus 保护：consensus 结晶的证据禁止单边软取代（防 admin 误伤共识）
        consensus_evidence = {eid for m in self.memories if m.scope == "consensus"
                              for eid in m.evidence_ids}
        if loser_id in consensus_evidence:
            return {"ok": False,
                    "error": "forbidden: consensus evidence requires unanimous dissolution, not single supersede",
                    "loser_id": loser_id}
        loser = by_id[loser_id]
        winner = by_id[winner_id]
        ts = now()
        loser.superseded_by = winner_id
        loser.superseded_at = ts
        if loser_id not in winner.supersedes:
            winner.supersedes.append(loser_id)  # 记录被取代者（反向血缘指针）
            winner.supersedes = sorted(set(winner.supersedes))
        if not winner.superseded_at:
            winner.superseded_at = ts
        return {
            "ok": True, "loser_id": loser_id, "winner_id": winner_id,
            "loser_superseded_by": loser.superseded_by,
            "winner_supersedes": winner.supersedes,
            "timestamp": ts,
        }

    # ── v1.8.2 治理/问责查询（Semantica 借鉴：时间旅行/影响分析/实体消解，全部只读）──
    def state_at(self, dt_str: str) -> dict:
        """时间点快照（time-travel）：返回 dt 时刻应可见的记忆状态。

        Semantica `state_at("2024-01-01")` 对应物。基于现存节点时间戳过滤：
          - evidence：timestamp<=dt 且未 archived 且未 superseded_by
          - memory：formed_at<=dt
        只读零副作用。局限：被 delete 真删的节点无法回溯（液环 append-only 但 delete 是真删，
        如需完整历史须重放 audit.log——标注为待办，本方法为近似的现行可见快照）。
        """
        try:
            dt = datetime.fromisoformat(dt_str)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_datetime", "at": dt_str}
        anchor_name = {a.id: a.name for a in self.anchors}
        evs = []
        for e in self.evidences:
            if e.archived or e.superseded_by:
                continue
            try:
                if datetime.fromisoformat(e.timestamp) > dt:
                    continue
            except ValueError:
                continue
            evs.append({
                "id": e.id, "content": e.content, "agent_id": e.agent_id,
                "category": anchor_name.get(e.anchor_id, ""),
                "timestamp": e.timestamp,
            })
        mems = []
        for m in self.memories:
            try:
                if datetime.fromisoformat(m.formed_at) > dt:
                    continue
            except ValueError:
                continue
            mems.append({
                "id": m.id, "content": m.content, "scope": m.scope,
                "contributors": m.contributors, "formed_at": m.formed_at,
            })
        return {"ok": True, "at": dt_str, "evidence_count": len(evs),
                "memory_count": len(mems), "evidences": evs, "memories": mems}

    def analyze_impact(self, node_id: str, depth: int = 2) -> dict:
        """影响分析：从节点沿因果边/血缘 BFS 展开下游影响子图（决策问责）。

        Semantica `analyze_decision_impact()` 对应物。出边=node.causal 的
        enables/causes/contradicts（我催生了/使能了/反驳了谁）；血缘=引用该 evidence 的
        结晶 Memory（used_in）。返回分层影响集，供"这条决策影响了谁"审计。只读。
        """
        nodes: dict = {}
        for e in self.evidences:
            nodes[e.id] = {"type": "evidence", "node": e}
        for m in self.memories:
            nodes[m.id] = {"type": "memory", "node": m}
        if node_id not in nodes:
            return {"ok": False, "error": "node_not_found", "node_id": node_id}

        def _out(id_: str):
            n = nodes[id_]["node"]
            res = []
            for rel in ("enables", "causes", "contradicts"):
                for d in n.causal.get(rel, []):
                    if d in nodes:
                        res.append((d, rel))
            if nodes[id_]["type"] == "evidence":
                for m in self.memories:
                    if id_ in m.evidence_ids:
                        res.append((m.id, "used_in"))
            return res

        levels: list = []
        frontier: list = [(node_id, "root", 0)]
        seen = {node_id}
        for L in range(1, int(depth) + 1):
            nxt: list = []
            for cur, _r, _lvl in frontier:
                for d, r in _out(cur):
                    if d in seen:
                        continue
                    seen.add(d)
                    nxt.append((d, r, L))
            if not nxt:
                break
            levels.append([{
                "node_id": d, "type": nodes[d]["type"], "relation": r, "level": L,
                "content": (nodes[d]["node"].content or "")[:80],
            } for d, r, L in nxt])
            frontier = nxt
        return {"ok": True, "root": node_id, "depth": depth,
                "levels": levels, "total_affected": sum(len(l) for l in levels)}

    def find_duplicates(self, threshold: float = 0.8, max_pairs: int = 20) -> dict:
        """重复候选检测（实体消解前置，禁向量）。

        Semantica DuplicateDetector 对应物但走字符级：对未归档/未取代的 evidence 做
        字符 bigram Jaccard 相似度；blocking 预过滤（首 4 字符相同 且 长度比接近）控 O(n²)。
        只报告候选、不自动处理（守取自动/存保守铁律），供治理者显式 supersede 取代。
        """
        evs = [e for e in self.evidences if not e.archived and not e.superseded_by]
        if len(evs) < 2:
            return {"ok": True, "candidates": [], "pairs_scanned": 0}

        def _bigrams(s: str):
            s = re.sub(r"\s+", "", s or "")
            return {s[i:i + 2] for i in range(max(0, len(s) - 1))}

        def _jaccard(a: set, b: set) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / len(a | b)

        grams = {e.id: _bigrams(e.content) for e in evs}
        lengths = {e.id: len(e.content or "") for e in evs}
        by_prefix: dict = {}
        for e in evs:
            by_prefix.setdefault((e.content or "").strip()[:4], []).append(e.id)

        scored: list = []
        scanned = 0
        seen_pairs: set = set()
        for e in evs:
            pref = (e.content or "").strip()[:4]
            for other_id in by_prefix.get(pref, []):
                if other_id == e.id:
                    continue
                key = (e.id, other_id) if e.id < other_id else (other_id, e.id)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                L1, L2 = lengths[e.id], lengths[other_id]
                if L1 <= 0 or L2 <= 0:
                    continue
                if abs(L1 - L2) / max(L1, L2) > 0.5:
                    continue  # 长度差异过大不可能是重复
                scanned += 1
                j = _jaccard(grams[e.id], grams[other_id])
                if j >= threshold:
                    scored.append((j, e.id, other_id))
        scored.sort(key=lambda x: x[0], reverse=True)
        candidates = [{
            "src_id": a, "dst_id": b, "similarity": round(j, 3),
            "src": next((x.content for x in evs if x.id == a), "")[:80],
            "dst": next((x.content for x in evs if x.id == b), "")[:80],
        } for j, a, b in scored[:max_pairs]]
        return {"ok": True, "candidates": candidates,
                "pairs_scanned": scanned, "threshold": threshold}

    # ── v1.7 证据老化回收（督办项警示①落地）──
    def _lifecycle_sweep(self) -> dict:
        """主动老化回收：长期未召回 + 权重冷却到地板 + 非结晶来源 + 非冲突 → 冷归档。

        直接消警示①（append-only 长期 horizon 退化）：避免无限 append 致激活集膨胀。
        守铁律：archived=True 仅冷归档零丢失（可被解冻）；archived_at 留痕可审计；
        timestamp 完整保留（守警示②：语义压缩销毁时序线索 → 此处不压缩、仅隔离）。
        不依赖相似度（守警示③ / 禁向量铁律）。
        双判据理论基点（v1.7.1）：floor_weight=β(熵增成本权重)、ttl_eps=horizon(回忆效用半衰期)，
        来自增益自适应 efficient-coding objective（cost=α·准确性+β·熵增成本）的归一化推导，
        与"调权重>重写证据"同源——增益调节而非重连的生物实证支撑。
        """
        protected = {eid for m in self.memories for eid in m.evidence_ids}
        now_ts = now()
        swept: list[str] = []
        for e in self.evidences:
            if e.archived:
                continue
            if e.id in protected:
                continue  # 结晶血缘保护：被任一 memory 引用的证据不归档
            if e.relation == "contradiction":
                continue  # 冲突证据零丢失优先（守反证轨对抗群体幻觉）
            # 年龄判据：优先用最后召回时间，否则用创建时间(timestamp)；二者皆空→保守不归档
            ref_ts = e.last_recall_at or e.timestamp
            if not ref_ts:
                continue
            try:
                age = (datetime.fromisoformat(now_ts) - datetime.fromisoformat(ref_ts)).total_seconds()
            except ValueError:
                continue
            if age < LIFECYCLE_TTL_EPS:
                continue  # 未老化超 TTL（新记忆/近期召回）→ 不归档
            if e.weight >= LIFECYCLE_FLOOR_WEIGHT:
                continue  # 权重未冷却到地板 → 仍活跃
            # 老化超 TTL 且权重冷却到地板 → 冷归档
            e.archived = True
            e.archived_at = now_ts
            swept.append(e.id)
        if swept:
            self.updated_at = now_ts
        return {"swept": len(swept), "swept_ids": swept, "total": len(self.evidences)}

    def lifecycle_sweep(self) -> dict:
        """公开老化回收入口（供 8790 手动/定时触发，或测试）。"""
        return self._lifecycle_sweep()

    def recall(self, query: str, agent_id: str = "", top_k: int = 5):
        """统一召回入口（封装 RAR 倒排索引）+ 自动 register_recall。

        命中 evidence 类型的结果自动 bump 注意力增益（#115 多看几眼=价值升）。
        返回 [(mid, score, meta), ...]，与 RARIndex.recall 兼容。
        注意：LiquidReweight / LiquidSelfSpin 为 client 引擎/意识层，独立运行时不经此入口；
        其命中若在 server 层走 RAR 仍由本方法覆盖（server 可逐步切换至此入口）。
        """
        from .rar import RARIndex
        cands = RARIndex.visible_candidates(self, agent_id)
        if not cands:
            return []
        # 用 RARIndex.build 每次独立构建（不依赖 build_or_cache 的跨 state 缓存，
        # 其 key 仅含 version+agent_id，会复用旧 idx 致跨 state 污染 → evidence 不被 bump）
        idx = RARIndex.build(cands, version=getattr(self, "version", ""))
        hits = idx.recall(query, top_k=top_k)
        ev_ids = [mid for (mid, _sc, meta) in hits if meta.get("type") == "evidence"]
        if ev_ids:
            self.register_recall(ev_ids)
        return hits

    def perceive_replay_pressure(self) -> dict:
        """重放压力感知（#116）：暴露存在 over-replay 的 contradiction 的 memory 及其压力值。

        Science 2026-06-04 实证：压力记忆印迹的病理性频繁自发重放是睡眠碎片化核心驱动力，
        靶向单一印痕抑制即可逆转。本方法把该"压力"显式暴露，供上层决策；
        局部阻尼已在 `._update_memory_stability` 中按 recall_hits 超阈自动触发（靶向单印痕降温）。

        返回 {memory_id: {content, replay_pressure, stability}, ...}，仅含压力>0 的 memory。
        """
        out: dict = {}
        for m in self.memories:
            pressure = getattr(m, "replay_pressure", 0.0)
            if pressure > 0.0:
                content = m.content if len(m.content) <= 40 else m.content[:40] + "..."
                out[m.id] = {
                    "content": content,
                    "replay_pressure": pressure,
                    "stability": m.stability,
                }
        return out

    # ─────────────────────────────────────────────────────────────
    # v0.9.3 多智能体授权原语
    # 修复共识误删事故：把归属校验从各接入层 wrapper 固化进核心，
    # 使 REST / MCP / trae 桥 / 任意未来 agent 共用同一套授权，杜绝重复实现漂移。
    # 注：本段不调用 save()，落盘由调用方在变更生效时负责（与 add_evidence 等一致）。
    # ─────────────────────────────────────────────────────────────

    def list_for(self, agent_id: str = "", category: Optional[str] = None) -> list[dict]:
        """归属隔离列表：共识仅对贡献者可见，私有仅对贡献者可见，他人证据隔离。

        - evidence：仅返回 agent_id 本人的（agent_id 为空 → 不返回任何证据，避免泄漏）。
        - consensus 结晶：仅当 agent_id 是其 contributors 之一才返回（修复误删根因）。
        - private 结晶：仅当 agent_id 在 contributors 才返回。
        - 未知 scope 一律不暴露。
        """
        out: list[dict] = []
        if agent_id:
            for e in self.evidences:
                if e.agent_id != agent_id:
                    continue
                cat = ""
                anchor = next((a for a in self.anchors if a.id == e.anchor_id), None)
                if anchor is not None:
                    cat = anchor.name
                # v1.1 evidence 层加 category 过滤（修复：原实现 evidence 层完全不过滤 category，
                #      导致 POST /list?category=X 返回 agent_id 的全部 evidence 而非 X 类）
                if category and category != "(结晶)" and cat != category:
                    continue
                out.append({
                    "memory_id": e.id,
                    "type": "evidence",
                    "category": cat,
                    "content": e.content,
                    "agent_id": e.agent_id,
                })
        for m in self.memories:
            # memory 层：category 为空或 "(结晶)" 时返回；其他 category 值只过滤 evidence 层
            if category and category != "(结晶)":
                continue
            if m.scope == "consensus":
                if not agent_id or agent_id not in m.contributors:
                    continue
            elif m.scope == "private":
                if not agent_id or agent_id not in m.contributors:
                    continue
            else:
                continue
            out.append({
                "memory_id": m.id,
                "type": "memory",
                "category": "(结晶)",
                "content": m.content,
                "scope": m.scope,
                "contributors": m.contributors,
            })
        return out

    def delete_as(self, agent_id: str, memory_id: str) -> dict:
        """带归属校验的删除（不自行落盘，由调用方 save）。

        约束：
        - 缺 agent_id → 拒绝。
        - evidence：仅所有者可删。
        - private 结晶：仅贡献者可删。
        - consensus 结晶：禁止单端删除（需全体一致 dissolve），返回明确错误。
        - content 兜底：仅删调用者贡献的 private + 本人 evidence；命中 consensus 整体拒绝。
        """
        if not agent_id:
            return {"ok": False, "error": "forbidden: agent_id required to delete",
                    "memory_id": memory_id}
        ev = next((e for e in self.evidences if e.id == memory_id), None)
        if ev is not None:
            if ev.agent_id != agent_id:
                return {"ok": False, "error": "forbidden: not owner of evidence",
                        "memory_id": memory_id}
            for a in self.anchors:
                if ev.anchor_id == a.id and memory_id in a.evidence_ids:
                    a.evidence_ids.remove(memory_id)
            self.evidences.remove(ev)
            return {"ok": True, "deleted": "evidence", "memory_id": memory_id}
        mem = next((m for m in self.memories if m.id == memory_id), None)
        if mem is not None:
            if mem.scope == "consensus":
                return {"ok": False,
                        "error": "forbidden: consensus memory requires unanimous contributor dissolution, not single-agent delete",
                        "memory_id": memory_id}
            if agent_id not in mem.contributors:
                return {"ok": False, "error": "forbidden: not a contributor of this private memory",
                        "memory_id": memory_id}
            self.memories.remove(mem)
            return {"ok": True, "deleted": "memory", "memory_id": memory_id}
        mems = [m for m in self.memories if m.content == memory_id]
        if mems:
            if any(m.scope == "consensus" for m in mems):
                return {"ok": False,
                        "error": "forbidden: content matches consensus memory; use unanimous dissolution",
                        "memory_id": memory_id}
            owned = [m for m in mems if agent_id in m.contributors]
            if not owned:
                return {"ok": False, "error": "forbidden: no owned private memory matches content",
                        "memory_id": memory_id}
            ev_ids = {eid for m in owned for eid in m.evidence_ids}
            ev_ids |= {e.id for e in self.evidences if e.content == memory_id and e.agent_id == agent_id}
            for m in owned:
                self.memories.remove(m)
            to_del = [e for e in self.evidences if e.id in ev_ids]
            for e in to_del:
                for a in self.anchors:
                    if e.id in a.evidence_ids:
                        a.evidence_ids.remove(e.id)
                self.evidences.remove(e)
            return {"ok": True, "deleted": "memory+evidence(owned only)", "memory_id": memory_id,
                    "memories": len(owned), "evidences": len(to_del)}
        return {"ok": False, "error": "not_found", "memory_id": memory_id}

    def dissolve_as(self, agent_id: str, memory_id: str, votes_root: Path) -> dict:
        """consensus 结晶的合法移除：全体贡献者各调用一次，集齐才真删。

        - 缺 agent_id / 非 consensus / 非贡献者 → 拒绝。
        - 每贡献者一票；投票持久化于 votes_root/.dissolve_votes.json。
        - 集齐 → 真删（移除 memory + 其底层 evidence），清票，返回 dissolved=True。
        - 未集齐 → 记票返回进度，不删。
        不自行落盘 state（由调用方在 dissolved=True 时 save）。
        """
        if not agent_id:
            return {"ok": False, "error": "forbidden: agent_id required to dissolve",
                    "memory_id": memory_id}
        mem = next((m for m in self.memories if m.id == memory_id), None)
        if mem is None:
            return {"ok": False, "error": "not_found", "memory_id": memory_id}
        if mem.scope != "consensus":
            return {"ok": False, "error": "not_consensus: use delete_as for private memory",
                    "memory_id": memory_id}
        if agent_id not in mem.contributors:
            return {"ok": False, "error": "forbidden: not a contributor of this consensus",
                    "memory_id": memory_id}
        votes = _load_dissolve_votes(votes_root)
        cast = set(votes.get(memory_id, []))
        cast.add(agent_id)
        need = set(mem.contributors)
        if cast >= need:
            ev_ids = set(mem.evidence_ids)
            self.memories.remove(mem)
            to_del = [e for e in self.evidences if e.id in ev_ids]
            for e in to_del:
                for a in self.anchors:
                    if e.id in a.evidence_ids:
                        a.evidence_ids.remove(e.id)
                self.evidences.remove(e)
            votes.pop(memory_id, None)
            _save_dissolve_votes(votes_root, votes)
            return {"ok": True, "dissolved": True, "memory_id": memory_id,
                    "evidences": len(to_del), "voters": sorted(cast)}
        votes[memory_id] = sorted(cast)
        _save_dissolve_votes(votes_root, votes)
        return {"ok": True, "dissolved": False, "memory_id": memory_id,
                "voted": sorted(cast), "remaining": sorted(need - cast)}

# ── dissolve 投票持久化（独立于核心 dataclass，守 North-Star 零改动）──






# ==============================================================================
# ─────────────────────────────────────────────────────────────
# 研究脚手架区（research-only · 不在 8790 生产路径）
# 以下 CPERegularizer / SelfRefineEngine / meta_thinker 仅供 CLI 的
# audit / evolution 子命令与单测使用；8790 server 不加载，不影响稳态。
# ─────────────────────────────────────────────────────────────
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



# ==============================================================================
# SelfRefineEngine — 后向自进化（借鉴 MemMA 原位自进化）
# 融合点：利用液环现有锚点体系做探测QA验证 + 证据锚定修复
# ==============================================================================









# ==============================================================================
# MetaThinker — 前向策略层（借鉴 MemMA Meta-Thinker，零LLM方案）
# ==============================================================================





# ==============================================================================
