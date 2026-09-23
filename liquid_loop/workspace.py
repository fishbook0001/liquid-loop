from __future__ import annotations

from .textutil import (
    now, uid, _derive_lifecycle_thresholds, _get_version,
    _tokenize, _keyword_overlap, _judge_answer,
    _dissolve_votes_path, _load_dissolve_votes, _save_dissolve_votes,
)
from .audit import AuditChain
from .cpe import AGGREGATE_ANCHOR_NAMES, CPERegularizer
from .self_refine import SelfRefineEngine, meta_thinker_evaluate, meta_thinker_advice
from .guard import validate_content


from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import uuid
from pathlib import Path
import hashlib
import json
import sys
import os
import re
import logging
from collections import Counter, defaultdict

from .cognitive_budget import CognitiveBudgetStabilizer






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

    def merge_with(self, other: "Anchor") -> None:
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

    def split_partition(self, cap: int = ANCHOR_SPLIT_CAP, evidences: Optional[list] = None) -> list:
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



@dataclass
class WorkspaceState:
    anchors: list[Anchor] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    memories: list[Memory] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    snapshots: list[StateSnapshot] = field(default_factory=list)
    relations: list[AnchorRelation] = field(default_factory=list)
    audit_chain_hash: str = "genesis"
    audit_prev_hash: str = ""
    version: str = field(default_factory=_get_version)
    updated_at: str = field(default_factory=now)
    # 【v0.4.0】后向自进化状态（借鉴 MemMA 原位自进化）
    self_refine_probes: list[dict] = field(default_factory=list)
    self_refine_results: list[dict] = field(default_factory=list)
    self_refine_repair_count: int = 0
    # 【v2.0.5】即时觉醒模块：越界事件实时检测与日志
    instant_events: list[dict] = field(default_factory=list)  # 即时越界事件日志（最近100条）
    instant_awareness_stats: dict = field(default_factory=lambda: {"total_events": 0, "detected": 0, "awareness_rate": 0.0})
    instant_recent_ops: list[dict] = field(default_factory=list)  # 短期操作记忆（用于重复事件检测，最近50条）
    # 【v0.5.0】CPE 正则化状态（借鉴 UIUC CPE 论文 arXiv:2605.09315）
    regularized_evidences: list[str] = field(default_factory=list)  # 已通过正则化检查的证据ID
    blocked_evidences: list[str] = field(default_factory=list)      # 被正则化拦截的证据ID
    cpe_erosion_warnings: list[dict] = field(default_factory=list)  # 能力侵蚀告警
    cpe_regularization_count: int = 0                               # 累计正则化干预次数
    overlap_cache: dict = field(default_factory=dict)  # 缓存关键词重叠度
    _iteration: int = 0  # 有效状态更新次数（τ = Effective Iteration）；时间动力学以之为时间变量而非墙钟
    canon_fn: object = None  # 可替换 Projection Layer（Layer-1 修复）：注入后冲突检测复用此投影，None=旧关键词重叠回退
    # 【v2.1.0】L1工作记忆层：短期任务上下文缓冲，TTL+容量上限，可提升为长期记忆
    working_memory: list[WorkingMemoryItem] = field(default_factory=list)
    working_memory_capacity: int = 50  # L1工作记忆容量上限
    working_memory_default_ttl_hours: int = 24  # 默认TTL（小时）

    # ── 蒸馏 #202 记忆外置·生命周期(TTL) 回收 ──
    def evict_expired(self, ref_dt=None) -> int:
        """按 TTL 回收过期临时记忆（记忆外置四项挑战之'可靠删除'）。

        仅清理 tier='temp' 且 expires_at 已过期的 Memory；
        锚点/证据不受影响（证据不可删，符合反证轨不可篡改性）。
        返回回收条数。向后兼容：无 expires_at 的 Memory 永不过期。
        """
        if not self.memories:
            return 0
        from datetime import datetime, timezone
        ref = ref_dt or datetime.now(timezone.utc)
        kept, evicted = [], 0
        for m in self.memories:
            if m.tier == "temp" and m.expires_at:
                try:
                    exp = datetime.fromisoformat(m.expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp <= ref:
                        evicted += 1
                        continue
                except ValueError as _e:
                    sys.stderr.write(f"[workspace:warn] temp记忆过期日期解析失败: {_e}\n")
            kept.append(m)
        self.memories = kept
        return evicted

    # ── L1工作记忆层方法（v2.1.0）──

    def add_working_memory(self, content: str, agent_id: str = "",
                           ttl_hours: int = None, priority: int = 0,
                           source: str = "") -> WorkingMemoryItem:
        """添加一条L1工作记忆。自动过期回收+容量上限。"""
        from datetime import datetime, timezone, timedelta
        ttl = ttl_hours if ttl_hours is not None else self.working_memory_default_ttl_hours
        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()
        item = WorkingMemoryItem(
            content=content, agent_id=agent_id,
            expires_at=expires, priority=priority, source=source,
        )
        self.working_memory.append(item)
        self._enforce_working_memory_capacity()
        self.updated_at = now()
        return item

    def get_working_memory(self, agent_id: str = "", limit: int = 20) -> list[WorkingMemoryItem]:
        """获取未过期的工作记忆，按优先级+创建时间排序。"""
        self.evict_expired_working_memory()
        items = [w for w in self.working_memory if not w.is_expired()]
        if agent_id:
            items = [w for w in items if w.agent_id == agent_id or not w.agent_id]
        items.sort(key=lambda w: (-w.priority, w.created_at), reverse=False)
        return items[:limit]

    def recall_working_memory(self, item_id: str) -> Optional[WorkingMemoryItem]:
        """召回一条工作记忆（记录召回次数，用于提升判断）。"""
        for w in self.working_memory:
            if w.id == item_id:
                w.recall_hits += 1
                w.last_recall_at = now()
                return w
        return None

    def promote_working_memory(self, item_id: str, anchor_name: str = "working_memory_promoted",
                               principle: str = "") -> Optional[Evidence]:
        """将工作记忆提升为L2证据（长期记忆）。提升后工作记忆标记promoted_to。"""
        item = next((w for w in self.working_memory if w.id == item_id), None)
        if not item:
            return None
        anchor = next((a for a in self.anchors if a.name == anchor_name), None)
        if not anchor:
            anchor = self.add_anchor(anchor_name, description="L1工作记忆提升的证据")
        evidence = self.add_evidence(
            anchor, item.content, agent_id=item.agent_id,
            principle=principle or item.source,
        )
        if evidence:
            item.promoted_to = evidence.id
        return evidence

    def evict_expired_working_memory(self, ref_dt=None) -> int:
        """回收过期的工作记忆。返回回收条数。"""
        if not self.working_memory:
            return 0
        kept, evicted = [], 0
        for w in self.working_memory:
            if w.is_expired(ref_dt) and not w.promoted_to:
                evicted += 1
                continue
            kept.append(w)
        self.working_memory = kept
        return evicted

    def _enforce_working_memory_capacity(self):
        """强制工作记忆容量上限：超量时按优先级+召回次数淘汰最低的。"""
        if len(self.working_memory) <= self.working_memory_capacity:
            return
        # 排序：先保留未过期的，再按优先级+召回次数，淘汰最低的
        self.working_memory.sort(
            key=lambda w: (w.is_expired(), -w.priority, -w.recall_hits, w.created_at)
        )
        # 未提升的先淘汰
        unpromoted = [w for w in self.working_memory if not w.promoted_to]
        promoted = [w for w in self.working_memory if w.promoted_to]
        overflow = len(self.working_memory) - self.working_memory_capacity
        if overflow > 0 and unpromoted:
            # 淘汰最旧的未提升的
            unpromoted.sort(key=lambda w: (w.priority, w.recall_hits, w.created_at))
            self.working_memory = promoted + unpromoted[overflow:]
        self.updated_at = now()

    # ── API 层：add_anchor / add_evidence（让 README 示例能跑通）──

    def add_anchor(self, name: str, description: str = "",
                   value_density: str = "medium",
                   cognitive_stage: str = "raw",
                   liquidity: str = "warm",
                   ttl_days: float = 0.0) -> Anchor:
        """创建并添加一个新锚点。

        ttl_days: 锚点寿命(天)；0=永不过期(向后兼容默认)。>0 则离现算 expires_at。
        成核即记录 version_hash 状态快照(缺3 old-logits 基准) + 初始陈旧度(缺4)。
        """
        a = Anchor(
            id=uid(), name=name, description=description,
            value_density=value_density, cognitive_stage=cognitive_stage,
            liquidity=liquidity, ttl_days=ttl_days,
        )
        if ttl_days > 0:
            a.expires_at = (datetime.now(timezone) + timedelta(days=ttl_days)).isoformat()
        a.version_hash = a.snapshot_version()
        a.recalc_staleness()
        self.anchors.append(a)
        self.updated_at = now()
        return a

    def add_evidence(self, anchor, content: str, quality: float = 1.0, agent_id: str = "",
                     dedup: bool = False, relation: str = "support",
                     target_memory_id: str = "", principle: str = "",
                     priority: int = 0) -> Optional[Evidence]:
        """向指定锚点添加一条证据。

        anchor 参数兼容：锚点名称(str) | 锚点ID(str) | Anchor对象
        agent_id：多智能体共用写入者标识（缺省空=legacy/单实例）
        dedup：幂等去重开关（默认 False，核心语义允许重复写入以触发结晶）。
        relation：反证轨关系 "support"(一致, 默认) | "contradiction"(冲突) —— 见 v0.8 反证轨。
        target_memory_id：relation="contradiction" 时可选显式指向被反驳的 memory。
        """
        target = None
        if isinstance(anchor, Anchor):
            target = anchor
        else:
            # 先按 name 查，再按 id 查
            target = next((a for a in self.anchors if a.name == anchor), None)
            if not target:
                target = next((a for a in self.anchors if a.id == anchor), None)
        if not target:
            return None
        # 08-18 工程化修复：内容质量 ValidationRule 下沉库层（堵直调绕过通道）。
        # 与 server ll_remember 共用 guard.validate_content，单点维护。
        v_err = validate_content(content)
        if v_err:
            raise ValueError(v_err)
        # 幂等去重（仅当 dedup=True）：同锚点+同内容+同 agent 已存在则复用
        if dedup:
            for ex in self.evidences:
                if ex.anchor_id == target.id and ex.content == content and ex.agent_id == agent_id:
                    return ex
        e = Evidence(
            id=uid(), anchor_id=target.id, content=content,
            anchor_version_hash=target.version_hash,
            quality=quality, timestamp=now(), agent_id=agent_id,
            relation=relation, target_memory_id=target_memory_id,
            added_iter=self._iteration, principle=principle,
            priority=priority,
        )
        self.evidences.append(e)
        target.evidence_ids.append(e.id)
        # 统一调用 on_evidence_added 触发衰减→成核→稳定性刷新
        self._on_evidence_added(target.id)
        self.updated_at = now()
        return e

    def _on_evidence_added(self, anchor_id: str):
        """证据添加后的统一后处理：衰减→成核→反证轨稳定性→冲突检测→预算稳态"""
        self._decay_anchor(anchor_id)
        self._nucleate(anchor_id)
        self._update_memory_stability(anchor_id)
        self._recalc_anchor(anchor_id)
        self._detect_conflicts(anchor_id)
        self._stabilize_budget()
        self._lifecycle_sweep()  # v1.7 老化回收：写入时顺带清理过期证据

    def _stabilize_budget(self):
        """认知预算稳态（PEEK 落地）：超预算时冷归档最低价值证据。默认无预算不动作。"""
        result = CognitiveBudgetStabilizer(self).stabilize()
        if result.get("evicted", 0) > 0:
            self.updated_at = now()

    def _decay_anchor(self, anchor_id: str):
        for e in self.evidences:
            if e.anchor_id == anchor_id:
                e.weight = max(e.weight * 0.95, 0.1)

    def _nucleate(self, anchor_id: str):
        """双轨成核（多智能体共用机制）：

        - private 轨：同一 agent_id 下 >= 2 条一致证据 -> scope=private 结晶
        - consensus 轨：同一 content 被 >= 2 个 distinct agent_id 证据支持 -> scope=consensus 结晶

        复合键 (anchor_id, content, scope) 防跨锚点/跨轨污染。
        兼容 legacy：agent_id 为空视为单实例私有（走 private 轨，contributors=["legacy"]）。
        """
        group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if len(group) < 2:
            return
        # 已结晶键集合（复合键判定，杜绝跨锚点/跨轨污染）
        # 优化：用本锚点证据 id 集合直接判定 memory 是否引用本锚点，
        # 避免对每条 evidence_id 做 next() 线性扫描（原 O(M·E_m·E) → O(M·E_m)）
        group_ev_ids = {e.id for e in group}
        crystallized_keys: set = {
            (m.content, m.scope)
            for m in self.memories
            if group_ev_ids.intersection(m.evidence_ids)
        }
        # ── private 轨：按 agent_id 分组，组内同 content >= 2 ──
        by_agent: dict = defaultdict(list)
        for e in group:
            by_agent[e.agent_id if e.agent_id else "legacy"].append(e)
        for owner, evs in by_agent.items():
            content_counts = Counter(e.content for e in evs)
            for content, count in content_counts.items():
                if count >= 2 and (content, "private") not in crystallized_keys:
                    evidence_ids = [e.id for e in evs if e.content == content]
                    confidence = min(count / len(evs), 1.0)
                    # v1.3 因果演化循环成核：血缘注册（caused_by=成核证据链；contradicts=反证指向）
                    causal = {"caused_by": list(evidence_ids), "causes": [], "enables": [], "contradicts": []}
                    contra = [e.target_memory_id for e in evs if e.content == content
                              and e.relation == "contradiction" and e.target_memory_id]
                    if contra:
                        causal["contradicts"] = contra
                    self.memories.append(Memory(
                        content=content,
                        evidence_ids=evidence_ids,
                        confidence=confidence,
                        scope="private",
                        contributors=[owner],
                        causal=causal,
                    ))
        # ── consensus 轨：跨 distinct owner 同 content >= 2 ──
        owners_by_content: dict = defaultdict(set)
        for e in group:
            if not e.agent_id:
                continue  # legacy 不参与共识轨（无法跨主体）
            owners_by_content[e.content].add(e.agent_id)
        for content, owners in owners_by_content.items():
            if len(owners) < 2:
                continue
            # ── 共识参与者白名单守卫（2026-09-23 可选，fail-open）──
            # 默认仅告警（保持现状）；LIQUID_CONSENSUS_ENFORCE=1 时硬拒非白名单 agent 成核
            _parties = _load_consensus_parties()
            if _parties is not None:
                _non = owners - _parties
                if _non:
                    if os.environ.get("LIQUID_CONSENSUS_ENFORCE") == "1":
                        logging.getLogger("liquid_loop.workspace").warning(
                            "[consensus-guard] 拒绝非白名单 agent %s 形成共识(content=%s)，已降级",
                            sorted(_non), content[:40])
                        continue
                    logging.getLogger("liquid_loop.workspace").warning(
                        "[consensus-guard] 非白名单 agent %s 参与共识(仅告警, fail-open)(content=%s)",
                        sorted(_non), content[:40])
            # 已存在的共识结晶：把新一致方并入 contributors（动态扩展，支撑三方+ CCI 计量）
            existing = next((m for m in self.memories if m.scope in ("consensus", "authority_direct") and m.content == content), None)
            if existing is not None:
                merged = set(existing.contributors) | owners
                if merged != set(existing.contributors):
                    existing.contributors = sorted(merged)
                    # v2.1 新owner加入时补观测验证
                    for e in group:
                        if e.content == content and e.agent_id in owners and e.agent_id not in existing.observation_hashes:
                            existing.observation_hashes[e.agent_id] = hashlib.sha256(f'{e.id}:{e.agent_id}:{e.content}'.encode()).hexdigest()[:16]
                            existing.observation_timestamps[e.agent_id] = e.created_at if hasattr(e, 'created_at') and e.created_at else now()
                continue
            if (content, "consensus") not in crystallized_keys and (content, "authority_direct") not in crystallized_keys:
                evidence_ids = [e.id for e in group if e.content == content and e.agent_id in owners]
                confidence = min(len(owners) / 2.0, 1.0)
                # v1.3 因果演化循环成核：血缘注册（caused_by=成核证据链；contradicts=反证指向）
                causal = {"caused_by": list(evidence_ids), "causes": [], "enables": [], "contradicts": []}
                contra = [e.target_memory_id for e in group if e.content == content
                          and e.relation == "contradiction" and e.target_memory_id]
                if contra:
                    causal["contradicts"] = contra
                # v2.1 真独立观测验证：成核时自动记录各contributor的观测哈希+时间戳
                obs_hashes = {}
                obs_timestamps = {}
                for e in group:
                    if e.content == content and e.agent_id in owners:
                        obs_hashes[e.agent_id] = hashlib.sha256(f'{e.id}:{e.agent_id}:{e.content}'.encode()).hexdigest()[:16]
                        obs_timestamps[e.agent_id] = e.created_at if hasattr(e, 'created_at') and e.created_at else now()
                # v2.1 权威直注自动判定：证据带authority/rule标签或来自权威注入源 → authority_direct核
                auth_evidences = [e for e in group if e.content == content and e.agent_id in owners
                                  and (hasattr(e, 'tags') and e.tags and any(t in ('authority', 'rule', 'spec') for t in e.tags))]
                if auth_evidences and len(owners) == 1:
                    # 单源权威注入 → authority_direct
                    auth_source = auth_evidences[0].agent_id
                    self.memories.append(Memory(
                        content=content,
                        evidence_ids=evidence_ids,
                        confidence=1.0,
                        scope="authority_direct",
                        contributors=sorted(owners),
                        causal=causal,
                        observation_hashes=obs_hashes,
                        observation_timestamps=obs_timestamps,
                        authority_source=auth_source,
                        authorization_record=f"auto_inferred@{now()},source={auth_source}",
                    ))
                else:
                    # 多源观测一致 → consensus
                    self.memories.append(Memory(
                        content=content,
                        evidence_ids=evidence_ids,
                        confidence=confidence,
                        scope="consensus",
                        contributors=sorted(owners),
                        causal=causal,
                        observation_hashes=obs_hashes,
                        observation_timestamps=obs_timestamps,
                    ))
        # ── v1.4 原理优先成核（MSM 反哺：先教 why 再教 how）──
        # 共享 principle 的证据（即便 surface content 不同）结晶为「原理记忆」：
        # 原理成为首类结晶核心，表层事实经 causal.caused_by 挂到原理之下。
        # 对应 MSM 发现——先教原理(why)再教规则(how)使行为在分布漂移下仍稳健
        # （Anthropic MSM：Qwen 失控率 54%→7% / 68%→5%）。principle_grounded 标记
        # 供下游抗衰减加权（P2）。守禁向量：principle 为结构化文本，非 embedding。
        prin_groups: dict = defaultdict(list)
        for e in group:
            if e.principle:
                prin_groups[e.principle].append(e)
        for prin, evs_p in prin_groups.items():
            if len(evs_p) < 2:
                continue
            existing_p = next((m for m in self.memories
                               if m.principle_grounded and m.content == prin), None)
            if existing_p is not None:
                merged = set(existing_p.evidence_ids) | {e.id for e in evs_p}
                if merged != set(existing_p.evidence_ids):
                    existing_p.evidence_ids = sorted(merged)
                continue
            if (prin, "private") not in crystallized_keys:
                evidence_ids = [e.id for e in evs_p]
                confidence = min(len(evs_p) / 2.0, 1.0)
                # 原理记忆稳定性封顶为 1.0（principle_grounded 标记驱动下游抗衰减加权）
                causal = {"caused_by": list(evidence_ids), "causes": [],
                          "enables": [], "contradicts": []}
                self.memories.append(Memory(
                    content=prin,
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                    scope="private",
                    contributors=sorted({e.agent_id for e in evs_p if e.agent_id}),
                    principle=prin,
                    principle_grounded=True,
                    stability=1.0,
                    causal=causal,
                ))
        # 成核后触发自动描述回流（仅当描述为空）
        self._auto_describe_anchor(anchor_id)

    def _auto_describe_anchor(self, anchor_id: str):
        """高置信结晶自动回填锚点空描述（尊重人工设定）"""
        anchor = next((a for a in self.anchors if a.id == anchor_id), None)
        if not anchor or anchor.description:
            return
        crystals = [m for m in self.memories if any(eid in [e.id for e in self.evidences if e.anchor_id == anchor_id] for eid in m.evidence_ids)]
        if crystals:
            best = max(crystals, key=lambda m: m.confidence)
            if best.confidence >= 0.8:
                anchor.description = best.content

    def _detect_conflicts(self, anchor_id: str):
        """群内自洽检测（Layer-1 修复：复用可替换 Projection Layer，惩罚持久化）。

        旧实现用关键词重叠（Jaccard）近似"语义冲突"——本质是 Lexical Proxy，
        会导致：① 对真异事实（关键词不重叠）误触发；② 对近重复（关键词高重叠）
        反而不触发（反相关）；③ 自建一套不可替换的投影启发式（Hidden Projection Layer）。

        修复：若 WorkspaceState.canon_fn 已注入，则冲突判定改用同一 Projection Layer——
        两条 distinct content 证据，若投影到【同一 CP】但 relation 相反（一 support 一
        contradiction）-> 真冲突；若投影同 CP 同 relation -> 近重复（不冲突）；
        若投影不同 CP -> 即使关键词重叠也不算冲突（语义独立）。

        惩罚持久化：锚点稳定性惩罚写入锚点字段 conflict_penalty（累积乘子），
        _recalc_anchor 在合成 stability 时乘入，因此 load / step 重算后惩罚不丢失
        （旧实现在 stability 字段上直接 *=0.9，被后续 recalc 覆盖，故不持久）。

        canon_fn 为 None 时回退旧 _keyword_overlap 行为，保证生产向后兼容。
        """
        import os
        conflict_threshold = float(os.environ.get('LIQUID_CONFLICT_THRESHOLD', '0.2'))

        group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if len(group) < 2:
            # 08-18 工程化修复：锚点证据已不足 → 清除历史冲突残留（防 stale conflict 累积）
            self.conflicts = [c for c in self.conflicts if c.anchor_a != anchor_id]
            return
        # ── 聚合型锚点豁免（2026-08-17 审计修复）────────────────────────
        # distill/research_asset/principle 等锚点设计上聚合多主题资产，
        # keyword-overlap 平均一致度天然偏低，会系统性误报冲突/漂移。
        _agg_anchor = next((a for a in self.anchors if a.id == anchor_id), None)
        # P3c（2026-09-12）：豁免名单收敛为单一口径源（liquid_loop.cpe.AGGREGATE_ANCHOR_NAMES），
        # 消除此前的双份字面量（CPE 扫描与冲突检测曾各自维护，存在漂移风险）。
        if _agg_anchor and _agg_anchor.name in AGGREGATE_ANCHOR_NAMES:
            self.conflicts = [c for c in self.conflicts if c.anchor_a != anchor_id]
            return
        distinct: list = []
        seen: set = set()
        for e in group:
            if e.content and e.content not in seen:
                seen.add(e.content)
                distinct.append(e.content)

        # ── 回退路径：未注入 Projection Layer -> 旧关键词重叠行为（向后兼容）──
        if self.canon_fn is None:
            if len(distinct) < 2:
                # 08-18 工程化修复：去重后样本不足 → 清除历史冲突残留
                self.conflicts = [c for c in self.conflicts if c.anchor_a != anchor_id]
                return
            overlaps = []
            for i in range(len(distinct)):
                for j in range(i + 1, len(distinct)):
                    overlaps.append(_keyword_overlap(distinct[i], distinct[j], self.overlap_cache))
            if not overlaps:
                return
            avg = sum(overlaps) / len(overlaps)
            if avg < conflict_threshold:
                anchor = next((a for a in self.anchors if a.id == anchor_id), None)
                if any(c.anchor_a == anchor_id for c in self.conflicts):
                    return
                self.conflicts.append(Conflict(
                    anchor_a=anchor_id,
                    description=f"证据间平均一致度 {avg:.2f}（<{conflict_threshold}），存在潜在冲突/漂移，需人工确认",
                    severity=round(1.0 - avg, 2),
                ))
                if anchor:
                    anchor.conflict_penalty = max(0.1, anchor.conflict_penalty * 0.9)
            return

        # ── Layer-1 路径：复用同一可替换 Projection Layer ──
        # 按 CP 分组 distinct content；同 CP 内若同时有 support 与 contradiction -> 真冲突
        by_cp: dict = {}
        rel_by_content: dict = {}
        for e in group:
            cp = self.canon_fn(e.content)
            by_cp.setdefault(cp, []).append(e.content)
            rel_by_content.setdefault(e.content, set()).add(e.relation or "support")
        triggered = False
        for cp, contents in by_cp.items():
            if len(contents) < 2:
                continue
            distinct_rels = set()
            for c in contents:
                distinct_rels |= (rel_by_content.get(c) or {"support"})
            if "support" in distinct_rels and "contradiction" in distinct_rels:
                triggered = True
                break
        if triggered:
            anchor = next((a for a in self.anchors if a.id == anchor_id), None)
            if not any(c.anchor_a == anchor_id for c in self.conflicts):
                self.conflicts.append(Conflict(
                    anchor_a=anchor_id,
                    description="Projection Layer 检测到同 CP 内存在 support 与 contradiction 异 relation 证据，判定为语义冲突",
                    severity=conflict_threshold,
                ))
            if anchor:
                anchor.conflict_penalty = max(0.1, anchor.conflict_penalty * 0.9)


    def _recalc_anchor(self, anchor_id: str, group: Optional[list] = None):
        if group is None:
            group = [e for e in self.evidences if e.anchor_id == anchor_id]
        if not group:
            return
        avg_weight = sum(e.weight for e in group) / len(group)
        for a in self.anchors:
            if a.id == anchor_id:
                # base = 证据权重均值；stability = base + SEAL 自评层（seal_adjust 不被覆盖）
                base = avg_weight
                a.base_stability = base
                a.stability = round(min(1.0, max(0.1, base * a.conflict_penalty + a.seal_adjust)), 3)
                evidence_count = len(group)
                a.decay_value(evidence_count=evidence_count)
                a.recalc_strength(evidence_count)
                a.auto_classify(evidence_count)

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

    def _update_memory_stability(self, anchor_id: str, group: Optional[list] = None):
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
                "levels": levels, "total_affected": sum(len(l) for l in levels)}

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

    def list_for(self, agent_id: str = "", category: Optional[str] = None) -> list[dict]:
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
            if m.scope == "consensus":
                if not agent_id or agent_id not in m.contributors:
                    continue
            elif m.scope == "private":
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

    def _delete_evidence_by_id(self, agent_id: str, evidence_id: str) -> Optional[dict]:
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

    def _delete_memory_by_id(self, agent_id: str, memory_id: str) -> Optional[dict]:
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

    def _delete_by_content(self, agent_id: str, memory_id: str) -> Optional[dict]:
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

