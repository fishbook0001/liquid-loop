"""技能改进回路原型（SkillX Iterative Refinement × 液环误差回路哲学）。

验证机制：执行失败（预测误差）→ 失败上下文累积（note.open）→ 连续失败触发修订
→ 修订重置 shadow 重新验证 → 晋级循环。零向量零 LLM，纯结构化。
不碰生产 procedural_memory.py；此原型证明机制可行性。
"""
from __future__ import annotations

REGRESSION_EPS = 0.6     # 晋级成功率阈值（对齐生产）
MIN_USES_SHADOW = 3      # shadow 出观察期最少使用次数
MIN_USES_CANARY = 6      # canary 进 active 使用次数


class SkillUnit:
    def __init__(self, skill_id: str, invocation: str, task_tags: list[str]):
        self.skill_id = skill_id
        self.invocation = invocation          # 行动处方（修订后更新）
        self.task_tags = task_tags
        self.use_count = 0
        self.success_count = 0
        self.fail_contexts: list[str] = []    # note.open 区：结构化失败上下文
        self.gate_status = "shadow"           # shadow/canary/active/rejected
        self.refine_count = 0
        self.history: list[dict] = []         # 审计：每次使用的记录

    @property
    def success_rate(self) -> float:
        return self.success_count / self.use_count if self.use_count else 0.0

    def usable(self) -> bool:
        return self.gate_status in ("active", "canary")


class SkillRefineLoop:
    def __init__(self, fail_threshold: int = 3):
        self.skills: dict[str, SkillUnit] = {}
        self.fail_threshold = fail_threshold
        self.refine_events: list[dict] = []   # 修订事件审计

    def admit(self, skill_id: str, invocation: str, task_tags: list[str]) -> dict:
        self.skills[skill_id] = SkillUnit(skill_id, invocation, task_tags)
        return {"ok": True, "skill_id": skill_id, "gate_status": "shadow"}

    def record_use(self, skill_id: str, success: bool, failure_context: str = "") -> dict:
        """记录一次使用。失败时把结构化上下文追加到 note.open（不吞错）。"""
        s = self.skills[skill_id]
        s.use_count += 1
        if success:
            s.success_count += 1
        else:
            s.fail_contexts.append(failure_context or "no_context")
        s.history.append({"n": s.use_count, "success": success, "ctx": failure_context or ""})
        stage_before = s.gate_status
        self._recompute(s)
        return {"ok": True, "skill_id": skill_id, "gate_status": s.gate_status,
                "stage_before": stage_before, "success_rate": round(s.success_rate, 3)}

    def needs_refinement(self) -> list[str]:
        """连续失败 ≥ 阈值 且 未被拒 → 修订候选（误差信号：技能需要局部更新）。"""
        return [s.skill_id for s in self.skills.values()
                if len(s.fail_contexts) >= self.fail_threshold
                and s.gate_status != "rejected"]

    def refine(self, skill_id: str, new_invocation: str, evidence: str = "") -> dict:
        """修订：更新行动处方 + 重置 shadow 重新验证（失败反馈回流技能本体）。"""
        s = self.skills[skill_id]
        old_inv = s.invocation
        s.invocation = new_invocation
        s.refine_count += 1
        s.gate_status = "shadow"
        s.fail_contexts = []          # 清空旧失败（修订后重新累积）
        s.use_count = 0
        s.success_count = 0
        self.refine_events.append({
            "skill_id": skill_id, "refine_count": s.refine_count,
            "old": old_inv[:40], "new": new_invocation[:40], "evidence": evidence[:60],
        })
        return {"ok": True, "skill_id": skill_id, "gate_status": "shadow",
                "refine_count": s.refine_count}

    def _recompute(self, s: SkillUnit) -> None:
        """对齐生产分阶段部署：shadow≥3次且成功率≥0.6→canary；canary≥6次→active；不达标→rejected。"""
        if s.gate_status in ("pending", "rejected"):
            return
        if s.gate_status == "shadow" and s.use_count >= MIN_USES_SHADOW:
            s.gate_status = "canary" if s.success_rate >= REGRESSION_EPS else "rejected"
        elif s.gate_status == "canary" and s.use_count >= MIN_USES_CANARY:
            s.gate_status = "active" if s.success_rate >= REGRESSION_EPS else "rejected"


def selftest():
    """场景：技能"部署到生产"。修订前 3 连败触发修订，修订后 4/4 成功晋级 active。"""
    loop = SkillRefineLoop(fail_threshold=3)
    loop.admit("deploy", "ssh host; npm run deploy", ["deploy", "production"])
    s = loop.skills["deploy"]

    # 阶段1：2 次成功 + 3 次失败（端口冲突）→ 触发修订
    for i, ok in enumerate([True, True, False, False, False]):
        st = loop.record_use("deploy", ok, "port_8080_conflict" if not ok else "")
        print(f"  use#{i+1} ok={ok} stage={st['gate_status']}")
    print(f"  needs_refinement: {loop.needs_refinement()}  (期望 ['deploy'])")
    assert loop.needs_refinement() == ["deploy"], "失败累积未触发修订"

    # 阶段2：修订（加端口检查步骤）
    loop.refine("deploy", "lsof -i:8080 || ssh host; npm run deploy", "added port check")
    print(f"  修订后 stage={s.gate_status} refine_count={s.refine_count} fail_ctx={len(s.fail_contexts)}")
    assert s.gate_status == "shadow" and s.refine_count == 1

    # 阶段3：修订后 6 次成功 → canary → active
    for i in range(6):
        st = loop.record_use("deploy", True)
        print(f"  修订use#{i+1} ok=True stage={st['gate_status']}")
    print(f"  最终 stage={s.gate_status} success_rate={s.success_rate:.2f}")
    assert s.gate_status == "active", "修订后应晋级 active"

    # 对照：不修订的静态技能在 canary 重评点被拒绝（2 成 4 败，success_rate=0.33<0.6）
    loop2 = SkillRefineLoop()
    loop2.admit("deploy_static", "ssh host; npm run deploy", ["deploy"])
    for ok in [True, True, False, False, False, False]:
        loop2.record_use("deploy_static", ok, "port_conflict")
    s2 = loop2.skills["deploy_static"]
    print(f"\n对照(不修订): stage={s2.gate_status} success_rate={s2.success_rate:.2f}  (期望 rejected)")
    assert s2.gate_status == "rejected", "静态技能应 rejected（canary 重评点）"
    print("\n✓ 技能改进回路验证通过：修订救回(canary 期前)→active；静态对照 canary 重评→rejected")


if __name__ == "__main__":
    selftest()
