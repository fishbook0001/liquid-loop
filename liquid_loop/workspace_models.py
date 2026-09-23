"""workspace_models — 常量 + 共识守卫 + 7 个核心 dataclass。

2026-09-24 从 workspace.py（原 1912 行）拆分，**内容零改写搬运**。
拆分背景：绝对 LOC 铁律（>600 行 = 黑盒，应拆）。
拆分纪律：薄壳符号集等价（dir() 冻结对比，见 workspace.py 门面）。
"""
from __future__ import annotations

from .textutil import (
    now,
    uid,
    _derive_lifecycle_thresholds,
)


from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import sys
import os







# ========== 共识参与者白名单守卫（2026-09-23 可选，fail-open）==========
_CONSENSUS_PARTY_CACHE = "UNSET"  # "UNSET"→未加载；None→加载失败(允许全部)；set→白名单

def _load_consensus_parties():
    """读取共识参与方白名单（identity_registry.json agents.is_consensus_party=true）。

    唯一事实源 = ~/.liquidloop/memory/.liquid/identity_registry.json（2026-09-23 合并
    distill_registry.json/agents 后统一；原 distill_registry.json/agents 已移除去冗余）。

    返回 set(agent_id) 或 None（文件缺失/不可读 → fail-open：允许全部，不告警不拒绝）。
    结果缓存于模块级；registry 变更需随 8790 进程 reload 生效。
    """
    global _CONSENSUS_PARTY_CACHE
    if _CONSENSUS_PARTY_CACHE != "UNSET":
        return _CONSENSUS_PARTY_CACHE
    p = os.path.expanduser("~/.liquidloop/memory/.liquid/identity_registry.json")
    try:
        with open(p, encoding="utf-8") as _f:
            _data = json.load(_f)
        _agents = _data.get("agents", []) or []
        _parties = {a["agent_id"] for a in _agents if a.get("is_consensus_party")}
        _CONSENSUS_PARTY_CACHE = _parties
        return _parties
    except Exception:
        _CONSENSUS_PARTY_CACHE = None
        return None


# ========== 链式哈希审计（借鉴 KFG MemoryGovernance）==========



# --- 三维锚点分类（密度 / 认知阶段 / 流动性）+ 一维证据质量 ---
DensityLevel = str  # "high" | "medium" | "low"
CognitiveStage = str  # "raw" | "wip" | "crystallized" | "tooling"
LiquidityLevel = str  # "hot" | "warm" | "cold" | "frozen"

# ── v1.7 证据老化回收（督办项警示①落地：append-only 长期会崩）──
# 警示①（上交+清华 Agent-Native Memory 横评）："many append-only stores collapse on long
# horizons as evidence ages" → 液环须主动 lifecycle，而非仅 append。
# 机制：长期未被召回 + 权重冷却到地板 + 非结晶来源 + 非冲突证据 → 冷归档(archived=True)。
# 守铁律：零丢失(archived≠删) / 可审计(archived_at) / 保留时序(timestamp完整) / 禁向量。
#
# ── v1.7.1 理论基点（增益自适应 efficient-coding 映射，2026-08-10 深挖）──
# 论文 Prat-Carrabin et al. (Nature Communications 2026, s41467-026-73032-0)：
#   生物大脑自适应不靠改连接(重连/重训)，靠调增益(gain modulation)，目标函数
#   cost = α·解码准确性 + β·放电成本 —— 单一 objective 统一"prior attraction/adapter
#   repulsion"两个矛盾现象。这与液环"调权重 > 重写证据"同构。
# 液环映射：
#   decoding_accuracy(回忆准确性)  ↔ 保留高权重/近期证据（防误归档丢有用信息）
#   memory_entropy_cost(记忆熵增成本) ↔ 激活集膨胀/检索噪声/存储负担（由老化回收抑制）
#   gain modulation(增益调节)        ↔ evidence.weight 衰减（lifecycle 调权重冷归档，不删结构）
# 双判据推导（见 _derive_lifecycle_thresholds）：
#   floor_weight = β   （熵增成本权重直接作归档地板：越怕熵增越激进清）
#   ttl_eps      = HORIZON（回忆效用半衰期：accuracy 侧容忍上限，与 rar.py 180d decay 对齐）




