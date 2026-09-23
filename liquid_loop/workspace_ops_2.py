"""workspace_ops_2 — WorkspaceState Mixin②：记忆/证据运营 + 分析与撤销（零改写搬运）。"""
from __future__ import annotations

from .textutil import (
    now,
    uid,
)


from datetime import datetime
from typing import TYPE_CHECKING, Any
from collections.abc import Callable
import sys
import re
from collections import defaultdict







from .workspace_models import (
    Anchor,
    Evidence,
    Memory,
)


class WorkspaceOps2:
    if TYPE_CHECKING:
        # ── 跨分片成员声明（mypy 专用，见 workspace_ops_1 同款注释）──
        anchors: list[Anchor]
        evidences: list[Evidence]
        memories: list[Memory]
        _iteration: int
        updated_at: str
        _recalc_anchor: Callable[..., Any]   # workspace_ops_1

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

    def _update_memory_stability(self, anchor_id: str, group: list | None = None):
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

    def step(self, dt: int = 1, decay_rate: float = 0.05) -> None:
        """显式时间动力学步（v0.8 液态循环核心，v2.1加错误快照保护）：

            M(t+1) = M(t) + reinforcement − decay − contradiction_penalty

        - 每条证据按 (1−decay_rate)^dt 衰减权重（无强化则价值流失，下限 0.05）
        - 每个 memory 按 support/contradiction 计数重算固有稳定性（矛盾惩罚持续作用）；
          若本轮有 support 证据 → 稳定性恢复到固有值（强化）；否则按时间衰减（无强化流失）
        - 重算锚点稳定性与审计更新时间
        - v2.1: try/except快照保护，异常记录到self._step_errors不静默崩溃
        """
        if not hasattr(self, '_step_errors'):
            self._step_errors = []
        try:
            self._step_impl(dt, decay_rate)
        except Exception as e:
            import traceback
            err = f"step()异常 dt={dt} decay={decay_rate}: {e}\n{traceback.format_exc()[-500:]}"
            self._step_errors.append(err)
            print(f"⚠️  [step] {err.splitlines()[0]}", file=sys.stderr)
            # 异常后不re-raise，防止训练循环中断；错误已记录可追溯

    def _step_impl(self, dt: int = 1, decay_rate: float = 0.05) -> None:
        """step()核心逻辑（被try/except包裹）"""
        prev_iter = self._iteration  # 上次 step 的迭代序号（τ 时间变量，非墙钟）
        # v2.1 抗衰预计算：被memory引用的证据ID集 + 锚点稳定性映射
        referenced_ids = set()
        for m in self.memories:
            eids = getattr(m, 'evidence_ids', None)
            if eids:
                referenced_ids.update(eids)
        anchor_stability = {a.id: getattr(a, 'stability', 0.5) for a in self.anchors}
        # 1. 证据权重时间衰减（v2.1 差异化衰减率：抗衰机制）
        for e in self.evidences:
            if not e.archived:
                anti_aging = 1.0
                if getattr(e, 'recall_hits', 0) >= 2:
                    anti_aging *= 0.4  # 被反复召回=抗衰
                if e.id in referenced_ids:
                    anti_aging *= 0.6  # 被memory引用=核心知识=抗衰
                a_stab = anchor_stability.get(getattr(e, 'anchor_id', ''), 0.5)
                if a_stab > 0.7:
                    anti_aging *= 0.8  # 高稳定锚点=保护
                effective_decay = min(decay_rate * anti_aging, 0.1)  # 上限防过度
                floor = 0.1 if e.id in referenced_ids else 0.05  # 被引用证据下限更高
                e.weight = max(e.weight * ((1 - effective_decay) ** dt), floor)
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
    # ── v2.1 多巴胺机制：正向增益(有用召回) + 负向衰减(无效召回) + 新颖性奖励 + 饱和曲线 ──
    def register_recall(self, evidence_ids, used: bool = True):
        """记录证据被召回命中 → 多巴胺信号（正向增益/负向衰减）。

        #115 实证：人类面对多选项系统性低估中间项，但注意力（多看几眼）像增益旋钮
        因果重塑主观价值（关键窗口在奖励揭晓前）。

        v2.1 多巴胺机制：
        - 正向多巴胺(used=True)：召回后被实际使用→黏滞升稳+stability增益
        - 负向多巴胺(used=False)：召回后未被使用→轻微衰减+miss_hits累计
        - 新颖性奖励：低权重证据被召回且使用→额外增益（预测误差，超出预期的有用）
        - 饱和曲线：dopamine_hits越高，单次增益越小（避免单条证据垄断）

        守关键窗口（证据吸收须在 crystallization 前）：仅对未归档证据升权；
        已归档(archived)证据不升（零丢失，仅降权不增权）。
        """
        ATTENTION_GAIN_K = 0.15  # 黏滞系数：每次召回把 weight 向 1.0 渐进逼近
        DOPAMINE_POSITIVE = 0.08  # 正向多巴胺基础增益
        DOPAMINE_NEGATIVE = 0.95  # 负向多巴胺衰减系数
        NOVELTY_BONUS = 1.5  # 低权重证据的新颖性奖励倍数
        ids = set(evidence_ids) if evidence_ids else set()
        for e in self.evidences:
            if e.id in ids and not e.archived and not e.superseded_by:
                e.recall_hits += 1
                e.last_recall_at = now()  # 记录召回时间，供 lifecycle 老化判据
                # P1: CPE演化通道——观察期内的证据召回计数，用于转正判定
                if getattr(e, 'evolve_status', '') == "observing":
                    if not hasattr(e, 'evolve_recall_hits'):
                        e.evolve_recall_hits = 0
                    e.evolve_recall_hits += 1
                if used:
                    # 正向多巴胺：黏滞升稳（gaze 增益饱和曲线）
                    e.weight = min(1.0, e.weight + ATTENTION_GAIN_K * (1.0 - e.weight))
                    # 多巴胺增益：低权重证据获得额外增益（新颖性奖励=预测误差）
                    if not hasattr(e, 'dopamine_hits'):
                        e.dopamine_hits = 0
                    novelty = NOVELTY_BONUS if e.weight < 0.3 else 1.0
                    saturation = max(0.3, 1.0 - e.dopamine_hits * 0.05)  # 饱和曲线
                    gain = DOPAMINE_POSITIVE * novelty * saturation
                    e.weight = min(1.0, e.weight + gain * (1.0 - e.weight))
                    e.dopamine_hits += 1
                else:
                    # 负向多巴胺：轻微衰减+miss累计（连续无效=加速遗忘）
                    if not hasattr(e, 'miss_hits'):
                        e.miss_hits = 0
                    e.weight = max(0.01, e.weight * DOPAMINE_NEGATIVE)
                    e.miss_hits += 1

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
                "levels": levels, "total_affected": sum(len(lvl) for lvl in levels)}

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
