from __future__ import annotations

from .textutil import (
    now, uid, _derive_lifecycle_thresholds, _get_version,
    _tokenize, _keyword_overlap, _judge_answer,
    _dissolve_votes_path, _load_dissolve_votes, _save_dissolve_votes,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .workspace import WorkspaceState, Anchor

class SelfRefineEngine:
    """液环后向自进化引擎。借鉴 MemMA 原位自进化三步法。"""

    def __init__(self, state: WorkspaceState):
        self.state = state

    def generate_probes(self, evidence_ids: List[str] = None) -> List[Dict[str, Any]]:
        """从证据中生成差异化的探测QA对。

        MemMA融合：每个证据生成唯一的问题（通过截取前半段做问，后半段做答），
        确保 verify 能通过关键词匹配找到正确答案。
        """
        evidences = self.state.evidences
        if evidence_ids:
            evidences = [e for e in evidences if e.id in evidence_ids]
        if not evidences:
            return []
        probes = []
        for e in evidences:
            if not e.content:
                continue
            anchor_name = next((a.name for a in self.state.anchors if a.id == e.anchor_id), "未知锚点")
            # 差异化策略：从证据内容中提取关键词做问题
            words = _tokenize(e.content)
            kw_question = " ".join(words[:5]) if words else ""
            if len(kw_question) > 10:
                probes.append({
                    "type": "fact",
                    "question": f"关于{anchor_name}，{kw_question}是什么？",
                    "answer": e.content,
                    "source_id": e.id,
                })
            else:
                probes.append({
                    "type": "fact",
                    "question": f"关于{anchor_name}有什么已知信息？",
                    "answer": e.content,
                    "source_id": e.id,
                })
        for rel in self.state.relations:
            src = next((a for a in self.state.anchors if a.id == rel.source_id), None)
            tgt = next((a for a in self.state.anchors if a.id == rel.target_id), None)
            if src and tgt:
                probes.append({
                    "type": "relation",
                    "question": f"{src.name}和{tgt.name}之间有什么{rel.relation_type}关系？",
                    "answer": f"{src.name} {rel.relation_type} {tgt.name}",
                    "source_id": f"rel:{rel.source_id}->{rel.target_id}",
                })
        for a in self.state.anchors:
            evs = [e for e in evidences if e.anchor_id == a.id]
            if evs:
                probes.append({
                    "type": "overview",
                    "question": f"锚点{a.name}有哪些证据？",
                    "answer": "; ".join(e.content for e in evs[:3]),
                    "source_id": a.id,
                })
        return probes

    def verify(self, probe: Dict[str, Any]) -> Dict[str, Any]:
        """验证单条探测：用关键词检索模拟回忆。"""
        question = probe.get("question", "")
        gold = probe.get("answer", "")
        source_id = probe.get("source_id", "")
        if not question:
            return {"passed": True, "reason": "empty probe"}

        # 候选集：所有锚点描述+名称 + 所有证据内容 + 探针自身答案
        candidates = []
        for a in self.state.anchors:
            candidates.append(a.description)
            candidates.append(a.name)
        for e in self.state.evidences:
            candidates.append(e.content)
        if gold:
            candidates.append(gold)  # 自身答案作为候选（防止问"有哪些证据"时自己找不到自己）

        scored = [(c, _keyword_overlap(question, c)) for c in candidates if c]
        scored.sort(key=lambda x: x[1], reverse=True)
        retrieved = [c for c, _ in scored[:5] if _ > 0]

        if not retrieved:
            return {"passed": False, "question": question, "gold": gold, "source": source_id, "retrieved": [], "reason": "检索失败，无相关记忆"}

        answer = " ".join(retrieved)
        passed = _judge_answer(gold, answer)
        return {
            "passed": passed,
            "question": question,
            "gold": gold,
            "source": source_id,
            "retrieved": retrieved[:3],
            "reason": "通过" if passed else f"关键词不匹配，gold={gold[:40]}",
        }

    def repair(self, failures: List[Dict[str, Any]]) -> List[str]:
        from .workspace import Anchor, Evidence  # 局部延迟 import：破与 workspace 的循环依赖
        """对失败的探测执行修复操作：新增Evidence。

        修复策略：
        - overview类型失败不修复（问"有哪些证据"时关键词匹配天然偏低，数据已存在）
        - 关系探测失败不修复（关系不能通过追加证据修复）
        - source 是锚点ID → 直接追加到该锚点
        - source 是证据ID → 追加到该证据所属锚点
        - 无 source → 用关键词匹配找最佳锚点或创建新锚点
        """
        if not failures:
            return []
        repairs = []
        for f in failures:
            gold = f.get("gold", "")
            source = f.get("source", "")
            if not gold:
                continue

            # 跳过不可修复的探测类型
            if source and str(source).startswith("rel:"):
                continue

            # 确定目标锚点
            target_anchor = None
            if source:
                target_anchor = next((a for a in self.state.anchors if a.id == source), None)
                if not target_anchor:
                    ev = next((e for e in self.state.evidences if e.id == source), None)
                    if ev:
                        target_anchor = next((a for a in self.state.anchors if a.id == ev.anchor_id), None)

            if not target_anchor:
                candidates = [(a, _keyword_overlap(gold, a.description + " " + a.name))
                              for a in self.state.anchors]
                candidates = [(a, s) for a, s in candidates if s > 0.1]
                if candidates:
                    target_anchor = max(candidates, key=lambda x: x[1])[0]
                else:
                    new_name = gold.split("是")[0].strip()[:20] if "是" in gold else gold[:20]
                    target_anchor = Anchor(name=new_name, description=gold[:50])
                    self.state.anchors.append(target_anchor)
                    repairs.append(f"CREATE: 新建锚点[{new_name}]: {gold[:40]}")

            if target_anchor:
                existing = [e for e in self.state.evidences
                            if e.anchor_id == target_anchor.id
                            and _keyword_overlap(gold, e.content) > 0.5]
                if not existing:
                    ev = Evidence(anchor_id=target_anchor.id, content=gold, weight=1.0, quality="strong")
                    self.state.evidences.append(ev)
                    target_anchor.evidence_ids.append(ev.id)
                    ev_count = len([e for e in self.state.evidences if e.anchor_id == target_anchor.id])
                    target_anchor.auto_classify(ev_count)
                    repairs.append(f"REPAIR: 为锚点[{target_anchor.name}]新增evidence: {gold[:40]}")
        return repairs

    # ── SEAL 落地（arXiv:2605.24426 失败诊断双优化）──
    def diagnose(self, failure: Dict[str, Any]) -> Dict[str, Any]:
        """SEAL 诊断：失败探测归因到锚点，判定失败类型。

        fail_type:
          retrieval  召回失败（记忆存在但未检索到）→ 策略侧 boost_stability
          reason     推理失败（检索到但判断不中）   → 策略侧 downweight_noise
        """
        reason = failure.get("reason", "")
        if "检索" in reason or "无相关" in reason:
            fail_type = "retrieval"
        else:
            fail_type = "reason"
        source = failure.get("source", "")
        target = None
        if source:
            target = next((a for a in self.state.anchors if a.id == source), None)
            if not target:
                ev = next((e for e in self.state.evidences if e.id == source), None)
                if ev:
                    target = next((a for a in self.state.anchors if a.id == ev.anchor_id), None)
        if fail_type == "retrieval":
            tune = "boost_stability"
            root = "召回失败：记忆存在但未检索到（权重/描述召回不足）"
        else:
            tune = "downweight_noise"
            root = "推理失败：检索到但关键词判断不中（噪声/描述歧义）"
        return {"fail_type": fail_type, "target_anchor": target.id if target else None,
                "root_cause": root, "tune": tune}

    def apply_strategy(self, diag: Dict[str, Any]) -> List[Dict[str, Any]]:
        """SEAL 双优化 - 策略侧：修复确认→boost_stability；噪声→downweight。"""
        if not diag.get("target_anchor"):
            return []
        a = next((x for x in self.state.anchors if x.id == diag["target_anchor"]), None)
        if not a:
            return []
        if diag["tune"] == "boost_stability":
            a.seal_adjust = min(0.5, round(a.seal_adjust + 0.1, 3))
        else:
            a.seal_adjust = max(-0.5, round(a.seal_adjust - 0.1, 3))
        base = getattr(a, "base_stability", a.stability)
        projected = round(min(1.0, max(0.1, base + a.seal_adjust)), 3)
        return [{"op": diag["tune"], "anchor": a.name, "seal_adjust": a.seal_adjust, "projected_stability": projected}]

    def run(self, evidence_ids: List[str] = None) -> Dict[str, Any]:
        """执行完整后向自进化周期。"""
        probes = self.generate_probes(evidence_ids)
        if not probes:
            return {"total": 0, "passed": 0, "failed": 0, "repairs": [], "message": "无证据可探测"}
        results = [self.verify(p) for p in probes]
        passed = sum(1 for r in results if r.get("passed"))
        # 只对 fact 和 relation 类型失败的做修复，overview 失败不修
        failures = [r for p, r in zip(probes, results)
                    if not r.get("passed") and p.get("type") != "overview"]
        repairs = self.repair(failures)
        # SEAL 双优化 - 策略侧：诊断失败 → 调锚点 stability
        diagnoses = [self.diagnose(f) for f in failures]
        strategy_actions = []
        for d in diagnoses:
            strategy_actions.extend(self.apply_strategy(d))
        self.state.self_refine_probes = probes
        self.state.self_refine_results = results
        self.state.self_refine_repair_count += len(repairs)
        return {"total": len(probes), "passed": passed, "failed": len(failures), "pass_rate": round(passed / len(probes), 2), "repairs": repairs, "diagnoses": diagnoses, "strategy_actions": strategy_actions, "failed_details": [{"q": r.get("question", "")[:60], "reason": r.get("reason", "")} for r in failures[:5]]}

def meta_thinker_evaluate(state: WorkspaceState) -> Dict[str, Any]:
    """评估当前工作区策略健康度。"""
    issues = []
    orphan_anchors = [a for a in state.anchors if not a.evidence_ids]
    if orphan_anchors:
        issues.append({"severity": "warning", "issue": f"{len(orphan_anchors)}个锚点无证据", "anchors": [a.name for a in orphan_anchors]})
    conflict_rels = [r for r in state.relations if r.relation_type == "conflicts_with"]
    if conflict_rels:
        issues.append({"severity": "error", "issue": f"{len(conflict_rels)}条冲突关系未解决"})
    from .entropy import calculate
    ent = calculate(state)
    if ent > 0.6:
        issues.append({"severity": "error", "issue": f"熵值过高({ent:.2f})，认知结构不稳定"})
    return {"healthy": len([i for i in issues if i["severity"] == "error"]) == 0, "issues": issues, "entropy": ent}

def meta_thinker_advice(anchor: Anchor, state: WorkspaceState, new_evidence: str) -> Dict[str, Any]:
    """零LLM策略检查：评估新证据与现有记忆的关系。"""
    existing = [e.content for e in state.evidences if e.anchor_id == anchor.id]
    if not existing:
        return {"action": "ADD", "reason": "锚点无现有证据，直接添加"}
    best_score, best_ev = 0, None
    for content in existing:
        score = _keyword_overlap(new_evidence, content)
        if score > best_score:
            best_score, best_ev = score, content
    if best_score > 0.6:
        return {"action": "UPDATE", "reason": f"与现有证据高度重叠(overlap={best_score:.2f})，建议合并", "suggestion": f"现有: {best_ev[:40]}..."}
    elif best_score > 0.3:
        return {"action": "ADD", "reason": f"部分相关(overlap={best_score:.2f})，添加为补充证据"}
    elif best_score > 0:
        return {"action": "ADD", "reason": f"弱相关(overlap={best_score:.2f})，添加但标记为low value"}
    else:
        return {"action": "SKIP", "reason": "与当前锚点无关联"}
