"""液环召回相关性过滤（蒸馏自 RobSelf arXiv:2602.18822，落地 recall 过滤）

RobSelf 两大组件：
  ① 错位感知特征转换器：局部窗口搜索吸收空间错位（无需预对齐）
  ② 内容感知参考滤波器：按内容相似度保留/抑制参考（去噪）
蒸馏到液环 recall：在 jaccard 主相关性之外，加一层「内容感知 + 错位容忍」的
辅助相关性打分，并可选抑制近零权重噪声候选。

  - content_aware_weight：token 词频向量的余弦相似度（分布级相关性）。
    比 jaccard 更能识别「只共享个别稀有 token 的噪声候选」→ RobSelf 去噪。
  - aligned_score：错位容忍对齐分 = max(jaccard, 含容度(q⊆c), 含容度(c⊆q))。
    吸收查询与记忆间的措辞/语序错位（paraphrase / 子集包含），即 RobSelf ①。

零爆破半径设计：
  - 默认仅增强（给每条候选附加两个 metadata 字段），绝不重排 recall 顺序。
  - 仅当调用方显式给 θ∈(0,1) 时才抑制 content_aware_weight<θ 的噪声候选。
  - fail-open：异常由 caller 兜底（返回原样）。

零依赖：仅 Python 标准库（math, re, collections）。
"""
from __future__ import annotations

import math
import re
from collections import Counter

# 与 server 同源的 token 切分
_TOKEN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def _tokens(s: str) -> list:
    return _TOKEN.findall(s or "")


def _vec(s: str) -> dict:
    """字符串 → 词频分布向量 {token: freq}。空串返回 {}。"""
    toks = _tokens(s)
    if not toks:
        return {}
    n = len(toks)
    c = Counter(toks)
    return {t: v / n for t, v in c.items()}


def cosine_dict(a: dict, b: dict) -> float:
    """两分布向量的余弦相似度（内容感知打分核心）。"""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _jaccard_sets(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _containment_sets(a: set, b: set) -> float:
    """重叠系数 |A∩B| / min(|A|,|B|)：对子集包含（错位/部分匹配）鲁棒。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def content_aware_filter(ref_vec: dict, tgt_vec: dict, theta: float):
    """内容感知参考滤波器（RobSelf ② 落地）：
       计算参考与目标的内容相似度；≥θ 保留权重1，<θ 软抑制（权重=sim/θ）。
       返回 (weight, sim)。不变量：抑制噪声/不匹配区域，保留有用内容。"""
    sim = cosine_dict(ref_vec, tgt_vec)
    weight = 1.0 if sim >= theta else max(sim / theta, 0.0) if theta > 0 else sim
    return weight, sim


def recall_content_filter(query: str, candidates: list, theta: float = 0.0,
                          window: int = 3) -> tuple:
    """对 recall 候选做内容感知 + 错位容忍增强（RobSelf 蒸馏落地）。

    参数：
        query     — 查询串
        candidates— recall 返回的候选 dict 列表（需含 "content"）
        theta     — 噪声抑制阈值（0=仅增强不抑制；0<θ<1=抑制 weight<θ 的候选）
        window    — 错位容忍窗口（保留接口，当前对齐用含容度吸收错位）
    返回 (过滤后候选列表, report)。
      · 每条保留候选附加 content_aware_weight / aligned_score 两字段
      · theta>0 时丢弃 content_aware_weight<θ 的噪声候选（RobSelf 去噪）
    不变量：绝不重排（保留原 jaccard score 作为主排序）；fail-open 由 caller 包裹。
    """
    q_set = set(_tokens(query))
    q_vec = _vec(query)
    kept = []
    suppressed = 0
    for r in candidates:
        content = r.get("content", "") or ""
        c_set = set(_tokens(content))
        c_vec = _vec(content)

        # ① 内容感知权重（RobSelf ②）
        if theta > 0:
            weight, _ = content_aware_filter(c_vec, q_vec, theta)
        else:
            weight = cosine_dict(c_vec, q_vec)  # 纯相似度，仅展示

        # ② 错位容忍对齐分（RobSelf ①）：吸收措辞/语序/子集包含错位
        jac = _jaccard_sets(q_set, c_set)
        cont_qc = _containment_sets(q_set, c_set)  # query 是候选的子集
        cont_cq = _containment_sets(c_set, q_set)  # 候选是 query 的子集
        aligned = max(jac, cont_qc, cont_cq)

        if theta > 0 and weight < theta:
            suppressed += 1
            continue  # 噪声候选：内容感知低相似 → 抑制（不进入召回）

        new_r = dict(r)  # 复制，避免污染原 dict
        new_r["content_aware_weight"] = round(weight, 4)
        new_r["aligned_score"] = round(aligned, 4)
        kept.append(new_r)

    rep = {"enabled": True, "theta": theta, "n_in": len(candidates),
           "n_out": len(kept), "suppressed": suppressed}
    return kept, rep
