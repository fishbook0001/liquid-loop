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


def _token_distribution_similarity(a: str, b: str) -> float:
    """d798交叉熵蒸馏：词分布相似度（KL散度思想的零向量近似）。

    KL散度 D_KL(P||Q) = Σ p_i log(p_i/q_i) 衡量两个分布的距离。
    这里用token频率分布的重叠度近似：高频词重叠越多，分布越相似。
    零依赖、确定性、与现有评分同量级。

    注意：_entity_tokens把中文切成单字（长度1），所以用长度>=2的token
    （英文词/连续数字）作为分布特征；纯中文文本退化为0，由其他维度补偿。
    """
    ta = _entity_tokens(a)
    tb = _entity_tokens(b)
    if not ta or not tb:
        return 0.0
    # 长度>=2的token（英文词/连续数字）作为分布特征
    long_ta = {t for t in ta if len(t) >= 2}
    long_tb = {t for t in tb if len(t) >= 2}
    if not long_ta or not long_tb:
        # 纯中文文本：用全部token的Jaccard作为退化分布相似度
        union = ta | tb
        if not union:
            return 0.0
        return len(ta & tb) / len(union)
    # 分布重叠 = 交集 / 较小集合（类比KL散度的反向：重叠大则距离小）
    overlap = len(long_ta & long_tb)
    smaller = min(len(long_ta), len(long_tb))
    return overlap / smaller if smaller > 0 else 0.0


def compute_match_score(evidence_content: str, memory_content: str,
                        evidence_anchor_id: str = "",
                        memory_evidence_anchor_ids: set | None = None,
                        evidence_principle: str = "",
                        memory_principle: str = "") -> float:
    """计算evidence与Memory的匹配得分（0~1）。

    权重：
    - 关键词重叠度：0.2
    - Jaccard相似度：0.15
    - 词分布相似度（d798 KL散度蒸馏）：0.15
    - 同锚点加成：0.2
    - 标签前缀匹配：0.15
    - 原理匹配加成：0.15
    """
    kw = _keyword_overlap(evidence_content, memory_content)
    jac = _jaccard(evidence_content, memory_content)
    dist_sim = _token_distribution_similarity(evidence_content, memory_content)
    anchor_bonus = 0.2 if _anchor_match(evidence_anchor_id, memory_evidence_anchor_ids or set()) else 0.0
    tag_bonus = 0.15 * _tag_prefix_match(evidence_content, memory_content)
    principle_bonus = 0.15 if _principle_match(evidence_principle, memory_principle) else 0.0
    score = 0.2 * kw + 0.15 * jac + 0.15 * dist_sim + anchor_bonus + tag_bonus + principle_bonus
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

    # 3. stability增稳（d798交叉熵蒸馏：预测误差驱动）
    # 匹配分数高=锚点对新证据的预测准确=增稳多；
    # 匹配分数低但仍匹配=预测误差大=增稳少（类比KL散度：分布距离大则信息量小）
    if match_score >= 0.7:
        stability_gain = min(0.05 + match_score * 0.1, 0.15)  # 预测准确，正常增稳
    elif match_score >= 0.5:
        stability_gain = min(0.03 + match_score * 0.05, 0.08)  # 预测有误差，增稳减半
    else:
        stability_gain = 0.02  # 预测误差大，微增稳（仍匹配说明有相关性）
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


# 千脑智能理论蒸馏（D788, 2026-09-25）：主动验证机制
# 皮质柱不是被动接收感觉，而是主动运动探索来验证假设。
# 液环对应：证据匹配失败但分数接近阈值时，不被动等待，而是标记为"待主动验证"，
# 记录验证方向，后续证据优先检查待验证项。
ACTIVE_VERIFY_LOW = 0.20   # 主动验证下限：低于此值直接丢弃（太不相关）
ACTIVE_VERIFY_HIGH = 0.35  # 主动验证上限：高于此值正常匹配成核
ACTIVE_VERIFY_TAG = "active_verification_pending"


def _mark_for_active_verification(evidence, best_score: float, best_memory_id: str,
                                   threshold: float) -> dict:
    """千脑理论蒸馏：标记证据为待主动验证（皮质柱主动探索机制）。

    当证据匹配分数在[ACTIVE_VERIFY_LOW, threshold)区间时，不是简单丢弃，
    而是标记为待验证，记录：
    - 最佳匹配的memory_id（验证目标）
    - 当前分数（差距有多大）
    - 验证方向（需要什么样的证据来弥合差距）

    这类似于千脑理论中皮质柱的"主动感觉运动探索"：
    不是被动等待更多证据，而是主动标记验证方向，引导后续证据流入。
    """
    ev_content = getattr(evidence, 'content', '')
    # 提取验证方向：从证据内容中提取关键实体/术语
    entities = _ENTITY_TOKEN.findall(ev_content)
    key_terms = [e for e in entities if len(e) >= 3][:5]

    verification = {
        "status": ACTIVE_VERIFY_TAG,
        "best_match_memory_id": best_memory_id,
        "current_score": round(best_score, 4),
        "threshold": threshold,
        "gap": round(threshold - best_score, 4),
        "verification_direction": key_terms,
        "marked_at": datetime.now().isoformat(),
    }

    # 将验证标记写入evidence的causal字段（液环Evidence的标准扩展字段）
    causal = getattr(evidence, 'causal', None)
    if isinstance(causal, dict):
        causal[ACTIVE_VERIFY_TAG] = verification
    else:
        # 无causal字段时，写入evolve_status作为标记
        if hasattr(evidence, 'evolve_status'):
            evidence.evolve_status = ACTIVE_VERIFY_TAG

    return verification


