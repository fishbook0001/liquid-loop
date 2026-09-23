"""RAR — Relation-Augmented Retrieval for Liquid Loop (v2, indexed).

零向量 · 纯结构化关系扩展检索 + 时序感知 + 偏好显式索引。

效率核心：
  - 索引 build 一次 (O(N))，建立 token+实体倒排索引 + 邻接表；
  - recall 仅 O(命中实体数)，远低于基线全量 O(N) jaccard 扫描；
  - 进程内缓存 (build_or_cache) 按 state.updated_at 失效，避免每次重建。

接入：
  cands = RARIndex.visible_candidates(state, agent_id)   # 隔离枚举(同 ll_recall)
  idx   = RARIndex.build_or_cache(cands, version, agent_id)
  hits  = idx.recall(query, top_k=5)                      # -> [(id, score, meta), ...]
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime

# ── token / entity extraction ──────────────────────────────
_TOKEN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.I)
# 中文按单字切（与 selfspin 同源字符级：中文单字 + 英数串），虚词/高频字作
# 停用过滤，避免噪声。此前仅 [a-z0-9_]+ → 中文 _tokens 恒空 → 中文检索失效。
_CJK = re.compile(r"[\u4e00-\u9fff]")
_STOP_CJK = set(
    "的了吗呢吧啊哟哦嗯是与我不人都和也就很着没看说自己这那它她什么怎么哪"
)
_NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b")
_ACR = re.compile(r"\b([A-Z]{2,6})\b")
_DATE = re.compile(
    r"\b(\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?|"
    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s*\d{4})?)\b"
)
_NUM = re.compile(r"\b(\d{3,}(?:\.\d+)?[kKmM]?)\b")
_QUOTE = re.compile(r'"([^"]{2,60})"')

_PREF_PAT = re.compile(
    r"\b(i|we|my|the user|user)\b\s+(?:really\s+|also\s+)?"
    r"(?:like|love|prefer|enjoy|hate|dislike|favorite|favourite|fond of|adore|am into|into)\b\s+"
    r"(?:to\s+|eating\s+|drinking\s+|watching\s+|reading\s+|playing\s+|listening to\s+|using\s+|wearing\s+|visiting\s+|going to\s+|the\s+)?"
    r"([A-Za-z][A-Za-z\s'\-]{1,40}?)(?:\.|\,|\band\b|\bbut\b|$)",
    re.I,
)
_PREF_INTENT = re.compile(
    r"\b(like|love|prefer|enjoy|favorite|favourite|hate|dislike|interested in|passionate about)\b", re.I
)
_RECENCY = re.compile(
    r"\b(latest|current|now|recent|recently|today|as of|updated|newest|most recent|these days)\b", re.I
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_MONTH = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?", re.I)

_STOP = set(
    """a an the and or but if then else when while for to of in on at by with from into about over under
    again further once here there all any both each few more most other some such no nor not only own same so than too very
    can will just should now i you he she it we they them his her its their my your our me him us as be been being have has had
    do does did having am are is was were this that these those what which who whom whose where why how one two three four five
    six seven eight nine ten also via per due off up down out after before between during without within get got getting make
    made makes making use used using would could might must shall""".split()  # noqa: SIM905
)

# tokens/entities appearing in more than this many memories are treated as hubs:
# kept in the inverted index (for seed location) but excluded from edge-building (avoid O(deg^2) blow-up)
_HUB_CUTOFF = 300


def _epoch(ts: str) -> float:
    ts = (ts or "").strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(ts, fmt).timestamp()
        except Exception:
            pass
    m = _YEAR.search(ts)
    if m:
        try:
            return datetime(int(m.group()), 1, 1).timestamp()
        except Exception:
            pass
    return 0.0


def _struct_entities(text: str) -> set:
    """结构化强信号实体（专名 / 缩写 / 日期 / 大数 / 引号串）。"""
    es: set = set()
    for m in _NAME.findall(text or ""):
        w = m.strip()
        if len(w) >= 3 and w.lower() not in _STOP:
            es.add("N:" + w.lower())
    for m in _ACR.findall(text or ""):
        es.add("A:" + m.upper())
    for m in _DATE.findall(text or ""):
        es.add("D:" + m.replace("/", "-"))
    for m in _NUM.findall(text or ""):
        es.add("#:" + m)
    for m in _QUOTE.findall(text or ""):
        s = m.strip().lower()
        if s:
            es.add("Q:" + s)
    return es


def _tokens(text: str) -> set:
    out: set = set()
    for t in _TOKEN.findall(text or ""):
        tl = t.lower()
        if _CJK.match(t):                       # 中文单字：保留（过滤虚词）
            if tl not in _STOP_CJK:
                out.add(tl)
        elif len(tl) >= 3 and tl not in _STOP:  # 英文 / 数字：原逻辑
            out.add(tl)
    return out


def _jaccard(a: str, b: str) -> float:
    ta = set(_TOKEN.findall((a or "").lower()))
    tb = set(_TOKEN.findall((b or "").lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _jaccard_cached(qt: set, ct: set) -> float:
    """Jaccard using precomputed token sets (no re-tokenization)."""
    if not qt or not ct:
        return 0.0
    return len(qt & ct) / len(qt | ct)


def _extract_pref(content: str) -> set:
    objs: set = set()
    for m in _PREF_PAT.finditer(content or ""):
        obj = m.group(2).strip().lower()
        words = [w for w in re.sub(r"\s+", " ", obj).split()
                 if w not in _STOP and len(w) >= 2][:2]
        if words:
            objs.add("P:" + " ".join(words))
    return objs


def _category_of(state, anchor_id: str) -> str:
    a = next((x for x in state.anchors if x.id == anchor_id), None)
    return a.name if a else ""


def _pref_tokens(mid: str, idx: RARIndex) -> set:
    out: set = set()
    for p in idx.pref.get(mid, set()):
        out |= _tokens(p)
    return out


class RARIndex:
    def __init__(self):
        self.inv: dict = {}          # token/entity -> set(mid)
        self.adj: dict = {}          # mid -> {nbr: weight}
        self.content: dict = {}      # mid -> content
        self.ts: dict = {}           # mid -> epoch float
        self.struct: dict = {}       # mid -> set(STRUCT-only entities: N:/A:/D:/#/Q:)
        self.cat: dict = {}          # mid -> category
        self.owner: dict = {}        # mid -> owner agent_id (evidence)
        self.scope: dict = {}        # mid -> scope (memory)
        self.contrib: dict = {}      # mid -> contributors (memory)
        self.type: dict = {}         # mid -> type ("evidence"/"memory")
        self.pref: dict = {}         # mid -> set(pref objects)
        self.pref_inv: dict = {}     # pref_object -> set(mid)
        self.ctoks: dict = {}         # mid -> content-only token set (cached for jaccard)
        self.version: str = ""
        self.size: int = 0

    # ── build ──────────────────────────────────────────────
    @classmethod
    def build(cls, cands: list, version: str = "", norm: float = 3.0) -> RARIndex:
        idx = cls()
        idx.version = version
        all_tokens: dict = {}
        for c in cands:
            mid = c["id"]
            content = c.get("content", "")
            idx.content[mid] = content
            idx.cat[mid] = c.get("category", "")
            idx.type[mid] = c.get("type", "")
            idx.ctoks[mid] = _tokens(content)   # cached for O(1) jaccard
            idx.ts[mid] = _epoch(c.get("timestamp", ""))
            idx.owner[mid] = c.get("owner", "")
            idx.scope[mid] = c.get("scope", "")
            idx.contrib[mid] = c.get("contributors", [])
            toks = _tokens(content) | _struct_entities(content)
            all_tokens[mid] = toks
            idx.struct[mid] = {t for t in toks if t[:2] in ("N:", "A:", "D:", "#:", "Q:")}
            for t in toks:
                idx.inv.setdefault(t, set()).add(mid)
            prefs = _extract_pref(content)
            if prefs:
                idx.pref[mid] = prefs
                for p in prefs:
                    idx.pref_inv.setdefault(p, set()).add(mid)
        # NOTE: adjacency is NOT prebuilt (would be O(deg^2) and dominates build
        # cost). Instead, 1-hop propagation is computed DYNAMICALLY inside
        # recall() — O(#seed entities × #neighbours) per query, i.e. O(hit).
        # This is what makes RAR recall faster than the baseline full O(N) scan
        # once the corpus is large/sparse.
        idx.ent = all_tokens   # memory_id -> set(token + struct entities)
        idx.size = len(cands)
        return idx

    # ── recall ─────────────────────────────────────────────
    def recall(self, query: str, top_k: int = 5, displace_thresh: float = 0.15,
               rar_top: int = 20, recency: bool | None = None,
               now: float | None = None, entity_bonus: float = 0.5,
               norm: float = 3.0, prop_budget: int = 40) -> list:
        q_tokens = _tokens(query) | _struct_entities(query)
        pref_intent = bool(_PREF_INTENT.search(query))

        # seeds: memories sharing any query token/entity (O(hit)); this covers
        # EVERY memory with jaccard>0 against the query (shared token ⟺ shared entity).
        seed_set: set = set()
        for t in q_tokens:
            seed_set |= self.inv.get(t, set())
        if pref_intent:
            for _mset in self.pref_inv.values():
                seed_set |= _mset   # pull all preference memories on intent

        # direct = max(char-Jaccard, entity-bonus for query-shared entities)
        # (the entity-bonus term is what lifts multi-session / temporal questions
        #  where the query and gold context share entities but few literal chars)
        direct = {}
        for mid in seed_set:
            j = _jaccard_cached(q_tokens, self.ctoks.get(mid, set()))
            shared = len(self.ent.get(mid, set()) & q_tokens)
            ent_sc = min(1.0, shared * entity_bonus) if shared else 0.0
            direct[mid] = max(j, ent_sc)

        # 1-hop propagation (DYNAMIC — O(hit) recall).
        # Optimisation: only propagate from the TOP-`prop_budget` seeds by direct
        # score. The bridging entity lives in the most query-relevant seeds, so
        # limiting the budget bounds propagation cost (else it is O(N·struct)),
        # while preserving the multi-session / temporal recall gains.
        now_ep = now if now is not None else time.time()
        recency_on = recency if recency is not None else bool(
            _RECENCY.search(query) or _YEAR.search(query) or _MONTH.search(query))
        prop: dict = {}
        prop_seeds = sorted(
            (m for m in seed_set if direct.get(m, 0.0) > 0),
            key=lambda m: direct.get(m, 0.0), reverse=True)[:prop_budget]
        for mid in prop_seeds:
            base = direct[mid]
            for e in self.struct.get(mid, set()):
                inv = self.inv.get(e)
                if not inv or len(inv) > _HUB_CUTOFF:
                    continue   # skip hub tokens (avoid pulling the whole corpus)
                for nb in inv:
                    if nb == mid or nb in direct:
                        continue
                    w = min(1.0, 1.0 / norm)   # single shared-entity edge weight
                    sc = base * 0.15 + w * 0.5
                    if recency_on and self.ts.get(nb, 0) and self.ts.get(mid, 0):
                        age = abs(self.ts[nb] - now_ep)
                        rec = math.exp(-age / (180.0 * 86400.0))   # 180-day decay
                        sc *= (0.5 + 0.5 * rec)
                    if sc > prop.get(nb, 0.0):
                        prop[nb] = sc

        # preference boost: when intent present, give pref memories a base score
        # proportional to overlap between query tokens and their pref objects
        pref_score: dict = {}
        if pref_intent:
            for mid in self.pref:
                ptoks = _pref_tokens(mid, self)
                if not ptoks or not q_tokens:
                    continue
                ov = len(q_tokens & ptoks) / max(1, len(q_tokens))
                pref_score[mid] = 0.1 + 0.4 * ov

        # confidence-gated rerank: protect baseline high-jaccard hits, fill the
        # remaining slots with RAR (seeds + propagated neighbours + pref boosts).
        protected = [mid for mid in direct if direct[mid] >= displace_thresh]
        protected.sort(key=lambda m: direct[m], reverse=True)
        prot_set = set(protected)

        def _score(m):
            return max(direct.get(m, 0.0), prop.get(m, 0.0), pref_score.get(m, 0.0))

        pool = sorted(
            [m for m in direct if m not in prot_set] +
            [m for m in prop if m not in prot_set] +
            [m for m in pref_score if m not in prot_set],
            key=_score, reverse=True)
        ordered = (protected + pool)[:top_k]

        out = []
        for mid in ordered:
            sc = _score(mid)
            meta = {
                "rar_seed": mid in direct,
                "rar_propagated": mid in prop and mid not in direct,
                "pref": mid in self.pref,
                "type": self.type.get(mid, ""),
                "category": self.cat.get(mid, ""),
                "direct": round(direct.get(mid, 0.0), 3),
                "propagated": round(prop.get(mid, 0.0), 3),
                "pref_score": round(pref_score.get(mid, 0.0), 3),
            }
            out.append((mid, round(sc, 3), meta))
        return out

    # ── isolation-aware candidate enumeration (single source of truth) ──
    @staticmethod
    def visible_candidates(state, agent_id: str) -> list:
        """Same visibility/isolation rules as server ll_recall.

        Returns list of dict {id, type, category, content, timestamp, owner, scope, contributors}.
        """
        cands: list = []
        for e in state.evidences:
            if agent_id and e.agent_id != agent_id:
                continue
            if getattr(e, "superseded_by", False) or getattr(e, "archived", False):
                continue  # 08-18 对齐 ll_recall：被取代/归档证据退出召回候选
            cands.append({
                "id": e.id, "type": "evidence",
                "category": _category_of(state, e.anchor_id),
                "content": e.content, "timestamp": e.timestamp,
                "owner": e.agent_id, "scope": "", "contributors": [],
            })
        for m in state.memories:
            if m.scope == "consensus" or (m.scope == "private" and agent_id in m.contributors):
                cands.append({
                    "id": m.id, "type": "memory",
                    "category": "(结晶)", "content": m.content,
                    "timestamp": m.formed_at, "owner": "",
                    "scope": m.scope, "contributors": m.contributors,
                })
        return cands


# ── process-local cache (keyed by agent_id + version) ───────
_CACHE: dict = {}


def build_or_cache(cands: list, version: str, agent_id: str, norm: float = 3.0) -> RARIndex:
    key = (agent_id, version, norm)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    idx = RARIndex.build(cands, version=version, norm=norm)
    _CACHE[key] = idx
    return idx


def invalidate(agent_id: str | None = None, version: str | None = None):
    """Drop cached index(es). Call after /remember with the same agent_id."""
    if agent_id is None:
        _CACHE.clear()
    else:
        for k in list(_CACHE.keys()):
            if k[0] == agent_id and (version is None or k[1] == version):
                _CACHE.pop(k, None)
