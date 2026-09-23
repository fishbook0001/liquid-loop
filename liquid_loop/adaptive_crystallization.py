"""自适应结晶匹配模块：记忆结晶作为活锚点，自动吸引相关证据并演化。

设计理念：
- 记忆结晶不是死物，而是活锚点——能主动吸引相关证据，随证据流入而演化
- 新evidence写入时，自动匹配到语义最相关的Memory，更新结晶内容
- 字符级相似度计算（禁向量），守液环零向量公理

匹配算法：
1. 关键词重叠度（数字串+长度≥4词的专名/术语）
2. Jaccard相似度（token集合）
3. 同锚点加成（evidence所属anchor与Memory关联evidence的anchor一致）
4. 原理匹配加成（principle字段相同）

活锚点行为：
- 匹配成功 → evidence_id加入Memory.evidence_ids
- 结晶内容加权融合更新（旧内容权重随证据数衰减）
- stability/support_count/last_reinforced同步更新
- 防过度匹配：每evidence最多匹配1个Memory，每Memory证据上限50条
"""
import re
import hashlib
from datetime import datetime


# 关键词提取（与selfspin实体boost同构，零向量纯标量）
_ENTITY_KEY_NUM = re.compile(r"[0-9]+")
_ENTITY_TOKEN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")

# D编号提取（军师调研 D570 / [D593] / 标签·D590 等主题标识）
_D_ID_RE = re.compile(r"\bD\d{2,}\b")

# 顶层scope标签：只标识证据域，不标识内容主题，标签匹配时跳过
_SCOPE_TAG_RE = re.compile(r"^(research|fix|active_question|junshi_distill)$")


def _extract_d_ids(s: str) -> set:
    """提取内容中的D编号集合（如 {'D570','D593'}）；无编号返回空集。"""
    return set(_D_ID_RE.findall(s or ""))


def _subject_drift_penalty(evidence_content: str, memory_content: str) -> float:
    """主题漂移惩罚：evidence与Memory均含D编号但无交集 → 衰减到30%。

    作用：阻断跨主题证据被吸进不相关结晶（D570证据不应匹配含D571的结晶）。
    一方无编号或编号有交集时不做惩罚（有交集说明同主题延续）。
    """
    ev_ids = _extract_d_ids(evidence_content)
    mem_ids = _extract_d_ids(memory_content)
    if not ev_ids or not mem_ids:
        return 1.0
    if ev_ids & mem_ids:
        return 1.0
    return 0.3


def _entity_tokens(s: str) -> set:
    """提取关键token：数字串 + 长度≥4的词（专名/术语/度量）。"""
    return set(_ENTITY_TOKEN.findall(s or ""))


def _keyword_overlap(a: str, b: str) -> float:
    """关键词重叠度：命中数 / 查询关键词数（0~1）。"""
    ka = _entity_tokens(a)
    kb = _entity_tokens(b)
    if not ka:
        return 0.0
    return len(ka & kb) / len(ka)


def _jaccard(a: str, b: str) -> float:
    """Jaccard相似度：token集合交集/并集（0~1）。"""
    ta = _entity_tokens(a)
    tb = _entity_tokens(b)
    if not ta or not tb:
        return 0.0
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def _anchor_match(evidence_anchor_id: str, memory_evidence_anchor_ids: set) -> bool:
    """同锚点匹配：evidence所属anchor是否在Memory关联evidence的anchor集合中。"""
    return evidence_anchor_id in memory_evidence_anchor_ids


def _principle_match(evidence_principle: str, memory_principle: str) -> bool:
    """原理匹配：principle字段相同（非空时）。"""
    if not evidence_principle or not memory_principle:
        return False
    return evidence_principle.strip() == memory_principle.strip()