def _check_active_verifications(state, new_evidence) -> list:
    """千脑理论蒸馏：检查新证据是否能验证之前待验证的证据。

    后续证据写入时，优先检查是否有"待验证"的evidence可以被验证。
    如果新证据与待验证证据的验证方向匹配，则提升待验证证据的匹配分数。

    返回：被验证的evidence列表。
    """
    evidences = getattr(state, 'evidences', [])
    verified = []

    for ev in evidences:
        # 从causal字段读取验证信息（液环Evidence的标准扩展字段）
        causal = getattr(ev, 'causal', {}) or {}
        if not isinstance(causal, dict):
            continue
        verify_info = causal.get(ACTIVE_VERIFY_TAG)
        if not verify_info or verify_info.get("status") != ACTIVE_VERIFY_TAG:
            continue

        # 检查新证据是否包含验证方向中的关键词
        direction = verify_info.get("verification_direction", [])
        new_content = getattr(new_evidence, 'content', '')
        hits = sum(1 for term in direction if term in new_content)

        if hits >= 1:  # 至少命中1个验证方向关键词
            # 验证成功：清除待验证标记
            causal.pop(ACTIVE_VERIFY_TAG, None)
            verified.append({
                "evidence_id": getattr(ev, 'id', 'unknown'),
                "hits": hits,
                "verified_by": getattr(new_evidence, 'id', 'unknown'),
            })

    return verified


# d794落地（2026-09-25）：2条一致证据触发初步结晶
# 设计理念：后训练的"行为选择"——当2条独立evidence内容相似时，
# 即使单条分数未达0.35成核阈值，也应形成初步结晶（低confidence），
# 后续证据流入时再演化强化。类似推理训练中的"初步假设→验证强化"。
PRELIMINARY_CRYSTAL_THRESHOLD = 0.25  # 初步结晶相似度阈值（低于成核阈值0.35）
PRELIMINARY_CRYSTAL_CONFIDENCE = 0.3  # 初步结晶初始confidence（低，待强化）


def _find_similar_orphan_evidence(state, evidence, threshold: float = PRELIMINARY_CRYSTAL_THRESHOLD) -> list:
    """查找与当前evidence相似但未关联任何Memory的独立evidence。

    返回：相似的独立evidence列表（按分数降序）。
    只查找：未被任何Memory关联、非归档、非当前evidence本身。
    """
    evidences = getattr(state, 'evidences', [])
    memories = getattr(state, 'memories', [])

    # 收集已被Memory关联的evidence_id集合
    linked_ev_ids = set()
    for m in memories:
        for eid in getattr(m, 'evidence_ids', []):
            linked_ev_ids.add(eid)

    current_id = getattr(evidence, 'id', '')
    current_content = getattr(evidence, 'content', '')
    current_anchor = getattr(evidence, 'anchor_id', '')
    current_principle = getattr(evidence, 'principle', '')

    similar = []
    for ev in evidences:
        ev_id = getattr(ev, 'id', '')
        # 跳过：当前evidence、已关联Memory、已归档
        if ev_id == current_id or ev_id in linked_ev_ids:
            continue
        if getattr(ev, 'archived', False):
            continue

        score = compute_match_score(
            evidence_content=current_content,
            memory_content=getattr(ev, 'content', ''),
            evidence_anchor_id=current_anchor,
            memory_evidence_anchor_ids={getattr(ev, 'anchor_id', '')},
            evidence_principle=current_principle,
            memory_principle=getattr(ev, 'principle', ''),
        )

        if score >= threshold:
            similar.append((ev, score))

    # 按分数降序
    similar.sort(key=lambda x: x[1], reverse=True)
    return similar


