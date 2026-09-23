"""workspace_ops_3 — WorkspaceState Mixin③：生命周期治理 + 召回 + 删除/溶解（零改写搬运）。"""
from __future__ import annotations

from .textutil import (
    now,
    uid,
    _load_dissolve_votes,
    _save_dissolve_votes,
)


from datetime import datetime
from typing import TYPE_CHECKING, Any
from collections.abc import Callable
from pathlib import Path







from .workspace_models import (
    ANCHOR_DEAD_GRACE_DAYS,
    ANCHOR_MERGE_SIM,
    ANCHOR_SPLIT_CAP,
    ANCHOR_STALE_FLOOR,
    Anchor,
    Evidence,
    LIFECYCLE_FLOOR_WEIGHT,
    LIFECYCLE_TTL_EPS,
    Memory,
)


class WorkspaceOps3:
    if TYPE_CHECKING:
        # ── 跨分片成员声明（mypy 专用，见 workspace_ops_1 同款注释）──
        anchors: list[Anchor]
        evidences: list[Evidence]
        memories: list[Memory]
        updated_at: str
        register_recall: Callable[..., Any]   # workspace_ops_2

    def _lifecycle_sweep(self) -> dict:
        """主动老化回收：长期未召回 + 权重冷却到地板 + 非结晶来源 + 非冲突 → 冷归档。

        直接消警示①（append-only 长期 horizon 退化）：避免无限 append 致激活集膨胀。
        守铁律：archived=True 仅冷归档零丢失（可被解冻）；archived_at 留痕可审计；
        timestamp 完整保留（守警示②：语义压缩销毁时序线索 → 此处不压缩、仅隔离）。
        不依赖相似度（守警示③ / 禁向量铁律）。
        双判据理论基点（v1.7.1）：floor_weight=β(熵增成本权重)、ttl_eps=horizon(回忆效用半衰期)，
        来自增益自适应 efficient-coding objective（cost=α·准确性+β·熵增成本）的归一化推导，
        与"调权重>重写证据"同源——增益调节而非重连的生物实证支撑。

        v2.2: CPE演化通道——观察期内的EVOLVE证据处理：
        - 观察期内召回≥3次 → 转正(graduated)，weight恢复
        - 观察期已过且召回<3次 → 拒绝(rejected)，冷归档
        """
        # ── P1: CPE演化通道观察期处理 ──
        now_ts = now()
        evolved: list[str] = []
        rejected: list[str] = []
        for e in self.evidences:
            if getattr(e, 'evolve_status', '') != "observing":
                continue
            observe_until = getattr(e, 'evolve_observe_until', '')
            recall_hits = getattr(e, 'evolve_recall_hits', 0)
            # 召回次数计入（register_recall时已bump，这里只做观察期判定）
            try:
                expired = observe_until and datetime.fromisoformat(observe_until) <= datetime.fromisoformat(now_ts)
            except ValueError:
                expired = False
            if recall_hits >= 3:
                # 观察期内被召回≥3次 → 转正
                e.evolve_status = "graduated"
                e.weight = min(1.0, e.weight + 0.2)  # 转正后weight恢复
                evolved.append(e.id)
            elif expired:
                # 观察期已过且召回<3次 → 拒绝，冷归档
                e.evolve_status = "rejected"
                e.archived = True
                e.archived_at = now_ts
                rejected.append(e.id)
        # ── 原有老化回收逻辑 ──
        protected = {eid for m in self.memories for eid in m.evidence_ids}
        swept: list[str] = []
        for e in self.evidences:
            if e.archived:
                continue
            if e.id in protected:
                continue  # 结晶血缘保护：被任一 memory 引用的证据不归档
            if e.relation == "contradiction":
                continue  # 冲突证据零丢失优先（守反证轨对抗群体幻觉）
            if getattr(e, 'evolve_status', '') == "observing":
                continue  # 观察期中的证据不参与老化回收
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
        if swept or evolved or rejected:
            self.updated_at = now_ts
        return {"swept": len(swept), "swept_ids": swept, "total": len(self.evidences),
                "evolved": len(evolved), "evolved_ids": evolved,
                "rejected": len(rejected), "rejected_ids": rejected}

    def lifecycle_sweep(self) -> dict:
        """公开老化回收入口（供 8790 手动/定时触发，或测试）。"""
        return self._lifecycle_sweep()

    # ===== 锚点生命周期机制补强（2026-09-22 飞哥拍板·全量深做） =====
    # 守铁律：零丢失(archived≠删) / 可审计(archived_at) / 禁向量 /
    # 所有"写"类方法默认 dry_run=True，须经显式 apply 才落盘，防不可逆误操作。

    def active_anchors(self) -> list:
        """返回未归档(活跃)锚点列表（recall/合并/分裂的活跃集过滤）。"""
        return [a for a in self.anchors if not a.archived]

    def recompute_staleness_all(self) -> int:
        """周期重算全部活跃锚点的常驻陈旧度（缺4·主动重算钩子）。"""
        n = 0
        for a in self.active_anchors():
            a.recalc_staleness()
            n += 1
        if n:
            self.updated_at = now()
        return n

    def probe_anchors(self) -> dict:
        """主动探活全部活跃锚点（缺2·定期巡检入口）。返回各级计数。"""
        counts = {"active": 0, "dormant": 0, "dead": 0, "unknown": 0}
        for a in self.active_anchors():
            lv = a.probe()
            counts[lv] = counts.get(lv, 0) + 1
        self.updated_at = now()
        return counts

    def anchor_version_drift(self) -> list:
        """检测 old-logits 不一致（缺3）：证据采集时锚点版本 ≠ 当前锚点版本。

        返回漂移证据 id 列表。用于识别"证据针对的是已被合并/分裂/重算改写的旧锚点"。
        """
        drift = []
        ver_by_id = {a.id: a.version_hash for a in self.anchors}
        for e in self.evidences:
            if e.archived:
                continue
            h = getattr(e, "anchor_version_hash", "")
            if h and h != ver_by_id.get(e.anchor_id, ""):
                drift.append(e.id)
        return drift

    def anchor_lifecycle_sweep(self, dry_run: bool = True) -> dict:
        """死锚点冷归档（缺1+缺4 联合驱动）：TTL 过期 或 (高陈旧度且零证据零访问)。

        守铁律：archived≠删；零丢失可解冻；archived_at 留痕。
        默认 dry_run=True（只报告候选，不落盘）——须经显式 apply 才改共享状态，
        防不可逆误操作（红线#硬不可逆）。
        """
        candidates = []
        for a in self.active_anchors():
            a.recalc_staleness()
            expired = a.is_expired()
            dead = (a.staleness_score >= ANCHOR_STALE_FLOOR
                    and a.access_count == 0
                    and len(a.evidence_ids) == 0
                    and a.age_days() >= ANCHOR_DEAD_GRACE_DAYS)
            if expired or dead:
                candidates.append(a.id)
        if dry_run or not candidates:
            return {"would_archive": candidates, "applied": 0, "dry_run": dry_run}
        ts = now()
        for aid in candidates:
            a = next((x for x in self.anchors if x.id == aid), None)
            if a and not a.archived:
                a.archived = True
                a.archived_at = ts
                a.liquidity = "archived"
        self.updated_at = ts
        return {"would_archive": candidates, "applied": len(candidates), "dry_run": False}

    def merge_similar_anchors(self, threshold: float = ANCHOR_MERGE_SIM,
                              dry_run: bool = True) -> dict:
        """相似锚点主动聚合（缺5·merge，区别于 cpe 冲突 MERGE）。

        用禁向量相似度(关键词重叠+Jaccard，复用 adaptive_crystallization 原语)判定；
        evidence 并集上限保护，避免无界膨胀。默认 dry_run。
        """
        from .adaptive_crystallization import _keyword_overlap, _jaccard
        act = self.active_anchors()
        merged = []
        skip = set()
        for i in range(len(act)):
            ai = act[i]
            if ai.id in skip:
                continue
            for j in range(i + 1, len(act)):
                aj = act[j]
                if aj.id in skip:
                    continue
                # 名称相似度是合并主门（缺5·merge 修正 2026-09-22）：
                # 旧实现用 name+description 的 Jaccard/keyword_overlap，被 CJK 字符级
                # token + 共享描述模板("MARVIS X 记忆锚点")污染——所有锚点对齐首锚得
                # 恒定 0.833，会误并 52 个互异锚点。同名/近名=同概念才该合并。
                name_a = (ai.name or "").strip()
                name_b = (aj.name or "").strip()
                if not name_a or not name_b:
                    continue
                name_sim = _jaccard(name_a, name_b)
                if name_sim < ANCHOR_MERGE_SIM:
                    continue
                sa = (ai.name + " " + ai.description).strip()
                sb = (aj.name + " " + aj.description).strip()
                if not sa or not sb:
                    continue
                score = max(_keyword_overlap(sa, sb), _jaccard(sa, sb))
                if score >= threshold:
                    if not dry_run:
                        ai.merge_with(aj)
                        for eid in aj.evidence_ids:
                            ev = next((e for e in self.evidences if e.id == eid), None)
                            if ev:
                                ev.anchor_id = ai.id
                    merged.append({"keep": ai.id, "absorb": aj.id,
                                   "name_sim": round(name_sim, 3), "score": round(score, 3)})
                    skip.add(aj.id)
        return {"merged": merged, "applied": 0 if dry_run else len(merged), "dry_run": dry_run}

    def split_oversized_anchors(self, cap: int = ANCHOR_SPLIT_CAP,
                                dry_run: bool = True) -> dict:
        """过大锚点分裂（缺5·split）：evidence 超 cap 的锚点拆为子锚点。

        父锚点保留(置 split_from 血缘)，子锚点继承 description + 切片 evidence_ids；
        移动的证据其 Evidence.anchor_id 重指派到子锚点，保持双向一致。零丢失，可审计。
        默认 dry_run。
        """
        split_info = []
        for a in self.active_anchors():
            if len(a.evidence_ids) <= cap:
                continue
            parts = a.split_partition(cap, evidences=self.evidences)
            split_info.append({"anchor": a.id, "n_parts": len(parts), "evidence": len(a.evidence_ids)})
            if not dry_run:
                for idx, part in enumerate(parts[1:], start=1):  # parts[0] 留守父锚点
                    child = Anchor(
                        id=uid(), name=f"{a.name}#{idx}", description=a.description,
                        value_density=a.value_density, cognitive_stage=a.cognitive_stage,
                        liquidity=a.liquidity,
                    )
                    child.evidence_ids = list(part)
                    child.split_from = a.id
                    child.version_hash = child.snapshot_version()
                    child.recalc_staleness()
                    for eid in part:
                        ev = next((e for e in self.evidences if e.id == eid), None)
                        if ev:
                            ev.anchor_id = child.id
                    self.anchors.append(child)
                a.evidence_ids = list(parts[0])       # 父锚点只留守第一部分，避免与子锚点重复
                a.split_from = a.split_from or a.id  # 标记已分裂(父)
        if split_info and not dry_run:
            self.updated_at = now()
        return {"split": split_info,
                "applied": 0 if dry_run else sum(s["n_parts"] - 1 for s in split_info),
                "dry_run": dry_run}

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

    def list_for(self, agent_id: str = "", category: str | None = None) -> list[dict]:
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
            if m.scope in ("consensus", "private"):
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
        result = self._delete_evidence_by_id(agent_id, memory_id)
        if result is not None:
            return result
        result = self._delete_memory_by_id(agent_id, memory_id)
        if result is not None:
            return result
        result = self._delete_by_content(agent_id, memory_id)
        if result is not None:
            return result
        return {"ok": False, "error": "not_found", "memory_id": memory_id}

    def _purge_evidence_refs(self, eid: str) -> None:
        """统一清理所有对象对 eid 的引用（evidence_ids + causal 四边），杜绝悬空。

        排除 eid 自身（其即将被删除，无需清理自身引用）。
        方案A·F1修复：_delete_evidence_by_id / _delete_by_content / dissolve_as 三处删除路径
        统一调用，确保 anchor/memory/evidence 的 evidence_ids 与 causal 四边均被清理。
        """
        for a in self.anchors:
            if eid in a.evidence_ids:
                a.evidence_ids.remove(eid)
            for _k in ("caused_by", "causes", "enables", "contradicts"):
                _lst = (a.causal or {}).get(_k)
                if _lst and eid in _lst:
                    _lst.remove(eid)
        for m in self.memories:
            if eid in m.evidence_ids:
                m.evidence_ids.remove(eid)
            for _k in ("caused_by", "causes", "enables", "contradicts"):
                _lst = (m.causal or {}).get(_k)
                if _lst and eid in _lst:
                    _lst.remove(eid)
        for e in self.evidences:
            if e.id == eid:
                continue
            for _k in ("caused_by", "causes", "enables", "contradicts"):
                _lst = (e.causal or {}).get(_k)
                if _lst and eid in _lst:
                    _lst.remove(eid)

    def _delete_evidence_by_id(self, agent_id: str, evidence_id: str) -> dict | None:
        """按evidence id删除（仅所有者可删）。命中返回结果，未命中返回None。"""
        ev = next((e for e in self.evidences if e.id == evidence_id), None)
        if ev is None:
            return None
        if ev.agent_id != agent_id:
            return {"ok": False, "error": "forbidden: not owner of evidence",
                    "memory_id": evidence_id}
        # 级联清理（方案A·F1统一）：全量移除对该 evidence 的所有引用——
        # anchor/memory/evidence 的 evidence_ids 与 causal 四边，杜绝悬空。
        self._purge_evidence_refs(evidence_id)
        self.evidences.remove(ev)
        return {"ok": True, "deleted": "evidence", "memory_id": evidence_id}

    def _delete_memory_by_id(self, agent_id: str, memory_id: str) -> dict | None:
        """按memory id删除（private仅贡献者可删，consensus禁止单端删除）。"""
        mem = next((m for m in self.memories if m.id == memory_id), None)
        if mem is None:
            return None
        if mem.scope == "consensus":
            return {"ok": False,
                    "error": "forbidden: consensus memory requires unanimous contributor dissolution, not single-agent delete",
                    "memory_id": memory_id}
        if agent_id not in mem.contributors:
            return {"ok": False, "error": "forbidden: not a contributor of this private memory",
                    "memory_id": memory_id}
        self.memories.remove(mem)
        return {"ok": True, "deleted": "memory", "memory_id": memory_id}

    def _delete_by_content(self, agent_id: str, memory_id: str) -> dict | None:
        """按content兜底删除（仅删调用者贡献的private+本人evidence，命中consensus整体拒绝）。"""
        mems = [m for m in self.memories if m.content == memory_id]
        if not mems:
            return None
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
            self._purge_evidence_refs(e.id)
            self.evidences.remove(e)
        return {"ok": True, "deleted": "memory+evidence(owned only)", "memory_id": memory_id,
                "memories": len(owned), "evidences": len(to_del)}

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
                self._purge_evidence_refs(e.id)
                self.evidences.remove(e)
            votes.pop(memory_id, None)
            _save_dissolve_votes(votes_root, votes)
            return {"ok": True, "dissolved": True, "memory_id": memory_id,
                    "evidences": len(to_del), "voters": sorted(cast)}
        votes[memory_id] = sorted(cast)
        _save_dissolve_votes(votes_root, votes)
        return {"ok": True, "dissolved": False, "memory_id": memory_id,
                "voted": sorted(cast), "remaining": sorted(need - cast)}