def _tag_prefix_match(a: str, b: str) -> float:
    """标签前缀匹配：提取内容中的[标签·xxx]格式前缀，计算匹配度。

    如"[军师调研硬规则 v1.0] ..."和"[军师调研硬规则 v2.0] ..."前缀高度匹配。
    返回0~1的匹配度。
    """
    import re
    tag_pattern = re.compile(r'\[([^\]]+)\]')
    tags_a = tag_pattern.findall(a or "")
    tags_b = tag_pattern.findall(b or "")
    if not tags_a or not tags_b:
        return 0.0
    # 跳过顶层scope标签（research/fix/active_question等），取第一个内容标签
    def _first_content_tag(tags):
        for t in tags:
            if not _SCOPE_TAG_RE.match(t.strip()):
                return t
        return tags[0]
    ta = _first_content_tag(tags_a)
    tb = _first_content_tag(tags_b)
    # D编号不同 → 内容标签不同主题，直接0分（D570 vs D571）
    _da = _extract_d_ids(ta)
    _db = _extract_d_ids(tb)
    if _da and _db and _da != _db:
        return 0.0
    # 去掉版本号后缀（v1.0/v2.0等）后比较；负向后顾防止误剥D570中的570
    ta_core = re.sub(r'(?<![A-Za-z0-9])v?\d+(\.\d+)*\s*$', '', ta).strip()
    tb_core = re.sub(r'(?<![A-Za-z0-9])v?\d+(\.\d+)*\s*$', '', tb).strip()
    if ta_core == tb_core:
        return 1.0
    # 部分匹配：计算前缀的Jaccard
    ta_tokens = set(ta_core.split())
    tb_tokens = set(tb_core.split())
    if not ta_tokens or not tb_tokens:
        return 0.0
    return len(ta_tokens & tb_tokens) / len(ta_tokens | tb_tokens)


def compute_match_score(evidence_content: str, memory_content: str,
                        evidence_anchor_id: str = "",
                        memory_evidence_anchor_ids: set | None = None,
                        evidence_principle: str = "",
                        memory_principle: str = "") -> float:
    """计算evidence与Memory的匹配得分（0~1）。

    权重：
    - 关键词重叠度：0.25
    - Jaccard相似度：0.2
    - 同锚点加成：0.2
    - 标签前缀匹配：0.2
    - 原理匹配加成：0.15
    """
    kw = _keyword_overlap(evidence_content, memory_content)
    jac = _jaccard(evidence_content, memory_content)
    anchor_bonus = 0.2 if _anchor_match(evidence_anchor_id, memory_evidence_anchor_ids or set()) else 0.0
    tag_bonus = 0.2 * _tag_prefix_match(evidence_content, memory_content)
    principle_bonus = 0.15 if _principle_match(evidence_principle, memory_principle) else 0.0
    score = 0.25 * kw + 0.2 * jac + anchor_bonus + tag_bonus + principle_bonus
    # L2主题漂移惩罚：编号无交集 → 总分衰减，阻断跨主题吸附（D570≠D571）
    score *= _subject_drift_penalty(evidence_content, memory_content)
    return round(min(score, 1.0), 4)


def find_best_match(evidence_content: str, memories: list,
                    evidence_anchor_id: str = "",
                    evidences: list | None = None,
                    evidence_principle: str = "",
                    threshold: float = 0.35) -> tuple[object, float] | None:
    """为新evidence寻找最佳匹配的Memory。

    返回 (memory, score) 或 None（无匹配达到阈值）。
    每个evidence最多匹配到1个Memory（最佳匹配）。
    """
    if not memories:
        return None

    # 预计算每个Memory关联evidence的anchor_id集合
    ev_id_to_anchor = {}
    if evidences:
        for e in evidences:
            ev_id_to_anchor[getattr(e, 'id', '')] = getattr(e, 'anchor_id', '')

    best_memory = None
    best_score = 0.0

    for m in memories:
        # 跳过已归档/已满的Memory
        if len(getattr(m, 'evidence_ids', [])) >= 50:
            continue

        mem_anchor_ids = set()
        for eid in getattr(m, 'evidence_ids', []):
            if eid in ev_id_to_anchor:
                mem_anchor_ids.add(ev_id_to_anchor[eid])

        score = compute_match_score(
            evidence_content=evidence_content,
            memory_content=getattr(m, 'content', ''),
            evidence_anchor_id=evidence_anchor_id,
            memory_evidence_anchor_ids=mem_anchor_ids,
            evidence_principle=evidence_principle,
            memory_principle=getattr(m, 'principle', ''),
        )

        if score > best_score:
            best_score = score
            best_memory = m

    if best_memory and best_score >= threshold:
        return (best_memory, best_score)
    return None


