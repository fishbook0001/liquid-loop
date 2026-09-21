from __future__ import annotations

from .textutil import (
    now, uid, _derive_lifecycle_thresholds, _get_version,
    _tokenize, _keyword_overlap, _judge_answer,
    _dissolve_votes_path, _load_dissolve_votes, _save_dissolve_votes,
)
from .entropy import calculate
import os
import sys

# ── 聚合型锚点豁免名单（2026-09-12 口径对齐 workspace.py:690）──────────────
# distill / research_asset / principle 等锚点设计上聚合多主题资产，其证据之间的
# keyword_overlap 平均一致度天然偏低——用同一 overlap 度量判"泛化崩塌"会系统性误报。
# workspace.py 的冲突检测已豁免这 8 个锚点，而 cpe.scan_erosion 原先无豁免，
# 属同一度量的双口径。此处对齐同一名单，仅作用于依赖 pairwise overlap 的
# generalization_erosion 判定；retrospective_decay / behavioral_drift 不依赖
# overlap，聚合锚点同样适用，故不受豁免影响。
AGGREGATE_ANCHOR_NAMES = frozenset({
    "distill", "research_asset", "tool_ref", "principle",
    "harness-factor", "pattern", "metacontrol_flag", "pending_action",
    # ── P3c（2026-09-12）统一口径补入：资产/类目汇集型锚点 ──────────────
    # 判定依据（只读度量，见 output/P3c_步骤③锚点定论_2026-09-12.md）：
    # 这些锚点名为集合/类目名词（非单一命题），证据天然跨主题汇集，其
    # "两两词面重叠 <0.2" 由定义决定，不构成泛化崩塌信号。典型客观证据：
    #   fact            n=313，严格阈值(0.25)下 C=28 微主题簇、最大簇占比 0.58
    #   junshi_workflow n=591，C=80、最大簇 0.475、224 个主题标签、4 个写入者
    #   军师调研         n=31，C=21、25 个主题标签、2 个写入者
    #   research        n=52，C=46、40 个主题标签
    #   marvis_lessons  n=19（19 条互异教训）、code n=6（6 段源码）、rule n=6（3 类规则）
    #   marvis_rules n=12、quarantine n=39（隔离区多来源）、
    #   distilled_evidence n=3、马维斯版军师调研 n=12
    # 反向口径：单一命题型锚点（harness-factor 已单列、active_question /
    # evolution_candidate / verification 等）若出现低重叠，仍判真实侵蚀、保留告警。
    "fact", "junshi_workflow", "军师调研", "marvis_rules", "marvis_lessons",
    "code", "rule", "distilled_evidence", "research", "quarantine",
    "马维斯版军师调研",
})


