"""蒸馏 #4 落地：多键记忆索引 + 邻域扩展（m67 多键记忆索引 蒸馏 · 2026-08-29）。

设计背景（m67 蒸馏结论）：单一召回键在跨域/长尾场景下严重漏召——记忆既按
「主题」组织，也应按「实体/事件/时间」等多键暴露；邻域扩展则用已命中记忆的
关联键反哺检索，摊薄单键漏召风险。

本模块定位（克制原则）：
  - 零依赖纯标准库，纯函数，不 import 液环内部模块 → 可直接被 recall_filter /
    server 侧召回链选用，也便于单测。
  - 不做向量/语义相似度（红线#3：机制层零向量），只做「多键倒排 + 邻域扇出」的
    字符级结构增强，与现有 tfidf/entity 字符级增强同族。
  - 不修改 storage.py 的持久化 schema：索引是运行时结构，按需构建，向后兼容。

用法:
  from liquid_loop.recall_multi_key import MultiKeyIndex
  idx = MultiKeyIndex()
  idx.add(doc_id="m1", keys=["memory/遗忘机制", "entity:DeepTutor", "event:m60"])
  idx.add(doc_id="m2", keys=["memory/蒸馏", "entity:SoMEval", "event:m62"])
  hits = idx.query("蒸馏 DeepTutor", top_k=3, expand_neighbors=True)
"""
from __future__ import annotations

import re

_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
    "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会",
    "着", "没有", "看", "好", "自己", "这", "那", "与", "及", "或", "并",
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is",
    "are", "was", "were", "with", "at", "from", "by", "as", "it", "this",
}


def _tokenize(text: str) -> list[str]:
    """中英混合轻量分词：英文按词、中文按字（字符级 n-gram 交给调用方）。

    中文按字切分是为了与液环现有字符级 jaccard 增强同族，避免引入分词依赖。
    英文转小写去停用词。
    """
    text = (text or "").lower()
    tokens: list[str] = []
    # 英文词
    for w in re.findall(r"[a-z][a-z0-9]*", text):
        if w not in _STOPWORDS:
            tokens.append(w)
    # 中文：按连续汉字串逐字拆
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(list(seg))
    return tokens


class MultiKeyIndex:
    """多键倒排索引 + 邻域扩展。

    键格式约定（冒号前缀分命名空间，与液环实体/事件命名风格对齐）：
      memory/<主题>      —— 主题命名空间
      entity:<名称>      —— 实体命名空间
      event:<名称>       —— 事件命名空间
      time:<YYYY-MM>     —— 时间命名空间（可选）
    非命名空间键按普通词处理。
    """

    def __init__(self):
        self._inverted: dict[str, dict[str, int]] = {}   # 键 → {doc_id: count}
        self._doc_keys: dict[str, set] = {}              # doc_id → {键}
        self._doc_text: dict[str, str] = {}              # doc_id → 原始文本

    @staticmethod
    def _key_terms(key: str) -> set:
        """显式键的检索词展开：全键 + 名称部分（冒号/斜杠后）。

        示例：'memory/遗忘机制' → {'memory/遗忘机制', '遗忘机制'}
              'entity:DeepTutor' → {'entity:deeptutor', 'deeptutor'}
        这样中文整串键可被查询词『蒸馏』命中，同时保留命名空间键的精确匹配能力。
        """
        terms = {key.lower()}
        name = key.split(":", 1)[-1].split("/", 1)[-1].strip()
        if name:
            terms.add(name.lower())
        return terms

    def add(self, doc_id: str, keys: list[str], text: str = "") -> None:
        """注册一条记忆的多键索引。keys 为显式键，text 为原文（参与兜底词检）。"""
        self._doc_keys.setdefault(doc_id, set()).update(keys)
        if text:
            self._doc_text[doc_id] = text
        # 显式键展开后入倒排（全键 + 名称部分）
        for k in keys:
            for term in self._key_terms(k):
                self._inverted.setdefault(term, {})
                self._inverted[term][doc_id] = self._inverted[term].get(doc_id, 0) + 1
        # 文本兜底：把分词后的词也作为隐式键（不污染 _doc_keys）
        for tok in set(_tokenize(text)):
            self._inverted.setdefault(tok, {})
            self._inverted[tok][doc_id] = self._inverted[tok].get(doc_id, 0) + 1

    def _score_docs(self, query_tokens: list[str]) -> dict[str, float]:
        """对查询词打分：命中键/词数加权。"""
        scores: dict[str, float] = {}
        for tok in query_tokens:
            for doc_id, cnt in self._inverted.get(tok, {}).items():
                scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 + 0.5 * (cnt - 1))
        return scores

    def query(self, query: str, top_k: int = 5,
              expand_neighbors: bool = False, neighbor_depth: int = 1) -> list[dict]:
        """多键检索。

        expand_neighbors=True 时做邻域扩展：取初排 TopN 命中者的全部显式键，
        再用这些键扇出更多候选（摊薄单键漏召），扇出文档按「原得分 + 邻域加成」
        排序。返回 [{"doc_id", "score", "matched_keys"}...]。
        """
        tokens = _tokenize(query)
        # 保留原始查询整串（去空白后小写），使『蒸馏』这类中文整串键可被命中；
        # 与 add 端显式键的名称部分展开（_key_terms）对称。
        raw = re.sub(r"\s+", "", (query or "").lower())
        if raw:
            tokens.append(raw)
        scores = self._score_docs(tokens)
        if not scores:
            return []
        if expand_neighbors and neighbor_depth > 0:
            # 邻域扇出：初排 TopN 的显式键作为扩展查询
            seeds = sorted(scores, key=scores.get, reverse=True)[:max(1, top_k)]
            ext_keys: set = set()
            for s in seeds:
                ext_keys.update(self._doc_keys.get(s, set()))
            for k in ext_keys:
                for doc_id, cnt in self._inverted.get(k, {}).items():
                    if doc_id not in scores:
                        scores[doc_id] = 0.0
                    # 邻域加成：扇出键命中给 0.3 弱加成，主查询得分者保持主导
                    scores[doc_id] += 0.3 * (1.0 + 0.5 * (cnt - 1))
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            {
                "doc_id": doc_id,
                "score": round(score, 4),
                "matched_keys": sorted(self._doc_keys.get(doc_id, set())),
            }
            for doc_id, score in ranked
        ]


def build_index(docs: list[dict]) -> MultiKeyIndex:
    """便捷构建：docs=[{"id", "keys": [...], "text": "..."}] → MultiKeyIndex。"""
    idx = MultiKeyIndex()
    for d in docs:
        idx.add(doc_id=d["id"], keys=d.get("keys", []), text=d.get("text", ""))
    return idx
