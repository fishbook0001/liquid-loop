"""workspace_ops_1 — WorkspaceState Mixin①：L1 工作记忆 + 锚点生命周期（零改写搬运）。"""
from __future__ import annotations

from .textutil import (
    now,
    uid,
    _keyword_overlap,
)
from .cpe import AGGREGATE_ANCHOR_NAMES
from .guard import validate_content


from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any
from collections.abc import Callable
import hashlib
import sys
import os
import logging
from collections import Counter, defaultdict

from .cognitive_budget import CognitiveBudgetStabilizer






from .workspace_models import (
    Anchor,
    Conflict,
    Evidence,
    Memory,
    WorkingMemoryItem,
    _load_consensus_parties,
)


class WorkspaceOps1:
    if TYPE_CHECKING:
        # ── 跨分片成员声明（mypy 专用）──────────────────────────────
        # Mixin 分片后 mypy 看不到兄弟分片/WorkspaceState 上定义的成员（attr-defined 误报源）。
        # 放在 `if TYPE_CHECKING` 内：运行期不求值 → 不产生 __annotations__ →
        # 不会污染 @dataclass 的字段收集（已实测字段数与顺序不变）。
        # 类型与 workspace_state.WorkspaceState 的字段声明保持一致。
        anchors: list[Anchor]
        evidences: list[Evidence]
        memories: list[Memory]
        conflicts: list[Conflict]
        working_memory: list[WorkingMemoryItem]
        working_memory_capacity: int
        working_memory_default_ttl_hours: int
        overlap_cache: dict[str, Any]
        _iteration: int
        updated_at: str
        canon_fn: Callable[..., Any] | None
        # 兄弟分片的方法（运行期由 MRO 解析）
        _lifecycle_sweep: Callable[..., Any]           # workspace_ops_3
        _update_memory_stability: Callable[..., Any]   # workspace_ops_2

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
                except ValueError as _e:
                    sys.stderr.write(f"[workspace:warn] temp记忆过期日期解析失败: {_e}\n")
            kept.append(m)
        self.memories = kept
        return evicted

    # ── L1工作记忆层方法（v2.1.0）──

    def add_working_memory(self, content: str, agent_id: str = "",
                           ttl_hours: int | None = None, priority: int = 0,
                           source: str = "") -> WorkingMemoryItem:
        """添加一条L1工作记忆。自动过期回收+容量上限。"""
        from datetime import datetime, timezone, timedelta
        ttl = ttl_hours if ttl_hours is not None else self.working_memory_default_ttl_hours
        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()
        item = WorkingMemoryItem(
            content=content, agent_id=agent_id,
            expires_at=expires, priority=priority, source=source,
        )
        self.working_memory.append(item)
        self._enforce_working_memory_capacity()
        self.updated_at = now()
        return item

    def get_working_memory(self, agent_id: str = "", limit: int = 20) -> list[WorkingMemoryItem]:
        """获取未过期的工作记忆，按优先级+创建时间排序。"""
        self.evict_expired_working_memory()
        items = [w for w in self.working_memory if not w.is_expired()]
        if agent_id:
            items = [w for w in items if w.agent_id == agent_id or not w.agent_id]
        items.sort(key=lambda w: (-w.priority, w.created_at), reverse=False)
        return items[:limit]

    def recall_working_memory(self, item_id: str) -> WorkingMemoryItem | None:
        """召回一条工作记忆（记录召回次数，用于提升判断）。"""
        for w in self.working_memory:
            if w.id == item_id:
                w.recall_hits += 1
                w.last_recall_at = now()
                return w
        return None

    def promote_working_memory(self, item_id: str, anchor_name: str = "working_memory_promoted",
                               principle: str = "") -> Evidence | None:
        """将工作记忆提升为L2证据（长期记忆）。提升后工作记忆标记promoted_to。"""
        item = next((w for w in self.working_memory if w.id == item_id), None)
        if not item:
            return None
        anchor = next((a for a in self.anchors if a.name == anchor_name), None)
        if not anchor:
            anchor = self.add_anchor(anchor_name, description="L1工作记忆提升的证据")
        evidence = self.add_evidence(
            anchor, item.content, agent_id=item.agent_id,
            principle=principle or item.source,
        )
        if evidence:
            item.promoted_to = evidence.id
        return evidence

    def evict_expired_working_memory(self, ref_dt=None) -> int:
        """回收过期的工作记忆。返回回收条数。"""
        if not self.working_memory:
            return 0
        kept, evicted = [], 0
        for w in self.working_memory:
            if w.is_expired(ref_dt) and not w.promoted_to:
                evicted += 1
                continue
            kept.append(w)
        self.working_memory = kept
        return evicted

    def _enforce_working_memory_capacity(self):
        """强制工作记忆容量上限：超量时按优先级+召回次数淘汰最低的。"""
        if len(self.working_memory) <= self.working_memory_capacity:
            return
        # 排序：先保留未过期的，再按优先级+召回次数，淘汰最低的
        self.working_memory.sort(
            key=lambda w: (w.is_expired(), -w.priority, -w.recall_hits, w.created_at)
        )
        # 未提升的先淘汰
        unpromoted = [w for w in self.working_memory if not w.promoted_to]
        promoted = [w for w in self.working_memory if w.promoted_to]
        overflow = len(self.working_memory) - self.working_memory_capacity
        if overflow > 0 and unpromoted:
            # 淘汰最旧的未提升的
            unpromoted.sort(key=lambda w: (w.priority, w.recall_hits, w.created_at))
            self.working_memory = promoted + unpromoted[overflow:]
        self.updated_at = now()

    # ── API 层：add_anchor / add_evidence（让 README 示例能跑通）──

    def add_anchor(self, name: str, description: str = "",
                   value_density: str = "medium",
                   cognitive_stage: str = "raw",
                   liquidity: str = "warm",
                   ttl_days: float = 0.0) -> Anchor:
        """创建并添加一个新锚点。

        ttl_days: 锚点寿命(天)；0=永不过期(向后兼容默认)。>0 则离现算 expires_at。
        成核即记录 version_hash 状态快照(缺3 old-logits 基准) + 初始陈旧度(缺4)。
        """
        a = Anchor(
            id=uid(), name=name, description=description,
            value_density=value_density, cognitive_stage=cognitive_stage,
            liquidity=liquidity, ttl_days=ttl_days,
        )
        if ttl_days > 0:
            a.expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
        a.version_hash = a.snapshot_version()
        a.recalc_staleness()
        self.anchors.append(a)
        self.updated_at = now()
        return a

    def add_evidence(self, anchor, content: str, quality: str = "normal", agent_id: str = "",
                     dedup: bool = False, relation: str = "support",
                     target_memory_id: str = "", principle: str = "",
                     priority: int = 0) -> Evidence | None:
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
            anchor_version_hash=target.version_hash,
            quality=quality, timestamp=now(), agent_id=agent_id,
            relation=relation, target_memory_id=target_memory_id,
            added_iter=self._iteration, principle=principle,
            priority=priority,
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
            # ── 共识参与者白名单守卫（2026-09-23 可选，fail-open）──
            # 默认仅告警（保持现状）；LIQUID_CONSENSUS_ENFORCE=1 时硬拒非白名单 agent 成核
            _parties = _load_consensus_parties()
            if _parties is not None:
                _non = owners - _parties
                if _non:
                    if os.environ.get("LIQUID_CONSENSUS_ENFORCE") == "1":
                        logging.getLogger("liquid_loop.workspace").warning(
                            "[consensus-guard] 拒绝非白名单 agent %s 形成共识(content=%s)，已降级",
                            sorted(_non), content[:40])
                        continue
                    logging.getLogger("liquid_loop.workspace").warning(
                        "[consensus-guard] 非白名单 agent %s 参与共识(仅告警, fail-open)(content=%s)",
                        sorted(_non), content[:40])
            # 已存在的共识结晶：把新一致方并入 contributors（动态扩展，支撑三方+ CCI 计量）
            existing = next((m for m in self.memories if m.scope in ("consensus", "authority_direct") and m.content == content), None)
            if existing is not None:
                merged = set(existing.contributors) | owners
                if merged != set(existing.contributors):
                    existing.contributors = sorted(merged)
                    # v2.1 新owner加入时补观测验证
                    for e in group:
                        if e.content == content and e.agent_id in owners and e.agent_id not in existing.observation_hashes:
                            existing.observation_hashes[e.agent_id] = hashlib.sha256(f'{e.id}:{e.agent_id}:{e.content}'.encode()).hexdigest()[:16]
                            existing.observation_timestamps[e.agent_id] = e.created_at if hasattr(e, 'created_at') and e.created_at else now()
                continue
            if (content, "consensus") not in crystallized_keys and (content, "authority_direct") not in crystallized_keys:
                evidence_ids = [e.id for e in group if e.content == content and e.agent_id in owners]
                confidence = min(len(owners) / 2.0, 1.0)
                # v1.3 因果演化循环成核：血缘注册（caused_by=成核证据链；contradicts=反证指向）
                causal = {"caused_by": list(evidence_ids), "causes": [], "enables": [], "contradicts": []}
                contra = [e.target_memory_id for e in group if e.content == content
                          and e.relation == "contradiction" and e.target_memory_id]
                if contra:
                    causal["contradicts"] = contra
                # v2.1 真独立观测验证：成核时自动记录各contributor的观测哈希+时间戳
                obs_hashes = {}
                obs_timestamps = {}
                for e in group:
                    if e.content == content and e.agent_id in owners:
                        obs_hashes[e.agent_id] = hashlib.sha256(f'{e.id}:{e.agent_id}:{e.content}'.encode()).hexdigest()[:16]
                        obs_timestamps[e.agent_id] = e.created_at if hasattr(e, 'created_at') and e.created_at else now()
                # v2.1 权威直注自动判定：证据带authority/rule标签或来自权威注入源 → authority_direct核
                auth_evidences = [e for e in group if e.content == content and e.agent_id in owners
                                  and (hasattr(e, 'tags') and e.tags and any(t in ('authority', 'rule', 'spec') for t in e.tags))]
                if auth_evidences and len(owners) == 1:
                    # 单源权威注入 → authority_direct
                    auth_source = auth_evidences[0].agent_id
                    self.memories.append(Memory(
                        content=content,
                        evidence_ids=evidence_ids,
                        confidence=1.0,
                        scope="authority_direct",
                        contributors=sorted(owners),
                        causal=causal,
                        observation_hashes=obs_hashes,
                        observation_timestamps=obs_timestamps,
                        authority_source=auth_source,
                        authorization_record=f"auto_inferred@{now()},source={auth_source}",
                    ))
                else:
                    # 多源观测一致 → consensus
                    self.memories.append(Memory(
                        content=content,
                        evidence_ids=evidence_ids,
                        confidence=confidence,
                        scope="consensus",
                        contributors=sorted(owners),
                        causal=causal,
                        observation_hashes=obs_hashes,
                        observation_timestamps=obs_timestamps,
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
        # P3c（2026-09-12）：豁免名单收敛为单一口径源（liquid_loop.cpe.AGGREGATE_ANCHOR_NAMES），
        # 消除此前的双份字面量（CPE 扫描与冲突检测曾各自维护，存在漂移风险）。
        if _agg_anchor and _agg_anchor.name in AGGREGATE_ANCHOR_NAMES:
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
        for _cp, contents in by_cp.items():
            if len(contents) < 2:
                continue
            distinct_rels: set[str] = set()
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


    def _recalc_anchor(self, anchor_id: str, group: list | None = None):
        if group is None:
            group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if not group:
            return
        avg_weight = sum(e.weight for e in group) / len(group)
        for a in self.anchors:
            if a.id == anchor_id:
                # base = 证据权重均值；stability = base + SEAL 自评层（seal_adjust 不被覆盖）
                base = avg_weight
                # base_stability 是**运行期动态挂载**的非字段属性（Anchor 无此 dataclass 字段）：
                # 消费方 = tests/{test_seal_persistence,test_peek_seal}.py 与
                # self_refine.py:220 的 getattr(a,"base_stability",a.stability) 兜底读。
                # 用 setattr 表达"动态挂载"本意（等价语义，且静态可判 → mypy/门禁不误报）。
                setattr(a, "base_stability", base)  # noqa: B010 —— 必须 setattr：Anchor 无此字段声明
                a.stability = round(min(1.0, max(0.1, base * a.conflict_penalty + a.seal_adjust)), 3)
                evidence_count = len(group)
                a.decay_value(evidence_count=evidence_count)
                a.recalc_strength(evidence_count)
                a.auto_classify(evidence_count)