class CPERegularizer:
    """CPE 正则化引擎：在证据添加前做能力保留裁决

    v2.4 修正（2026-09-13）：
    - P5: 修复 EVOLVE 死支 —— EVOLVE 不再嵌套在 risk>block 内，改由 evolve 阈值
      独立把关（normal: block 0.7 / evolve 0.5），使"方向一致+低重叠"的扩展型
      洞察真实可达；evolve_max=5 与观察期转正/拒绝机制保持不变。

    v2.3 补强（2026-09-01）：
    - P3: 场景感知（learning/normal），学习模式下阈值动态放宽
    - P4: EVOLVE灰色空间并发上限（默认5条），防膨胀
    - 阈值随场景动态调整，不再硬编码

    v2.2 优化（2026-09-01）：
    - P0: 漂移评分区分"漂移"与"演化"，增加方向一致性检查
    - P1: 新增EVOLVE动作，临时写入+观察期，给新洞察试错空间
    - P2: 保护权重上限从1.0降到0.5，方向一致的新内容保护权重再降70%
    """

    # 场景阈值配置：learning模式更宽松（允许更多演化），normal模式用默认
    # P5（2026-09-13）：normal 的 evolve 原与 block 同为 0.7 —— 演进通道阈值被 block
    #   覆盖而失效（且 EVOLVE 分支又嵌套在 risk>block 内 → 双重不可达）。
    #   现让 evolve 独立取值 0.5：方向一致+低重叠的扩展型洞察 risk≈0.52~0.58 可达该
    #   阈值 → 进入观察期；block=0.7 仅约束方向不一致的真漂移。
    _THRESHOLDS = {
        "normal": {"block": 0.7, "evolve": 0.5, "flag": 0.4},
        "learning": {"block": 0.85, "evolve": 0.55, "flag": 0.30},
    }

    def __init__(self, state: WorkspaceState, mode: str = ""):
        self.state = state
        # P3: 场景感知——从环境变量读取，默认normal；learning模式用于军师调研/自主学习等批量新知识注入场景
        self.mode = (mode or os.environ.get("LIQUID_CPE_MODE", "normal")).lower()
        if self.mode not in self._THRESHOLDS:
            self.mode = "normal"
        # P4: EVOLVE灰色空间并发上限——防膨胀，默认5条，可环境变量配置
        self.evolve_max = int(os.environ.get("LIQUID_CPE_EVOLVE_MAX", "5"))

    def _get_thresholds(self) -> dict:
        """P3: 根据当前场景返回动态阈值（不再硬编码）"""
        return self._THRESHOLDS.get(self.mode, self._THRESHOLDS["normal"])

    def _count_active_evolve(self) -> int:
        """P4: 统计当前处于观察期的EVOLVE证据数量（灰色空间占用量）"""
        count = 0
        for e in getattr(self.state, "evidences", []):
            if getattr(e, "evolve_status", "") == "observing":
                # 检查是否已过期（过期的不算占用）
                observe_until = getattr(e, "evolve_observe_until", "")
                if observe_until:
                    try:
                        from datetime import datetime, timezone
                        if datetime.fromisoformat(observe_until.replace("Z", "+00:00")) > datetime.now(timezone.utc):
                            count += 1
                            continue
                    except (ValueError, TypeError) as _e:
                        sys.stderr.write(f"[cpe:warn] evolve_observe_until日期解析失败: {_e}\n")
                count += 1
        return count

    def _calc_direction_consistency(self, anchor: Anchor, new_content: str, category: str = "") -> float:
        """P0: 计算新内容与锚点核心方向的一致性（0.0~1.0）

        v2.2.1修复：原算法用anchor.name/description的关键词重叠，
        但锚点name是自动生成的元数据（如"principle"），与实际内容几乎无重叠，
        导致direction_consistency恒≈0.02，EVOLVE通道永不触发。

        修复后：
        1. category匹配是方向一致性的最强信号——用户写入时指定category，
           本身就是在声明"这条内容属于这个方向"。category匹配→0.8
        2. 锚点核心关键词（从现有证据提取的高频词）重叠→补充信号
        3. 两者取最大值
        """
        # 信号1：category匹配（最强信号）
        if category and anchor.name:
            if category.lower() == anchor.name.lower() or category.lower() in anchor.name.lower():
                return 0.8

        # 信号2：从锚点下现有证据提取核心关键词，计算重叠
        direction_keywords = set()
        if hasattr(anchor, 'evidence_ids') and anchor.evidence_ids:
            # 从state中获取该锚点的证据，提取高频词
            evs = [e for e in self.state.evidences if e.anchor_id == anchor.id and e.content]
            if evs:
                # 简单词频：取所有证据中出现频率最高的前20个词
                from collections import Counter
                word_freq = Counter()
                for ev in evs[:20]:  # 最多看20条，避免计算量过大
                    words = _tokenize(ev.content)
                    word_freq.update(words)
                # 取频率>1的词作为核心关键词
                direction_keywords = {w for w, c in word_freq.most_common(30) if c > 1}

        if direction_keywords:
            new_words = set(_tokenize(new_content))
            overlap = len(new_words & direction_keywords) / max(len(direction_keywords), 1)
            return min(0.7, overlap * 3)  # 放大系数，因为核心关键词本身就少

        # 兜底：用anchor.name/description
        direction_text = " ".join(filter(None, [
            anchor.name or "",
            anchor.description or "",
        ]))
        if not direction_text:
            return 0.5
        return _keyword_overlap(new_content, direction_text)

    def evaluate_new_evidence(self, anchor: Anchor, new_content: str, category: str = "") -> Dict[str, Any]:
        """评估新证据对既有锚点体系的能力侵蚀风险（CPE §3 Regularized Self-Evolution Objective）

        返回:
            action: "PASS" | "BLOCK" | "FLAG" | "EVOLVE" | "MERGE"
            score: 0.0(安全) ~ 1.0(高风险)
            reasons: 原因列表
        """
        if not anchor.evidence_ids:
            return {"action": "PASS", "score": 0.0, "reasons": ["锚点无现有证据，无覆盖风险"]}

        evs = [e for e in self.state.evidences if e.anchor_id == anchor.id]
        if not evs:
            return {"action": "PASS", "score": 0.0, "reasons": ["无对应证据，安全"]}

        # ── 1. 回顾性检查（Retrospective Protection）：新证据是否与旧证据方向一致 ──
        old_contents = [e.content for e in evs if e.content]
        overlaps = [_keyword_overlap(new_content, old) for old in old_contents]
        max_overlap = max(overlaps) if overlaps else 0.0
        avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

        # ── P0: 方向一致性检查（区分漂移与演化）──
        direction_consistency = self._calc_direction_consistency(anchor, new_content, category)

        # ── 2. 漂移检查（Drift Constraint）：新证据是否偏离锚点定义 ──
        # v2.2优化：方向一致的新内容，低重叠是演化不是漂移，漂移风险减半
        if old_contents:
            base_drift = 1.0 - avg_overlap
            if direction_consistency > 0.5:
                # 方向一致 → 演化，漂移风险减半
                drift_score = base_drift * (1.0 - direction_consistency) * 0.5
            else:
                # 方向不一致 → 真正的漂移
                drift_score = base_drift
        else:
            drift_score = 1.0 - _keyword_overlap(new_content, anchor.name or anchor.description or "")

        # ── 4. 重复检测：新证据与已有证据相似度 > 0.7 → 近似重复，建议合并 ──
        duplicate_of = None
        for old_content in old_contents:
            if _keyword_overlap(new_content, old_content) > 0.7:
                duplicate_of = old_content[:60]
                break

        if duplicate_of:
            return {
                "action": "MERGE",
                "score": 0.0,
                "reasons": [f"与已有证据近似重复: '{duplicate_of}...'"],
                "details": {
                    "max_overlap": round(max_overlap, 3),
                    "avg_overlap": round(avg_overlap, 3),
                    "drift_score": round(drift_score, 3),
                    "direction_consistency": round(direction_consistency, 3),
                    "existing_evidence_count": len(evs),
                    "duplicate_of": duplicate_of,
                }
            }

        # ── 综合风险评分 ──
        risk_score = 0.0
        reasons = []

        # 低重叠 → 高回顾性衰退风险
        if max_overlap < 0.1:
            risk_score += 0.5
            reasons.append(f"回顾性风险: 与现有证据最大重叠{max_overlap:.2f}，可能侵蚀")
        elif max_overlap < 0.3:
            risk_score += 0.25
            reasons.append(f"回顾性风险: 中等重叠{max_overlap:.2f}，建议验证")

        if drift_score > 0.7:
            risk_score += 0.3
            reasons.append(f"漂移风险: 与锚点方向偏离概率{drift_score:.2f}")
        elif drift_score > 0.4:
            risk_score += 0.1
            reasons.append(f"漂移风险: 轻微偏离{drift_score:.2f}")

        # P0: 方向一致的新内容，标注为演化候选
        if direction_consistency > 0.5 and max_overlap < 0.3:
            reasons.append(f"演化候选: 方向一致性{direction_consistency:.2f}，低重叠为扩展非冲突")

        # ── P2: 保护权重优化（成熟锚点也需要演化）──
        # 原算法：protection_weight = min(len(evs)/10, 1.0)，上限1.0导致risk_score翻倍
        # 优化后：上限降到0.5，方向一致的新内容保护权重再降70%
        base_protection = min(len(evs) / 20, 0.5)
        if direction_consistency > 0.5:
            protection_weight = base_protection * 0.3  # 方向一致 → 保护权重降70%
        else:
            protection_weight = base_protection
        risk_score = risk_score * (1.0 + protection_weight)
        risk_score = min(risk_score, 1.0)

        # ── 判定（P1: EVOLVE动作 + P3: 动态阈值 + P4: EVOLVE上限）──
        thresholds = self._get_thresholds()
        block_thresh = thresholds["block"]
        evolve_thresh = thresholds["evolve"]
        flag_thresh = thresholds["flag"]

        # P5（2026-09-13）修正 evolve 死支：EVOLVE 不再嵌套在 risk>block 之下。
        # 原死因：方向一致时 drift_score 被减半（<0.25 不加分）、protection_weight 降 70%
        #   → risk 上界 ≈0.58 < block 0.7，EVOLVE 数学不可达（实测 20 例真实探针全落 FLAG、
        #   0 例 EVOLVE）。现逻辑：扩展型洞察（方向一致 >0.5 且低重叠 <0.3）由 evolve_thresh
        #   独立把关；block_thresh 只拦方向不一致的真漂移。evolve_max=5 与观察期转正/拒绝机制不变。
        is_extension = direction_consistency > 0.5 and max_overlap < 0.3
        if is_extension and risk_score >= evolve_thresh:
            # P4: EVOLVE灰色空间并发上限检查——防膨胀
            active_evolve = self._count_active_evolve()
            if active_evolve >= self.evolve_max:
                # 灰色空间已满：降级为FLAG（标记但不进入观察期），不膨胀
                action = "FLAG"
                reasons.append(f"EVOLVE空间已满({active_evolve}/{self.evolve_max})，降级为FLAG防膨胀")
            else:
                # 方向一致+低重叠的扩展型洞察 → 演化通道：临时写入观察
                action = "EVOLVE"
                reasons.append(
                    f"演化通道({self.mode}模式): 方向一致性{direction_consistency:.2f}+低重叠"
                    f"{max_overlap:.2f}，扩展型洞察进入观察期"
                    f"（evolve阈值{evolve_thresh}，灰色空间{active_evolve+1}/{self.evolve_max}）"
                )
        elif risk_score > block_thresh:
            # 方向不一致且风险高 → 真正的漂移，拦截
            action = "BLOCK"
        elif risk_score > flag_thresh:
            action = "FLAG"
        else:
            action = "PASS"

        return {
            "action": action,
            "score": round(risk_score, 3),
            "reasons": reasons,
            "details": {
                "max_overlap": round(max_overlap, 3),
                "avg_overlap": round(avg_overlap, 3),
                "drift_score": round(drift_score, 3),
                "direction_consistency": round(direction_consistency, 3),
                "protection_weight": round(protection_weight, 3),
                "existing_evidence_count": len(evs),
                # P5：阈值与实际判定依据显式化（审计用，不参与打分）
                "block_threshold": block_thresh,
                "evolve_threshold": evolve_thresh,
                "flag_threshold": flag_thresh,
                "is_extension": is_extension,
            }
        }

    def scan_erosion(self) -> List[Dict[str, Any]]:
        """扫描全工作区，检测能力侵蚀信号（CPE §2.2 Capability Erosion）

        对标 CPE 三大表现：
        - 回顾性衰退: value_score 连续两次衰减
        - 策略漂移: 锚点近期的 stability 波动超过阈值
        - 泛化崩塌: 同锚点证据之间的一致性持续下降
        """
        warnings = []
        for a in self.state.anchors:
            evs = sorted(
                [e for e in self.state.evidences if e.anchor_id == a.id],
                key=lambda x: x.timestamp
            )
            if len(evs) < 2:
                continue

            # 回顾性衰退：最新 vs 次新 value_score
            a.decay_value(evidence_count=len(evs))
            new_score = a.value_score
            a.decay_value(evidence_count=len(evs) - 1)
            old_score = a.value_score
            if old_score > new_score and (old_score - new_score) > 0.1:
                warnings.append({
                    "type": "retrospective_decay",
                    "anchor": a.name,
                    "severity": "medium",
                    "detail": f"value_score {old_score:.2f}→{new_score:.2f} (Δ={old_score - new_score:.2f})",
                })

            # 策略漂移：stability 突变
            old_strength = a.recalc_strength(len(evs) - 1)
            new_strength = a.recalc_strength(len(evs))
            drift = abs(new_strength - old_strength)
            if drift > 0.15:
                warnings.append({
                    "type": "behavioral_drift",
                    "anchor": a.name,
                    "severity": "medium",
                    "detail": f"stability {old_strength:.2f}→{new_strength:.2f} (Δ={drift:.2f})",
                })

            # 泛化崩塌：证据间平均重叠度
            # 聚合型锚点豁免（口径与 workspace.py:690 一致，见 AGGREGATE_ANCHOR_NAMES）：
            # 该类锚点天然跨主题汇集，两两重叠度低属设计使然，不构成侵蚀信号。
            if a.name in AGGREGATE_ANCHOR_NAMES:
                continue
            content_pairs = []
            for i in range(len(evs)):
                for j in range(i + 1, len(evs)):
                    if evs[i].content and evs[j].content:
                        content_pairs.append(
                            _keyword_overlap(evs[i].content, evs[j].content)
                        )
            if content_pairs:
                avg_consistency = sum(content_pairs) / len(content_pairs)
                if avg_consistency < 0.2 and len(evs) >= 3:
                    warnings.append({
                        "type": "generalization_erosion",
                        "anchor": a.name,
                        "severity": "high",
                        "detail": f"证据间平均重叠度{avg_consistency:.2f} (<0.2, {len(evs)}条证据)",
                    })

        self.state.cpe_erosion_warnings = warnings
        return warnings

    def coalesce(self, anchor_name: str, threshold: float = 0.7) -> Dict[str, Any]:
        """模糊去重合并：同锚点下相似度 > threshold 的证据自动合并

        合并策略：
          1. 遍历同锚点所有证据，两两计算 _keyword_overlap
          2. 重叠度 > threshold → 保留较长的那条，标记较短的那条废弃
          3. 返回合并统计

        这是 CPE 泛化防线的主动修复动作——检测到 erosion 后调用。
        """
        anchor = next((a for a in self.state.anchors if a.name == anchor_name), None)
        if not anchor:
            return {"status": "error", "message": f"锚点 '{anchor_name}' 不存在"}

        evs = [e for e in self.state.evidences if e.anchor_id == anchor.id]
        if len(evs) < 2:
            return {"status": "skip", "message": "证据不足2条，无需合并", "removed": 0}

        # 按时间戳排序（旧→新）
        evs_sorted = sorted(evs, key=lambda x: x.timestamp)
        to_remove: set[str] = set()
        merged = 0

        for i in range(len(evs_sorted)):
            if evs_sorted[i].id in to_remove:
                continue
            for j in range(i + 1, len(evs_sorted)):
                if evs_sorted[j].id in to_remove:
                    continue
                overlap = _keyword_overlap(evs_sorted[i].content, evs_sorted[j].content)
                if overlap > threshold:
                    # 保留较长的，移除较短的
                    if len(evs_sorted[i].content) >= len(evs_sorted[j].content):
                        to_remove.add(evs_sorted[j].id)
                    else:
                        to_remove.add(evs_sorted[i].id)
                        break
                    merged += 1

        self.state.evidences = [e for e in self.state.evidences if e.id not in to_remove]
        self.state.cpe_regularization_count += 1

        return {
            "status": "ok",
            "anchor": anchor_name,
            "threshold": threshold,
            "removed": len(to_remove),
            "merged_pairs": merged,
            "remaining": len(evs) - len(to_remove),
        }

    def regularize(self, anchor_name: str, content: str, force: bool = False) -> Dict[str, Any]:
        """对单条证据执行 CPE 正则化检查（对外接口）

        force=True 时跳过 BLOCK，仅做 FLAG 标记
        """
        s = self.state
        anchor = next((a for a in s.anchors if a.name == anchor_name), None)
        if not anchor:
            return {"action": "PASS", "reason": "锚点不存在，不拦截"}

        result = self.evaluate_new_evidence(anchor, content)
        action = result["action"]
        if force and action == "BLOCK":
            action = "FLAG"

        if action == "PASS":
            # 已通过的证据ID会在外部添加后追加到 regularized_evidences
            pass
        elif action == "BLOCK":
            s.blocked_evidences.append(f"{anchor_name}:{content[:40]}")
            s.cpe_regularization_count += 1
        elif action == "FLAG":
            s.blocked_evidences.append(f"FLAG:{anchor_name}:{content[:40]}")
            s.cpe_regularization_count += 1

        # 保存扫描结果
        self.scan_erosion()
        return result
