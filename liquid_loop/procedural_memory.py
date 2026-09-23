"""程序性记忆层（distill_registry 实现 · D3 + 几轮军师调研蒸馏整合）

蒸馏这几轮调研的「必要内容」，落地为液环一等公民组件：

  - PlugMem(#124 / #170 第二信源): procedural(行动处方)记忆一等公民，按任务召回
      → 本模块把「技能/配方」作为独立记忆类型，与声明式 Anchor/Evidence/Memory 正交
  - Skill1(#169): 单一任务信号拆三份 → 此处落地为「选择/利用/蒸馏」三类信用：
      选择信用 = 任务标签匹配度；利用信用 = 复用成功率；蒸馏信用 = 改进增量
  - EvoC2F(#172): 验证门控技能进化(功能测试+契约验证+回归评估)+分阶段部署
      (shadow→canary→active) + 不可逆操作交人工(↔ ops_gate 不可逆确认)
  - RE-TRAC(#165, 已落 structured_note): 技能笔记用 {answer, evidence, open} 三组分

守液环铁律：
  - 禁向量：任务路由用结构化 tag 精确/子串匹配，绝不 cosine / embedding
  - 提取式/确定性：准入判定为确定性规则，零 LLM 依赖
  - fail-open：任何异常返回安全默认(空列表/降级状态)，不阻断主流程
  - 零丢失：被拒/降级的技能仍保留(gate_status=rejected)，可审计、可解冻

持久化：sidecar `.liquid/procedural.json`，独立 fcntl 锁，不触碰 WorkspaceState 序列化
（与 storage.py 同风格，但独立文件避免扩大 WorkspaceState 改动面）。
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

from .context_compress import structured_note
from .workspace import now


# ── 阈值（环境变量可覆写，缺省保守）─────────────────────────────────────
def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, default))
        return v
    except (TypeError, ValueError):
        return default


REGRESSION_EPS = _env_float("LIQUID_PROCMEM_REGRESSION_EPS", 0.6)  # 回归门：成功率≥此值才晋级
MIN_USES_SHADOW = max(1, int(_env_float("LIQUID_PROCMEM_MIN_USES", 3)))  # 出 shadow 最少使用次数(≥1)
MIN_USES_ACTIVE = MIN_USES_SHADOW * 2                               # 出 canary 进 active 所需使用次数

_ACTION_VERB = re.compile(
    r"(?i)\b(run|exec|invoke|call|use|apply|send|start|build|deploy|install|"
    r"query|fetch|write|read|patch|create|delete|test|verify|extract)\b"
)


@dataclass
class ProceduralMemory:
    """一条程序性记忆（行动处方）。

    与声明式 Memory(结晶自≥2一致证据) 正交：这是「怎么做」而非「是什么」。
    """

    skill_id: str = ""
    task_tags: list = field(default_factory=list)        # 结构化任务标签(非向量)；选择信用匹配键
    invocation: str = ""                                 # 行动处方(how-to/命令/配方)
    evidence: str = ""                                   # 成功证据/来源
    note: dict = field(default_factory=lambda: {"answer": [], "evidence": [], "open": []})  # RE-TRAC 三组分
    gate_status: str = "pending"                         # pending→shadow→canary→active | rejected
    admit_score: float = 0.0                             # EvoC2F 三门前综合分(0~1)
    use_count: int = 0
    success_count: int = 0
    contract: dict = field(default_factory=dict)         # {inputs, outputs} 契约(门2 校验依据)
    irreversible: bool = False                           # 不可逆操作→交人工(↔ ops_gate)
    requires_human: bool = False                         # 不可逆技能晋级到 canary 后需人工放行
    created_at: str = field(default_factory=now)
    last_used_at: str = ""

    # ── 派生指标 ──
    @property
    def success_rate(self) -> float:
        if self.use_count == 0:
            return 0.0
        return self.success_count / self.use_count

    @property
    def usable(self) -> bool:
        """是否可被任务召回（降级/待审不可召回）。"""
        return self.gate_status in ("active", "canary")

    def as_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "task_tags": list(self.task_tags),
            "invocation": self.invocation,
            "evidence": self.evidence,
            "note": self.note,
            "gate_status": self.gate_status,
            "admit_score": round(self.admit_score, 3),
            "use_count": self.use_count,
            "success_count": self.success_count,
            "success_rate": round(self.success_rate, 3),
            "contract": self.contract,
            "irreversible": self.irreversible,
            "requires_human": self.requires_human,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }


class ProceduralRegistry:
    """程序性记忆注册表：验证门控准入 + 分阶段部署 + 任务路由召回。

    fail-open：load/save/admit/recall 任意异常均返回安全默认，不抛出。
    独立 sidecar 持久化，不依赖 WorkspaceState。
    """

    STORE_NAME = "procedural.json"

    def __init__(self, workspace_root: Path):
        self.root = Path(workspace_root)
        self.path = self.root / ".liquid" / self.STORE_NAME
        self._items: dict = {}
        self._load()

    # ── 持久化（fcntl 排他锁，与 storage.py 同风格）─────────────────────
    def _load(self) -> None:
        try:
            if self.path.exists():
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                _fields = {f.name for f in fields(ProceduralMemory)}
                self._items = {
                    sid: ProceduralMemory(**{k: v for k, v in it.items() if k in _fields})
                    for sid, it in data.get("skills", {}).items()
                }
        except Exception:
            self._items = {}

    def _save(self) -> bool:
        """原子写 sidecar；返回是否成功（失败不阻断，但调用方可感知）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"skills": {sid: it.as_dict() for sid, it in self._items.items()}}
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False  # fail-open：持久化失败不阻断，但结果带 warning

    def _with_lock(self, fn):
        """对 sidecar 加排他锁后执行 fn，保证 load→modify→save 原子。

        非阻塞锁 + fail-open：锁被占用(竞争/陈旧进程)时立即返回错误，
        绝不阻塞调用方(否则会拖垮整个 HTTP 端点)。这符合液环 fail-open 铁律。
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 锁句柄跨整段 load→fn→save 持有，由下方 finally 手动 LOCK_UN+close；
            # 不能用 with（with 会在块尾提前释放锁，失去原子语义）。
            lk = open(self.path.with_suffix(".lock"), "w")  # noqa: SIM115
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"ok": False, "error": "lock busy (fail-open, 重试)"}
            try:
                self._load()
                out = fn()
                saved = self._save()
                if isinstance(out, dict) and out.get("ok") and not saved:
                    out = {**out, "warning": "持久化失败(内存已更新，未落盘)"}
                return out
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
                lk.close()
        except Exception as e:
            return {"ok": False, "error": f"fail-open: {e}"}

    # ── EvoC2F 三关准入 ──────────────────────────────────────────────
    def _gate_functional(self, invocation: str) -> tuple:
        """门1 功能测试(本地代理)：行动处方非空且含可行动词/足够长度。"""
        if not invocation or len(invocation.strip()) < 8:
            return 0.0, "invocation 过短或为空"
        has_verb = bool(_ACTION_VERB.search(invocation))
        return (0.7 if has_verb else 0.4), ("含可行动词" if has_verb else "缺可行动词(弱)")

    def _gate_contract(self, evidence: str, contract) -> tuple:
        """门2 契约验证(本地代理)：若提供契约，校验证据涵盖声明输出键。"""
        if not isinstance(contract, dict) or not contract:
            return 1.0, "无契约声明→直接通过"
        outs = contract.get("outputs", [])
        if not outs:
            return 1.0, "契约无输出声明"
        if not evidence:
            return 0.0, "有契约但缺证据可校验"
        hit = sum(1 for o in outs if o and o in evidence)
        frac = hit / len(outs)
        ok = frac >= 0.5
        return (frac, f"证据涵盖 {hit}/{len(outs)} 输出键" + ("" if ok else "→不足"))

    def _gate_regression(self, use_count: int, success_rate: float) -> tuple:
        """门3 回归评估(本地代理)：无使用数据前不可定级，置 shadow。"""
        if use_count == 0:
            return 0.0, "尚无使用数据→置 shadow 观察"
        ok = success_rate >= REGRESSION_EPS
        return (success_rate, f"成功率 {success_rate:.2f} " + ("≥阈值" if ok else "<阈值"))

    def admit(self, skill_id: str, task_tags: list, invocation: str,
              evidence: str = "", contract: dict | None = None,
              irreversible: bool = False) -> dict:
        """准入一条程序性记忆，跑 EvoC2F 三关 + 分阶段部署。

        返回 {ok, skill_id, gates, gate_status, admit_score}。fail-open。
        已 active/canary 的 skill 重准入时**保留晋级进度与复用历史**（仅更新内容字段），
        不把已验证技能降回 shadow；shadow/rejected 重准入视为新尝试。
        """
        def _do():
            if contract is not None and not isinstance(contract, dict):
                return {"ok": False, "error": "contract 必须是 dict 对象"}
            existing = self._items.get(skill_id)
            f_s, f_msg = self._gate_functional(invocation)
            c_s, c_msg = self._gate_contract(evidence, contract or {})
            r_s, r_msg = self._gate_regression(0, 0.0)
            # 三关加权：功能 0.4 / 契约 0.3 / 回归(初始 0) 0.3 → 初始分由功能+契约决定
            admit_score = round(0.4 * f_s + 0.3 * c_s + 0.3 * 0.0, 3)
            # 功能不过 → 直接 rejected（脏技能污染防护，↔ Voyager 脏技能）
            status = "rejected" if f_s < 0.4 else "shadow"
            # 已验证技能(active/canary)重准入 → 保留晋级进度与历史
            preserved = bool(existing and existing.gate_status in ("active", "canary"))
            if preserved:
                status = existing.gate_status
            note = structured_note([evidence]) if evidence else {"answer": [], "evidence": [], "open": []}
            pm = ProceduralMemory(
                skill_id=skill_id,
                task_tags=list(task_tags or []),
                invocation=invocation,
                evidence=evidence,
                note=note,
                gate_status=status,
                admit_score=admit_score,
                use_count=existing.use_count if preserved else 0,
                success_count=existing.success_count if preserved else 0,
                contract=dict(contract or {}),
                irreversible=bool(irreversible),
                requires_human=bool(irreversible) or bool(existing and existing.requires_human),
                created_at=existing.created_at if existing else now(),
                last_used_at=existing.last_used_at if preserved else "",
            )
            self._items[skill_id] = pm
            return {
                "ok": True,
                "skill_id": skill_id,
                "gates": {
                    "functional": {"score": round(f_s, 3), "msg": f_msg},
                    "contract": {"score": round(c_s, 3), "msg": c_msg},
                    "regression": {"score": round(r_s, 3), "msg": r_msg},
                },
                "gate_status": status,
                "admit_score": admit_score,
            }
        return self._with_lock(_do)

    # ── 晋级/回退（EvoC2F 分阶段部署 + 回归门控）────────────────────
    def _recompute_stage(self, pm: ProceduralMemory) -> None:
        if pm.gate_status in ("pending", "rejected"):
            return
        if pm.gate_status == "shadow" and pm.use_count >= MIN_USES_SHADOW:
            if pm.success_rate >= REGRESSION_EPS:
                pm.gate_status = "canary"  # 不可逆也到 canary（封顶，需人工放行）
            else:
                pm.gate_status = "rejected"  # 回归不达标→拒绝(脏技能防护)
        elif pm.gate_status == "canary" and pm.use_count >= MIN_USES_ACTIVE:
            if pm.success_rate >= REGRESSION_EPS:
                if not pm.irreversible:
                    pm.gate_status = "active"
            else:
                pm.gate_status = "rejected"  # canary 后期回归→拒绝(防已放行技能劣化)

    def record_use(self, skill_id: str, success: bool) -> dict:
        """记录一次复用结果，自动触发晋级判定。fail-open。"""
        def _do():
            pm = self._items.get(skill_id)
            if pm is None:
                return {"ok": False, "error": "unknown skill_id"}
            pm.use_count += 1
            if success:
                pm.success_count += 1
            pm.last_used_at = now()
            self._recompute_stage(pm)
            return {"ok": True, "skill_id": skill_id,
                    "gate_status": pm.gate_status,
                    "success_rate": round(pm.success_rate, 3),
                    "requires_human": pm.requires_human}
        return self._with_lock(_do)

    def promote(self, skill_id: str, by: str = "human") -> dict:
        """人工放行晋级（不可逆技能封顶 canary 后由 ops_gate 同构的人工确认）。"""
        def _do():
            pm = self._items.get(skill_id)
            if pm is None:
                return {"ok": False, "error": "unknown skill_id"}
            if pm.irreversible and pm.gate_status == "canary":
                pm.requires_human = False
                pm.gate_status = "active"
                return {"ok": True, "skill_id": skill_id,
                        "gate_status": "active", "promoted_by": by}
            return {"ok": False, "error": f"不可晋级(irreversible={pm.irreversible},status={pm.gate_status})"}
        return self._with_lock(_do)

    # ── Skill1 任务路由召回（禁向量：结构化 tag 匹配 + 三信用排序）──
    @staticmethod
    def _tag_match(task_query: str, tags: list) -> float:
        """结构化匹配：task_query 子串命中多少 tag（非向量）。"""
        if not task_query or not tags:
            return 0.0
        q = task_query.lower()
        hit = sum(1 for t in tags if t and t.lower() in q)
        return hit / len(tags)

    def recall_for_task(self, task_query: str, top_k: int = 3) -> list:
        """按任务召回可复用程序性记忆。

        候选=usable 技能(active/canary)；排序分 = 选择信用(tag匹配)*0.5
        + 利用信用(成功率×时效)*0.5。禁向量，确定性。fail-open 返回 []。
        """
        try:
            cands = [pm for pm in self._items.values() if pm.usable]
            scored = []
            for pm in cands:
                sel = self._tag_match(task_query, pm.task_tags)        # 选择信用
                recency = 1.0 if not pm.last_used_at else 0.8          # 简化时效因子
                util = pm.success_rate * recency                        # 利用信用
                score = 0.5 * sel + 0.5 * util
                if sel <= 0 and util <= 0:
                    continue  # 既不匹配也无复用记录→不召回(避免噪声)
                scored.append((score, pm))
            scored.sort(key=lambda x: (-x[0], -x[1].success_rate, x[1].skill_id))
            return [pm.as_dict() | {"match_score": round(s, 3)} for s, pm in scored[:top_k]]
        except Exception:
            return []

    def list_all(self, include_rejected: bool = False) -> list:
        try:
            items = self._items.values()
            if not include_rejected:
                items = [p for p in items if p.gate_status != "rejected"]
            return [p.as_dict() for p in items]
        except Exception:
            return []


# ── 便捷函数（供 server / CLI 直接调用）────────────────────────────────
def procmem_recall(workspace_root: Path, task_query: str, top_k: int = 3) -> list:
    return ProceduralRegistry(workspace_root).recall_for_task(task_query, top_k)


def procmem_admit(workspace_root: Path, skill_id: str, task_tags: list,
                  invocation: str, evidence: str = "", contract: dict | None = None,
                  irreversible: bool = False) -> dict:
    return ProceduralRegistry(workspace_root).admit(
        skill_id, task_tags, invocation, evidence, contract, irreversible)
