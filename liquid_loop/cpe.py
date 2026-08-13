from __future__ import annotations

from .textutil import (
    now, uid, _derive_lifecycle_thresholds, _get_version,
    _tokenize, _keyword_overlap, _judge_answer,
    _dissolve_votes_path, _load_dissolve_votes, _save_dissolve_votes,
)
from .entropy import calculate

class CPERegularizer:
    """CPE 正则化引擎：在证据添加前做能力保留裁决"""

    def __init__(self, state: WorkspaceState):
        self.state = state

    def evaluate_new_evidence(self, anchor: Anchor, new_content: str) -> Dict[str, Any]:
        """评估新证据对既有锚点体系的能力侵蚀风险（CPE §3 Regularized Self-Evolution Objective）

        返回:
            action: "PASS" | "BLOCK" | "FLAG"
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

        # ── 2. 漂移检查（Drift Constraint）：新证据是否偏离锚点定义 ──
        drift_score = 1.0 - _keyword_overlap(new_content, anchor.description or anchor.name)

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

        # 证据量越多 → 保护应该越强（CPE的"旧能力权重递增"思想）
        protection_weight = min(len(evs) / 10, 1.0)
        risk_score = risk_score * (1.0 + protection_weight)  # 证据越多风险越敏感
        risk_score = min(risk_score, 1.0)

        # ── 判定 ──
        if risk_score > 0.7:
            action = "BLOCK"
        elif risk_score > 0.4:
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
                "protection_weight": round(protection_weight, 3),
                "existing_evidence_count": len(evs),
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