# ── dissolve 投票持久化（独立于核心 dataclass，守 North-Star 零改动）──






# ==============================================================================
# ─────────────────────────────────────────────────────────────
# 研究脚手架区（research-only · 不在 8790 生产路径）
# 以下 CPERegularizer / SelfRefineEngine / meta_thinker 仅供 CLI 的
# audit / evolution 子命令与单测使用；8790 server 不加载，不影响稳态。
# ─────────────────────────────────────────────────────────────
# CPERegularizer — 能力保留正则化引擎（借鉴 UIUC CPE 论文 arXiv:2605.09315）
#
# CPE 核心思想（§3）：自进化更新应在获取新能力的同时，最小化对已有能力结构的破坏性干扰。
# 液环实例化：当新证据可能覆盖/削弱旧锚点时，施加正则化约束——不是拦截而是加权重排队。
#
# 三个正则化策略对应 CPE 三个维度（§3.3）：
#   1. 回顾性保护（Retrospective Protection）：新证据与旧证据一致性低于阈值 → 降权
#   2. 漂移约束（Drift Constraint）：新证据与锚点定义方向偏离 → 标记
#   3. 泛化防线（Generalization Guard）：同锚点下证据一致性持续下降 → 告警
# ==============================================================================



# ==============================================================================
# SelfRefineEngine — 后向自进化（借鉴 MemMA 原位自进化）
# 融合点：利用液环现有锚点体系做探测QA验证 + 证据锚定修复
# ==============================================================================









# ==============================================================================
# MetaThinker — 前向策略层（借鉴 MemMA Meta-Thinker，零LLM方案）
# ==============================================================================





# ==============================================================================
