#!/usr/bin/env python3
"""liquid_core.py — 液环最小内核 (Liquid Loop minimal kernel)

零依赖 · 单文件 · 直接可跑：  python3 liquid_core.py

这是 liquid_loop/workspace.py (1420 行) 的**可读内核提取**，保留全部核心机制：
  1. 精确匹配一致性判定 —— 禁向量硬约束（consistency by exact string, never cosine）
  2. 双轨成核 private / consensus
  3. 反证轨   stability = s / (s + 2c + 1)
  4. 时间动力学 step()：无强化则衰减，有新 support 则恢复固有值
  5. 可解释召回 explain()：任何结论都能回溯到具体 evidence id

完整版额外含（本文件刻意省略）：LEI 九维熵、SHA256 审计链、认知预算稳态、
原理优先成核、因果边、CLI 11 命令、JSON 持久化、MESH 多智能体共识协议。
完整版： pip install liquid-loop  ·  MIT License · 飞哥 (Fei Ge) 2026
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

CONTRADICTION_WEIGHT = 2.0   # 单条反证的降稳效力 ≈ 两条支持 —— 对抗群体幻觉固化
EVIDENCE_DECAY_FLOOR = 0.05  # 证据权重衰减下限（价值流失但不归零）


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Evidence:
    """附着粒子：锚点下的一条具体观察。"""
    anchor: str
    content: str
    agent: str = ""                 # 多智能体写入者；空 = 单实例(legacy)
    relation: str = "support"       # support(一致) | contradiction(冲突)
    target: str = ""                # contradiction 时指向被反驳的 memory id
    weight: float = 1.0
    iter_added: int = 0             # 有效迭代序号 τ（非墙钟，跨算力可比）
    id: str = field(default_factory=_uid)


@dataclass
class Memory:
    """结晶体：2+ 条一致 Evidence 自动凝聚而成，非外部写入。"""
    content: str
    anchor: str
    scope: str = "private"          # private(同 agent≥2) | consensus(跨 agent≥2)
    evidence_ids: list = field(default_factory=list)
    contributors: list = field(default_factory=list)
    confidence: float = 0.0
    stability: float = 1.0
    support: int = 0
    contradiction: int = 0
    last_reinforced_iter: int = 0   # 最近被 support 强化的 τ
    id: str = field(default_factory=_uid)


class LiquidCore:
    """自组织记忆状态：输入证据 → 自动结晶 → 反证降稳 → 时间演化。

    没有任何 "外部编辑器"：不调 LLM 判重、不算 embedding 相似度、不做检索排序。
    一致性判定 = 精确字符串相等。这是硬约束，不是实现偷懒。
    """

    def __init__(self) -> None:
        self.evidences: list[Evidence] = []
        self.memories: list[Memory] = []
        self._iter = 0

    # ── 写入 ────────────────────────────────────────────────────────
    def add(self, anchor: str, content: str, agent: str = "",
            relation: str = "support", target: str = "") -> Evidence:
        """注入一条证据，自动触发：衰减 → 成核 → 稳定性重算。"""
        e = Evidence(anchor=anchor, content=content, agent=agent,
                     relation=relation, target=target, iter_added=self._iter)
        self.evidences.append(e)
        self._decay(anchor)
        self._nucleate(anchor)
        self._update_stability(anchor)
        return e

    def _decay(self, anchor: str) -> None:
        for e in self.evidences:
            if e.anchor == anchor:
                e.weight = max(e.weight * 0.95, EVIDENCE_DECAY_FLOOR)

    # ── 双轨成核 ────────────────────────────────────────────────────
    def _nucleate(self, anchor: str) -> None:
        group = [e for e in self.evidences if e.anchor == anchor]
        if len(group) < 2:
            return
        gids = {e.id for e in group}
        done = {(m.content, m.scope) for m in self.memories
                if gids.intersection(m.evidence_ids)}

        # private 轨：同一 agent 下同一 content 出现 >= 2 次
        by_agent: dict = defaultdict(list)
        for e in group:
            by_agent[e.agent or "legacy"].append(e)
        for owner, evs in by_agent.items():
            for content, n in Counter(e.content for e in evs).items():
                if n >= 2 and (content, "private") not in done:
                    ids = [e.id for e in evs if e.content == content]
                    self.memories.append(Memory(
                        content=content, anchor=anchor, scope="private",
                        evidence_ids=ids, contributors=[owner],
                        confidence=min(n / len(evs), 1.0)))

        # consensus 轨：同一 content 被 >= 2 个不同 agent 支持
        owners: dict = defaultdict(set)
        for e in group:
            if e.agent:
                owners[e.content].add(e.agent)
        for content, ows in owners.items():
            if len(ows) < 2:
                continue
            exist = next((m for m in self.memories
                          if m.scope == "consensus" and m.content == content), None)
            if exist:                                    # 已有共识 → 并入新一致方
                exist.contributors = sorted(set(exist.contributors) | ows)
            elif (content, "consensus") not in done:
                ids = [e.id for e in group if e.content == content and e.agent in ows]
                self.memories.append(Memory(
                    content=content, anchor=anchor, scope="consensus",
                    evidence_ids=ids, contributors=sorted(ows),
                    confidence=min(len(ows) / 2.0, 1.0)))

    # ── 反证轨：一致增稳 / 冲突降稳 ──────────────────────────────────
    def _update_stability(self, anchor: str) -> None:
        group = [e for e in self.evidences if e.anchor == anchor]
        gids = {e.id for e in group}
        for m in [mm for mm in self.memories if gids.intersection(mm.evidence_ids)]:
            sup = [e for e in group if e.relation == "support" and e.content == m.content]
            con = [e for e in group if e.relation == "contradiction"
                   and (e.target == m.id or not e.target)]
            s, c = len(sup), len(con)
            m.support, m.contradiction = s, c
            m.stability = round(s / (s + CONTRADICTION_WEIGHT * c + 1), 3)
            if sup:
                m.last_reinforced_iter = max(e.iter_added for e in sup)

    # ── 时间动力学：M(t+1) = M(t) + reinforce − decay − contradiction ──
    def step(self, dt: int = 1, decay_rate: float = 0.05) -> None:
        prev = self._iter
        for e in self.evidences:
            e.weight = max(e.weight * ((1 - decay_rate) ** dt), EVIDENCE_DECAY_FLOOR)
        for anchor in {e.anchor for e in self.evidences}:
            self._update_stability(anchor)
        for m in self.memories:
            intrinsic = m.stability                       # 计数驱动的固有稳定性
            if m.last_reinforced_iter >= prev:            # 本轮有新 support → 强化
                m.stability = intrinsic
            else:                                         # 无强化 → 时间流失
                m.stability = round(max(0.0, min(intrinsic,
                                    m.stability * ((1 - decay_rate) ** dt))), 3)
        self._iter += dt

    # ── 召回与解释 ──────────────────────────────────────────────────
    def recall(self, anchor: str = "", min_stability: float = 0.0) -> list[Memory]:
        """按 stability 降序返回结晶。无 cosine、无 top-k 打分器、无重排模型。"""
        out = [m for m in self.memories
               if (not anchor or m.anchor == anchor) and m.stability >= min_stability]
        return sorted(out, key=lambda m: (-m.stability, -m.confidence, m.content))

    def explain(self, memory_id: str) -> dict:
        """可解释性：任何结论都能回溯到具体 evidence —— 向量方案给不出这个。"""
        m = next((x for x in self.memories if x.id == memory_id), None)
        if not m:
            return {}
        g = [e for e in self.evidences if e.anchor == m.anchor]
        s, c = m.support, m.contradiction
        intrinsic = round(s / (s + CONTRADICTION_WEIGHT * c + 1), 3)
        return {
            "content": m.content, "scope": m.scope,
            "intrinsic": intrinsic, "stability_now": m.stability,
            "formula": f"{s}/({s}+2×{c}+1)={intrinsic}，经时间衰减后为 {m.stability}",
            "supported_by": [e.id for e in g
                             if e.relation == "support" and e.content == m.content],
            "contradicted_by": [e.id for e in g if e.relation == "contradiction"
                                and (e.target == m.id or not e.target)],
        }


if __name__ == "__main__":
    print("液环最小内核 · 零依赖单文件 demo\n" + "─" * 62)
    s = LiquidCore()

    print("\n[1] 结晶：2 条一致证据自动凝聚（无 LLM 判重、无相似度阈值）")
    s.add("用户偏好", "喜欢红色", agent="a1")
    s.add("用户偏好", "喜欢红色", agent="a1")
    m = s.recall("用户偏好")[0]
    print(f"    → '{m.content}'  stability={m.stability}  scope={m.scope}")

    print("\n[2] 反证轨：1 条冲突 ≈ 抵消 2 条支持（对抗群体幻觉固化）")
    s.add("用户偏好", "喜欢蓝色", agent="a1", relation="contradiction", target=m.id)
    print(f"    → stability 0.667 → {m.stability}（被压制，但证据未被删除）")

    print("\n[3] 时间动力学：无新支持则流失（τ 语义：首步视为期间已被强化）")
    s.step(dt=1)
    before = m.stability
    s.step(dt=10, decay_rate=0.05)
    print(f"    → 此后 step(dt=10): {before} → {m.stability}")

    print("\n[4] 共识轨：跨 agent 一致才升级为 consensus")
    s.add("项目约束", "禁用向量", agent="vera")
    s.add("项目约束", "禁用向量", agent="feige")
    c = [x for x in s.memories if x.scope == "consensus"][0]
    print(f"    → '{c.content}' scope={c.scope} contributors={c.contributors}")

    print("\n[5] 可解释：结论可回溯到具体证据 id（向量方案只能给 cosine 分数）")
    for k, v in s.explain(m.id).items():
        print(f"    {k}: {v}")