def _create_preliminary_crystal(state, evidence, similar_evidence, match_score: float) -> dict:
    """基于2条一致证据创建初步结晶Memory。

    初步结晶特征：
    - confidence=0.3（低，待后续证据强化）
    - scope=private（同agent）或consensus（跨agent）
    - content=两条evidence融合（第一条为主，第二条追加）
    - 标记causal.preliminary_crystal=True，便于后续审计

    返回：创建结果dict。
    """
    from liquid_loop.workspace_models import Memory

    ev1 = evidence
    ev2 = similar_evidence

    # 确定scope：同agent=private，跨agent=consensus
    agent1 = getattr(ev1, 'agent_id', '')
    agent2 = getattr(ev2, 'agent_id', '')
    scope = "consensus" if (agent1 and agent2 and agent1 != agent2) else "private"

    # 融合content：第一条完整，第二条取前80字追加
    content1 = getattr(ev1, 'content', '')
    content2 = getattr(ev2, 'content', '')
    if content1 and content2:
        fused_content = f"{content1} | +{content2[:80]}{'...' if len(content2) > 80 else ''}"
    else:
        fused_content = content1 or content2

    # 确定principle（取非空的那个）
    principle = getattr(ev1, 'principle', '') or getattr(ev2, 'principle', '')

    # 创建Memory
    memory = Memory(
        content=fused_content,
        confidence=PRELIMINARY_CRYSTAL_CONFIDENCE,
        scope=scope,
        contributors=list({a for a in [agent1, agent2] if a}),
        evidence_ids=[getattr(ev1, 'id', ''), getattr(ev2, 'id', '')],
        support_count=2,
        stability=0.5,  # 初步结晶稳定性较低
        principle=principle,
        principle_grounded=bool(principle),
        tier="fact",
    )

    # 标记为初步结晶
    memory.causal = {
        "preliminary_crystal": True,
        "created_at": datetime.now().isoformat(),
        "match_score": round(match_score, 4),
        "creation_evidence_ids": [getattr(ev1, 'id', ''), getattr(ev2, 'id', '')],
    }

    # 加入state
    if hasattr(state, 'memories'):
        state.memories.append(memory)

    return {
        "memory_id": memory.id,
        "scope": scope,
        "confidence": PRELIMINARY_CRYSTAL_CONFIDENCE,
        "match_score": round(match_score, 4),
        "evidence_ids": [getattr(ev1, 'id', ''), getattr(ev2, 'id', '')],
        "preliminary": True,
    }


def adaptive_crystallize(state, evidence, threshold: float = 0.35) -> dict:
    """自适应结晶入口：新evidence写入后，自动匹配并更新活锚点。

    流程（千脑理论增强，2026-09-25）：
    1. 先检查是否有"待主动验证"的证据可以被新证据验证（皮质柱主动探索）
    2. 寻找最佳匹配Memory
    3. 匹配成功（score >= threshold）→ 更新Memory（活锚点演化）
    4. 匹配失败但分数接近阈值（[0.20, threshold)）→ 标记为待主动验证
       （不是被动等待，而是主动记录验证方向，引导后续证据）
    5. 匹配失败且分数太低（< 0.20）→ 保留为独立evidence

    返回匹配结果dict。
    """
    memories = getattr(state, 'memories', [])
    evidences = getattr(state, 'evidences', [])

    # 千脑理论：先检查待验证证据
    verified_list = _check_active_verifications(state, evidence)

    result = find_best_match(
        evidence_content=getattr(evidence, 'content', ''),
        memories=memories,
        evidence_anchor_id=getattr(evidence, 'anchor_id', ''),
        evidences=evidences,
        evidence_principle=getattr(evidence, 'principle', ''),
        threshold=threshold,
    )

    if result is None:
        # 匹配失败：检查是否有接近阈值的匹配（需要降低阈值重新找最佳）
        near_result = find_best_match(
            evidence_content=getattr(evidence, 'content', ''),
            memories=memories,
            evidence_anchor_id=getattr(evidence, 'anchor_id', ''),
            evidences=evidences,
            evidence_principle=getattr(evidence, 'principle', ''),
            threshold=ACTIVE_VERIFY_LOW,  # 降低阈值找接近匹配
        )

        if near_result is not None:
            near_memory, near_score = near_result
            if ACTIVE_VERIFY_LOW <= near_score < threshold:
                # 千脑理论：标记为待主动验证（皮质柱主动探索）
                verification = _mark_for_active_verification(
                    evidence, near_score, near_memory.id, threshold
                )
                return {
                    "matched": False,
                    "reason": "active_verification_pending",
                    "threshold": threshold,
                    "near_match_score": round(near_score, 4),
                    "near_match_memory_id": near_memory.id,
                    "verification": verification,
                    "verified_prior": verified_list,
                }

        # d794落地：2条一致证据触发初步结晶
        # 当匹配失败时，检查是否有相似的独立evidence，有则创建初步结晶
        similar_orphans = _find_similar_orphan_evidence(state, evidence)
        if similar_orphans:
            best_orphan, best_orphan_score = similar_orphans[0]
            crystal_result = _create_preliminary_crystal(
                state, evidence, best_orphan, best_orphan_score
            )
            return {
                "matched": True,
                "preliminary_crystal": True,
                "reason": "two_evidence_preliminary_crystal",
                "threshold": threshold,
                "orphan_match_score": round(best_orphan_score, 4),
                "verified_prior": verified_list,
                **crystal_result,
            }

        return {
            "matched": False,
            "reason": "no_memory_above_threshold",
            "threshold": threshold,
            "verified_prior": verified_list,
        }

    memory, score = result
    update_result = update_memory_with_evidence(memory, evidence, score)

    return {
        "matched": True,
        "memory_id": memory.id,
        "match_score": score,
        "verified_prior": verified_list,
        **update_result,
    }