# 导出常量（沿用现状默认值，向后兼容测试与实战零误冻）
LIFECYCLE_FLOOR_WEIGHT, LIFECYCLE_TTL_EPS = _derive_lifecycle_thresholds()

# ── 锚点生命周期机制补强（飞哥 2026-09-22 拍板·全量深做）──
# 守同一铁律：零丢失(archived≠删) / 可审计(archived_at) / 禁向量 / 向后兼容(默认保守)。
# 默认保守：新锚点 TTL=0(永不过期，向后兼容)；死锚点归档须高陈旧度地板+零证据零访问；
# 所有"写"类 sweep 默认 dry_run=True，须经显式 apply 才落盘，防不可逆误操作。
ANCHOR_STALE_FLOOR = 0.85    # 陈旧度≥此值 且 零证据零访问 → 判定死锚点候选
ANCHOR_SPLIT_CAP = 100       # 单锚点 evidence 超此数 → 触发分裂（原50，提高减少碎片）
ANCHOR_MERGE_SIM = 0.6       # 相似度≥此值 → 相似锚点可合并（禁向量：关键词重叠+Jaccard）
ANCHOR_DEAD_GRACE_DAYS = 30   # 死锚点归档最低年龄门槛(天)：防误冻新建未挂证据的锚点

@dataclass
class Anchor:
    id: str = field(default_factory=uid)
    name: str = ""
    description: str = ""
    created_at: str = field(default_factory=now)
    stability: float = 1.0
    evidence_ids: list[str] = field(default_factory=list)
    value_density: str = "medium"
    cognitive_stage: str = "raw"
    liquidity: str = "warm"
    access_count: int = 0
    last_accessed: str = ""
    value_score: float = 1.0
    anchor_strength: float = 1.0
    seal_adjust: float = 0.0  # SEAL 自评层增量；_recalc 合成进 stability，不被覆盖（解 v0.6.3 假落地）
    conflict_penalty: float = 1.0  # 冲突惩罚累积乘子（Layer-1 修复：持久化，recalc/step/load 后保留）
    # ── v1.3 因果演化循环成核：因果边（符号化，守禁向量）──
    causal: dict = field(default_factory=dict)  # {causes,caused_by,enables,contradicts} -> list[node_id]
    # ── 锚点生命周期机制补强（2026-09-22）──
    ttl_days: float = 0.0            # 锚点寿命(天)；0=永不过期(向后兼容默认)
    expires_at: str = ""             # 成核时若 ttl_days>0 则算；空=无期限
    version_hash: str = ""           # 成核时状态哈希快照（缺3·异步RL old-logits 一致性基准）
    archived: bool = False           # 死锚点冷归档（零丢失，recall/合并/分裂活跃集跳过）
    archived_at: str = ""            # 归档时间戳（可审计）
    superseded_by: str = ""          # 被哪条锚点合并取代（零丢失血缘）
    merged_from: list = field(default_factory=list)  # 合并来源锚点 id 列表（血缘）
    split_from: str = ""             # 分裂父锚点 id（血缘）
    staleness_score: float = 0.0      # 常驻陈旧度 = f(时间,访问,证据质量)（缺4·重构非重写）
    last_probed: str = ""             # 主动探活时间戳（缺2）
    liveness: str = "unknown"         # active / dormant / dead / unknown（缺2）

    def decay_value(self, factor: float = 0.95, evidence_count: int = 0) -> float:
        if not self.created_at:
            return 0.0
        try:
            created = datetime.fromisoformat(self.created_at)
        except ValueError:
            return 0.0
        now_dt = datetime.now(timezone.utc)
        days = (now_dt - created).days
        freq_score = min((evidence_count * 0.12 + self.access_count * 0.08), 1.0)
        time_score = max(0.0, 1.0 - days / 30)
        raw = (freq_score * 0.4 + time_score * 0.6) * (factor ** max(days, 0))
        self.value_score = max(0.0, min(1.0, raw))
        return self.value_score

    def recalc_strength(self, evidence_count: int) -> float:
        evidence_factor = min(evidence_count / 5, 1.0)
        activity = self.value_score
        self.anchor_strength = evidence_factor * 0.6 + activity * 0.4
        self.anchor_strength = max(0.0, min(1.0, self.anchor_strength))
        return self.anchor_strength

    def auto_classify(self, evidence_count: int) -> dict:
        self.decay_value(evidence_count=evidence_count)
        self.recalc_strength(evidence_count)
        if self.anchor_strength > 0.8 and evidence_count >= 3:
            self.value_density = "high"
        elif self.anchor_strength > 0.4 or evidence_count >= 1:
            self.value_density = "medium"
        else:
            self.value_density = "low"
        if self.value_score > 0.7 and self.anchor_strength > 0.7:
            self.cognitive_stage = "crystallized"
        elif self.value_score > 0.4:
            self.cognitive_stage = "wip"
        elif evidence_count == 0:
            self.cognitive_stage = "raw"
        else:
            self.cognitive_stage = "wip"
        if self.last_accessed:
            try:
                last = datetime.fromisoformat(self.last_accessed)
                gap_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
                if gap_hours < 24:
                    self.liquidity = "hot"
                elif gap_hours < 168:
                    self.liquidity = "warm"
                elif gap_hours < 720:
                    self.liquidity = "cold"
                else:
                    self.liquidity = "frozen"
            except ValueError as _e:
                sys.stderr.write(f"[workspace:warn] liquidity日期解析失败: {_e}\n")
        return {"value_density": self.value_density, "cognitive_stage": self.cognitive_stage, "liquidity": self.liquidity}

    # ===== 锚点生命周期机制补强（2026-09-22 飞哥拍板·全量深做） =====
    # 守铁律：零丢失(archived≠删) / 可审计(archived_at) / 禁向量 / 向后兼容(默认保守)。

    def snapshot_version(self) -> str:
        """成核/重大变更时记录锚点状态哈希（缺3·异步RL old-logits 一致性基准）。

        哈希覆盖：name/description/evidence_ids/value_score/anchor_strength/created_at。
        证据采集时把此刻的 version_hash 写入 Evidence.anchor_version_hash；
        后续若锚点被合并/分裂/重算导致哈希漂移，即可检测"证据针对的是旧版本锚点"。
        """
        canon = json.dumps({
            "name": self.name,
            "desc": self.description,
            "ev": sorted(self.evidence_ids),
            "vs": round(self.value_score, 3),
            "st": round(self.anchor_strength, 3),
            "ca": self.created_at,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]

    def recalc_staleness(self, ref_dt=None) -> float:
        """常驻陈旧度评分 = f(时间衰减, 访问频率, 证据质量)（缺4·重构而非重写）。

        复用现有 liquidity/time_score 语义，固化为独立可排序字段：
          时间因子 = 1 - 上次访问距今/180d（无访问→最旧）
          访问因子 = min(access_count/10, 1)
          证据因子 = min(len(evidence_ids)/5, 1)（数量代理质量，禁向量）
          staleness = 1 - (0.5*时间 + 0.3*访问 + 0.2*证据)，夹 [0,1]。
        高=陈旧(死锚点候选)，低=鲜活。
        """
        ref = ref_dt or datetime.now(timezone.utc)
        if self.last_accessed:
            try:
                last = datetime.fromisoformat(self.last_accessed)
                gap_days = (ref - last).total_seconds() / 86400.0
            except ValueError:
                gap_days = 9999.0
        else:
            gap_days = 9999.0
        time_factor = max(0.0, 1.0 - gap_days / 180.0)
        access_factor = min(self.access_count / 10.0, 1.0)
        evidence_factor = min(len(self.evidence_ids) / 5.0, 1.0)
        freshness = 0.5 * time_factor + 0.3 * access_factor + 0.2 * evidence_factor
        self.staleness_score = round(max(0.0, min(1.0, 1.0 - freshness)), 4)
        return self.staleness_score

    def age_days(self, ref_dt=None) -> float:
        """锚点年龄（天）：created_at → ref。空 created_at 视为极老(9999)以便归档。"""
        if not self.created_at:
            return 9999.0
        try:
            c = datetime.fromisoformat(self.created_at)
            if c.tzinfo is None:
                c = c.replace(tzinfo=timezone.utc)
            return max(0.0, (ref_dt or datetime.now(timezone.utc) - c).total_seconds() / 86400.0)
        except ValueError:
            return 9999.0

    def is_expired(self, ref_dt=None) -> bool:
        """锚点是否超 TTL（缺1）。expires_at 空=无期限→永不真。"""
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return exp <= (ref_dt or datetime.now(timezone.utc))
        except ValueError:
            return False

    def probe(self, ref_dt=None) -> str:
        """主动探活（缺2）：刷新 last_probed 并据陈旧度定级 liveness。

        区别于 guard.env_anchor_probe（那是安全门对环境断言 curl/ps 探活，与此无关）。
        本方法仅对记忆锚点自身做活跃度巡检，不触外部系统。
        返回 liveness 等级：active / dormant / dead。
        """
        self.last_probed = (ref_dt or datetime.now(timezone.utc)).isoformat()
        self.recalc_staleness(ref_dt)
        if self.staleness_score >= ANCHOR_STALE_FLOOR:
            self.liveness = "dead"
        elif self.staleness_score >= 0.6:
            self.liveness = "dormant"
        else:
            self.liveness = "active"
        return self.liveness

    def merge_with(self, other: Anchor) -> None:
        """相似锚点聚合合并（缺5·merge）：吸收 other 的证据与血缘，other 冷归档。

        零丢失：other 不删，置 archived + superseded_by=本锚点 + 反向 merged_from，
        可被解冻审计。证据取并集(上限保护)，强度重算。
        Evidence.anchor_id 重指派由 WorkspaceState.merge_similar_anchors 负责。
        """
        if other is self:
            return
        self.evidence_ids = sorted(set(self.evidence_ids) | set(other.evidence_ids))
        self.merged_from.append(other.id)
        self.value_score = round((self.value_score + other.value_score) / 2.0, 4)
        self.recalc_strength(len(self.evidence_ids))
        other.archived = True
        other.archived_at = datetime.now(timezone.utc).isoformat()
        other.superseded_by = self.id
        other.liquidity = "archived"

    def split_partition(self, cap: int = ANCHOR_SPLIT_CAP, evidences: list | None = None) -> list:
        """过大锚点分裂（缺5·split）：按时间排序后顺序切片，保证时间局部性。

        若传入 evidences，先按 timestamp 排序再切片；否则直接按 evidence_ids 顺序切片。
        不直接改状态——由 WorkspaceState.split_oversized_anchors 据返回创建子锚点，
        父锚点保留(置 split_from 血缘)供审计，不物理删。
        """
        ids = list(self.evidence_ids)
        if not ids:
            return []
        if evidences:
            ev_map = {e.id: e for e in evidences}
            ids.sort(key=lambda eid: getattr(ev_map.get(eid), 'timestamp', ''))
        return [ids[i:i + cap] for i in range(0, len(ids), cap)]


@dataclass
class Evidence:
    id: str = field(default_factory=uid)
    anchor_id: str = ""
    anchor_version_hash: str = ""  # 证据采集时所属锚点的 version_hash 快照（缺3·old-logits 一致性对照基准）
    content: str = ""
    timestamp: str = field(default_factory=now)
    weight: float = 1.0
    quality: str = "normal"
    archived: bool = False  # 预算超额冷归档标记（PEEK 落地，零丢失）
    agent_id: str = ""  # 多智能体共用：写入者标识（共用机制，缺省=legacy/单实例）
    relation: str = "support"  # 反证轨：evidence 与 memory 的关系 "support"(一致) | "contradiction"(冲突) | ""(legacy 视为 support)
    target_memory_id: str = ""  # relation=contradiction 时，可选显式指向被反驳的 memory（缺省=同锚点最近结晶）
    added_iter: int = 0  # 证据写入时的有效状态更新序号（τ=Effective Iteration）；强化门控据此判"自上次 step 以来是否有新 support 抵达"
    # ── v1.3 因果演化循环成核：因果边（符号化，守禁向量）──
    causal: dict = field(default_factory=dict)  # {causes,caused_by,enables,contradicts} -> list[node_id]
    # ── v1.4 原理优先成核（MSM 反哺）：证据所例证的「原理/why」──
    principle: str = ""  # 该证据例证的原理陈述（非空→参与原理优先成核；守禁向量=结构化文本非 embedding）
    # ── v1.5 注意力增益（#115 神经科学实证：多看几眼=价值升）──
    recall_hits: int = 0  # 被召回命中次数（gaze 增益信号源；黏滞升稳由 register_recall 驱动）
    last_recall_at: str = ""  # 最近被召回时间戳（老化回收判据源；空=从未召回）
    archived_at: str = ""  # 被 lifecycle 冷归档的时间戳（审计展示用，零丢失）
    # ── v1.8 软取代（supersede）原语：显式语义取代（区别于 lifecycle 自动老化归档）──
    # 与 archived 正交：archived=熵增清理(低权+久未召回)；superseded_by=被另一条更优/更新
    # 证据显式取代(语义冗余)。二者皆零丢失可解冻；active recall 退出活跃，list 全量审计可见。
    superseded_by: str = ""  # 本证据被哪条 winner 证据取代（空=未被取代）
    supersedes: list = field(default_factory=list)  # 本证据取代过的 loser 证据 id 列表（反向指针/血缘）
    superseded_at: str = ""  # 被取代/取代发生的时间戳（审计展示用）
    # ── v2.1 多巴胺机制：正向增益(有用召回) + 负向衰减(无效召回) + 饱和曲线 ──
    dopamine_hits: int = 0  # 正向多巴胺累计次数（有用召回/主动重放的增益计数）
    miss_hits: int = 0  # 负向多巴胺累计次数（无效召回的衰减计数，连续无效=加速遗忘）
    # ── v2.2 CPE演化通道：EVOLVE临时写入+观察期（区分漂移与演化）──
    evolve_status: str = ""  # 空=正式证据 / "observing"=演化观察期中 / "graduated"=观察期通过转正 / "rejected"=观察期内被证明漂移已移除
    evolve_observe_until: str = ""  # 观察期截止时间戳（ISO格式），空=无观察期
    evolve_recall_hits: int = 0  # 观察期内被召回次数（≥3次且stability稳定→转正）
    # ── d763落地：证据优先级（先分类再压缩，结论/依据/限制默认保留）──
    priority: int = 0  # 0=普通,1=重要,2=关键；容量满时高优先级保留更久


@dataclass
class WorkingMemoryItem:
    """L1工作记忆：短期任务上下文缓冲，有TTL和容量上限，可提升为长期记忆。

    与evidences的区别：evidences是L2情景记忆（持久化、参与成核），working_memory是
    L1工作记忆（短期、当前任务上下文、自动过期、不参与成核）。
    """
    id: str = field(default_factory=uid)
    content: str = ""
    agent_id: str = ""
    created_at: str = field(default_factory=now)
    expires_at: str = ""  # ISO时间戳，空=永不过期（不推荐）
    priority: int = 0  # 0=普通，1=重要，2=关键（高优先级在容量满时保留更久）
    source: str = ""  # 来源：task_context/user_input/tool_result/intermediate
    promoted_to: str = ""  # 提升为evidence/memory后的ID，空=未提升
    recall_hits: int = 0  # 被召回次数（用于判断是否值得提升）
    last_recall_at: str = ""

    def is_expired(self, ref_dt=None) -> bool:
        if not self.expires_at:
            return False
        try:
            from datetime import datetime, timezone
            exp = datetime.fromisoformat(self.expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            ref = ref_dt or datetime.now(timezone.utc)
            return exp <= ref
        except ValueError:
            return False


@dataclass
class Memory:
    id: str = field(default_factory=uid)
    content: str = ""
    formed_at: str = field(default_factory=now)
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    scope: str = "private"  # 结晶来源: private(同 agent≥2一致) / consensus(跨 distinct owner≥2一致)
    contributors: list[str] = field(default_factory=list)  # 参与成核的 agent_id 列表
    # ── v0.8 反证轨 + 时间动力学 ──
    stability: float = 1.0  # 反证轨稳定性：一致增稳、冲突降稳（0~1）
    support_count: int = 0  # 支持该 memory 的证据数
    contradiction_count: int = 0  # 反驳该 memory 的证据数
    last_reinforced: str = ""  # 最近一次被 support 证据强化的时间戳（审计展示用）
    last_reinforced_iter: int = 0  # 最近一次被 support 强化的迭代序号（τ=Effective Iteration，非墙钟；强化门控以此为准）
    # ── v1.3 因果演化循环成核：因果边（符号化，守禁向量）──
    causal: dict = field(default_factory=dict)  # {caused_by,contradicts,...} -> list[node_id]；caused_by=成核血缘（证据id）
    # ── v1.4 原理优先成核（MSM 反哺）──
    principle: str = ""          # 该记忆例证的原理/why（非空=携带原理）
    principle_grounded: bool = False  # True=由共享 principle 的证据结晶（why 先于 how 的首类核心）
    attn_bonus: float = 0.0  # #115 注意力增益：被反复召回证据的 support 加成（仅增 s，不削 c，守反证轨）
    replay_pressure: float = 0.0  # #116 重放压力感知：over-replay 的 contradiction 累计超额召回次数（≥0）
    # ── 蒸馏 #202 记忆外置·分级存储 + 生命周期(TTL) ──
    tier: str = "fact"  # 记忆分级: fact(事实)/preference(偏好)/temp(临时)。temp 可被 TTL 回收
    expires_at: str = ""  # ISO 时间戳；非空且已过期 → evict_expired 回收（仅 temp）。空=永不过期
    # ── v2.1 真独立观测验证（解决字符串判重缺口）──
    observation_hashes: dict = field(default_factory=dict)  # {agent_id: sha256(观测内容)[:16]}，证明各contributor真有独立观测
    observation_timestamps: dict = field(default_factory=dict)  # {agent_id: iso_timestamp}，观测时间分离度可查
    # ── v2.1 权威直注核（authority_direct，规则类单源合法）──
    authority_source: str = ""  # 权威来源标识（如 marvis_rule_v1, liquid_loop_spec）
    authorization_record: str = ""  # 授权记录（如 user_authorized@2026-08-20, scope=R17-R27）


@dataclass
class Conflict:
    anchor_a: str = ""
    anchor_b: str = ""
    description: str = ""
    detected_at: str = field(default_factory=now)
    severity: float = 0.0


@dataclass
class AnchorRelation:
    source_id: str = ""
    target_id: str = ""
    relation_type: str = "relates_to"
    weight: float = 1.0
    created_at: str = field(default_factory=now)


@dataclass
class StateSnapshot:
    timestamp: str = field(default_factory=now)
    entropy: float = 0.0
    anchor_count: int = 0
    evidence_count: int = 0
    memory_count: int = 0
    conflict_count: int = 0

