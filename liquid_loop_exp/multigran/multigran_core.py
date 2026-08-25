"""v2.0 多粒度记忆检索实验核心（MemGAS 架构蓝图 × 液环结构化底层）。

- 四粒度单元：session 原文 / turn 切分 / summary(首句提取) / keyword(高频词)
- 熵路由：查询×粒度相似度分布 → Shannon 熵 → 逆熵加权（自适应粒度选择）
- 图传播：关联图(结构化相似度>阈值建边) + 迭代传播(PPR 思想)
- 零向量（结构化相似度）、零依赖、隔离命名空间（不碰生产 liquid_loop）
"""
import math
import re
from collections import Counter
from .sim_structured import structured_sim

class MultigranMemory:
    def __init__(self, sim_mode: str = "mix", mem_threshold: float = 0.3,
                 damping: float = 0.1, temp: float = 0.2, num_seednodes: int = 15):
        self.sim_mode = sim_mode
        self.mem_threshold = mem_threshold
        self.damping = damping
        self.temp = temp
        self.num_seednodes = num_seednodes
        self.units = []          # 记忆条目
        self.graph = {}          # {unit_idx: {neighbor_idx: sim}}
        self._dirty = True

    # ── 四粒度构建 ──────────────────────────────────────
    @staticmethod
    def _turn_split(session: list[str]) -> list[str]:
        return [t for t in session if t.strip()]

    @staticmethod
    def _summary_extract(session: list[str], n: int = 2) -> str:
        """结构化摘要：取前 n 句（零 LLM，fail-open 降级）。"""
        return " ".join(session[:n])

    @staticmethod
    def _keywords_extract(session: list[str], top_k: int = 8) -> str:
        """结构化关键词：去停用词后的高频词（零 LLM）。"""
        stops = {"the", "a", "an", "is", "are", "and", "or", "of", "to", "in",
                 "on", "for", "with", "as", "at", "by", "我", "的", "了", "是",
                 "在", "有", "和", "就", "不", "都", "这", "也", "很"}
        words = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", " ".join(session).lower())
        cnt = Counter(w for w in words if w and w not in stops)
        return " ".join(w for w, _ in cnt.most_common(top_k))

    def add(self, session: list[str], metadata: dict | None = None) -> int:
        if not session:
            raise ValueError("session 不能为空")
        turns = self._turn_split(session)
        entry = {
            "session": session,                     # 粒度0（原始列表）
            "session_text": " ".join(session),      # 粒度0 文本化（用于结构化相似度）
            "turn": turns,                          # 粒度1（列表）
            "summary": self._summary_extract(turns),
            "keyword": self._keywords_extract(turns),
            "metadata": metadata or {},
        }
        self.units.append(entry)
        self._dirty = True
        return len(self.units) - 1

    # ── 关联图构建（结构化相似度 > 阈值建边）────────────
    def _rebuild_graph(self):
        self.graph = {i: {} for i in range(len(self.units))}
        n = len(self.units)
        for i in range(n):
            for j in range(i + 1, n):
                s = self._unit_sim(i, j)
                if s >= self.mem_threshold:
                    self.graph[i][j] = s
                    self.graph[j][i] = s
        self._dirty = False

    def _unit_sim(self, i: int, j: int) -> float:
        """条目间相似度 = 各粒度相似度均值（结构化）。"""
        ei, ej = self.units[i], self.units[j]
        return (structured_sim(ei["session_text"], ej["session_text"], self.sim_mode)
                + structured_sim(ei["summary"], ej["summary"], self.sim_mode)
                + structured_sim(ei["keyword"], ej["keyword"], self.sim_mode)) / 3

    # ── 熵路由（自适应粒度选择）─────────────────────────
    def _entropy(self, probs: list[float]) -> float:
        ps = [p for p in probs if p > 0]
        if not ps:
            return 0.0
        return -sum(p * math.log(p) for p in ps)

    def _granularity_weights(self, query: str, cand_k: int = 8) -> dict[str, float]:
        """候选集上的熵路由（修复：全集分布均匀趋零→逆熵失效）。

        先以 session 粒度粗筛 top-k 为候选集，再在候选内算各粒度相似度分布熵。
        短查询下候选集分布有区分度，低熵(高置信)粒度权重大。"""
        if len(self.units) <= cand_k:
            cand = list(range(len(self.units)))
        else:
            sims0 = [structured_sim(query, u["session_text"], self.sim_mode)
                     for u in self.units]
            cand = sorted(range(len(self.units)), key=lambda i: -sims0[i])[:cand_k]
        weights = {}
        for g in ("session", "summary", "keyword"):
            sims = [structured_sim(query, self.units[i]["session_text"] if g == "session"
                                   else self.units[i][g], self.sim_mode) for i in cand]
            total = sum(sims) or 1e-9
            probs = [s / total for s in sims]
            H = self._entropy(probs)
            weights[g] = 1.0 / (1.0 + H)
        exp = {g: math.exp(w / max(self.temp, 1e-9)) for g, w in weights.items()}
        total = sum(exp.values()) or 1e-9
        return {g: e / total for g, e in exp.items()}

    # ── 图传播（PPR 思想）───────────────────────────────
    def _propagate(self, seeds: dict[int, float], iters: int = 10) -> dict[int, float]:
        self._rebuild_graph_if_needed()
        scores = dict(seeds)
        for _ in range(iters):
            new = {k: (1 - self.damping) * v for k, v in seeds.items()}
            for idx, sc in scores.items():
                for nb, edge in self.graph.get(idx, {}).items():
                    new[nb] = new.get(nb, 0) + self.damping * sc * edge
            scores = new
        return scores

    # ── 多路召回 + RRF 融合（经典无参数融合，零向量）──────
    def _retrieve_rrf(self, query: str, topk: int = 3, K: int = 60) -> list[dict]:
        """三粒度独立召回 top-n → RRF(倒数排名融合) → 图传播加权。

        熵路由在结构化底层的局限（三粒度分布趋同）→ 换多路召回融合：
        keyword 粒度召回单粒度漏掉的、session 召回 keyword 漏掉的，互补合并。
        """
        agg: dict[int, float] = {}
        granularity_used = {}
        for g in ("session", "summary", "keyword"):
            sims = [(i, structured_sim(query, u["session_text"] if g == "session"
                                       else u[g], self.sim_mode))
                    for i, u in enumerate(self.units)]
            ranked = [i for i, s in sorted(sims, key=lambda x: -x[1])
                      [:self.num_seednodes] if s > 0]
            for rank, idx in enumerate(ranked):
                agg[idx] = agg.get(idx, 0) + 1.0 / (K + rank + 1)
                granularity_used.setdefault(idx, set()).add(g)
        # 图传播放大候选关联
        if self.graph:
            seeds = {i: sc for i, sc in agg.items()}
            scores = self._propagate(seeds)
        else:
            scores = agg
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:topk]
        return [{"idx": idx, "score": round(sc, 4),
                 "summary": self.units[idx]["summary"][:80],
                 "via": sorted(granularity_used.get(idx, []))}
                for idx, sc in ranked if sc > 0]

    # ── 检索入口 ────────────────────────────────────────
    def retrieve(self, query: str, topk: int = 3, fusion: str = "rrf") -> list[dict]:
        if not self.units:
            return []
        if fusion == "rrf":
            return self._retrieve_rrf(query, topk=topk)
        self._rebuild_graph_if_needed()
        w = self._granularity_weights(query)
        # 种子：各粒度相似度加权求和取 Top-num_seednodes
        seeds = {}
        for idx, u in enumerate(self.units):
            seed = (w["session"] * structured_sim(query, u["session_text"], self.sim_mode)
                    + w["summary"] * structured_sim(query, u["summary"], self.sim_mode)
                    + w["keyword"] * structured_sim(query, u["keyword"], self.sim_mode))
            seeds[idx] = seed
        top_seeds = {k: v for k, v in sorted(seeds.items(), key=lambda x: -x[1])
                     [:self.num_seednodes] if v > 0}
        scores = self._propagate(top_seeds)
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:topk]
        return [{"idx": idx, "score": round(sc, 4),
                 "summary": self.units[idx]["summary"][:80],
                 "g_weights": {k: round(v, 3) for k, v in w.items()}}
                for idx, sc in ranked if sc > 0]

    def _rebuild_graph_if_needed(self):
        if self._dirty:
            self._rebuild_graph()