def update_memory_with_evidence(memory, evidence, match_score: float = 0.0) -> dict:
    """将evidence关联到Memory，并更新结晶内容（活锚点演化）。

    更新逻辑：
    1. evidence_id加入memory.evidence_ids（去重）
    2. 结晶内容加权融合：旧内容权重=n/(n+1)，新evidence权重=1/(n+1)
       （n=关联证据数，旧内容随证据数衰减，避免单条证据主导）
    3. stability增稳（match_score越高，增稳越多）
    4. support_count+1
    5. last_reinforced更新为当前时间
    6. contributors去重添加

    返回更新详情dict。
    """
    ev_id = getattr(evidence, 'id', '')
    ev_content = getattr(evidence, 'content', '')
    ev_agent = getattr(evidence, 'agent_id', '')

    # 1. 关联evidence（去重）
    if ev_id not in memory.evidence_ids:
        memory.evidence_ids.append(ev_id)

    n = len(memory.evidence_ids)

    # 2. 结晶内容加权融合（L1同源去重：同D编号同主题重复证据不重复追加内容）
    old_content = memory.content or ""
    # 若旧内容已含与evidence相同的D编号核心，跳过内容追加（仅关联+计数）
    _skip_append = False
    _ev_ids = _extract_d_ids(ev_content)
    _old_ids = _extract_d_ids(old_content)
    if _ev_ids and _old_ids and _ev_ids & _old_ids:
        _skip_append = True
    if old_content and ev_content and not _skip_append:
        # 加权融合：旧内容完整保留（n条证据结晶，权重高），新内容取前80字增量
        # （单条新证据权重低）。文本无法做数值加权，以"保留完整vs截取片段"实现权重差异。
        new_snippet = ev_content[:80] + ("..." if len(ev_content) > 80 else "")
        if n == 1:
            # 第一条关联证据：直接用evidence内容作为结晶核心
            memory.content = ev_content
        else:
            # 后续证据：旧内容保留，新内容追加标注
            memory.content = f"{old_content} | +{new_snippet}"
    elif not old_content:
        memory.content = ev_content

    # 3. stability增稳（match_score越高，增稳越多；上限1.0）
    stability_gain = min(0.05 + match_score * 0.1, 0.15)
    memory.stability = min(getattr(memory, 'stability', 1.0) + stability_gain, 1.0)

    # 4. support_count+1
    memory.support_count = getattr(memory, 'support_count', 0) + 1

    # 5. last_reinforced更新
    memory.last_reinforced = datetime.now().isoformat()
    if hasattr(memory, 'last_reinforced_iter'):
        memory.last_reinforced_iter += 1

    # 6. contributors去重
    if ev_agent and ev_agent not in memory.contributors:
        memory.contributors.append(ev_agent)

    # v2.1 真独立观测验证：关联证据时实时采集观测哈希+时间戳。
    # 哈希由证据链原生字段(id:agent:content)派生，禁止人工回填/事后编造；
    # 时间取证据原生created_at，保证多源观测时间真实分离。
    obs_hashes = getattr(memory, 'observation_hashes', None)
    if obs_hashes is None:
        obs_hashes = {}
        memory.observation_hashes = obs_hashes
    obs_timestamps = getattr(memory, 'observation_timestamps', None)
    if obs_timestamps is None:
        obs_timestamps = {}
        memory.observation_timestamps = obs_timestamps
    if ev_agent:
        obs_hashes[ev_agent] = hashlib.sha256(f'{ev_id}:{ev_agent}:{ev_content}'.encode()).hexdigest()[:16]
        ev_ts = getattr(evidence, 'created_at', None) or getattr(evidence, 'timestamp', None) or datetime.now().isoformat()
        obs_timestamps[ev_agent] = ev_ts

    # 7. confidence更新（随证据数增加）
    memory.confidence = min(len(memory.contributors) / 2.0, 1.0) if memory.scope == "consensus" else min(n / 5.0, 1.0)

    return {
        "memory_id": memory.id,
        "match_score": match_score,
        "evidence_added": ev_id not in memory.evidence_ids,
        "new_evidence_count": n,
        "stability_after": memory.stability,
        "support_count_after": memory.support_count,
    }


def adaptive_crystallize(state, evidence, threshold: float = 0.35) -> dict:
    """自适应结晶入口：新evidence写入后，自动匹配并更新活锚点。

    流程：
    1. 寻找最佳匹配Memory
    2. 匹配成功 → 更新Memory（活锚点演化）
    3. 匹配失败 → 保留为独立evidence，等待更多证据积累后手动/自动成核

    返回匹配结果dict。
    """
    memories = getattr(state, 'memories', [])
    evidences = getattr(state, 'evidences', [])

    result = find_best_match(
        evidence_content=getattr(evidence, 'content', ''),
        memories=memories,
        evidence_anchor_id=getattr(evidence, 'anchor_id', ''),
        evidences=evidences,
        evidence_principle=getattr(evidence, 'principle', ''),
        threshold=threshold,
    )

    if result is None:
        return {
            "matched": False,
            "reason": "no_memory_above_threshold",
            "threshold": threshold,
        }

    memory, score = result
    update_result = update_memory_with_evidence(memory, evidence, score)

    return {
        "matched": True,
        "memory_id": memory.id,
        "match_score": score,
        **update_result,
    }
