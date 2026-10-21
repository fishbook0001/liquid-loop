#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""decision_advisor — 液环决策顾问（旁路·fail-open·advisory-only）

封装本机 AgentJev-0.6B System-1 决策服务（127.0.0.1:8149），为液环
governance（删除/溶解/取代）、结晶（成核/冲突）、去冗余（召回去重）
提供语义二阶判断建议。

铁律（与液环 proprioception_probe 同范式）：
  1. advisory-only：返回值只作「建议」，绝不阻断原规则路径。
  2. fail-open：服务不可达/超时/解析失败一律返回 None，
     调用方必须把 None 视为「模型弃权，走纯规则」。
  3. 零核心侵入：本模块独立于 liquid_loop 包，可整目录删除撤除。

接口契约（实测 agentjev.decision.v1，来源 jev_service/contract.py）：
  POST /api/evaluate {"state": str, "questions": [...]}
    boolean: 可选 criteria{true,false}；返回 answer{probability=P(true), value=bool}
    choice : options 为 dict{key:desc} 或 list；返回 answer{value=best_key,
             top_probability, margin, distribution}
    score  : levels 必填 2..10 有序描述；返回 answer{score=期望索引,
             level=argmax索引, legend}
  ⚠️ state 与每个 candidate 编码上限 2048 tokens（超限 400 且不截断），
     本封装对输入做 1600 字符硬截断保护。
"""
import json
import urllib.request

__all__ = ["AgentJevAdvisor"]

# 单段文本字符上限：中文 ≈1 token/字，给 [STATE]/[QUESTION]/[CANDIDATE]
# 前缀与英文余量留出空间，防 contract.encode_paths 400。
_CLIP = 1600


def _clip(text, limit=_CLIP):
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class AgentJevAdvisor:
    """AgentJev 决策服务旁路封装。所有公开方法 fail-open：异常返回 None。"""

    def __init__(self, host="127.0.0.1", port=8149, timeout=8.0, enabled=True):
        self.base = f"http://{host}:{port}"
        self.timeout = float(timeout)
        self.enabled = bool(enabled)
        self.last_error = None  # 最近一次失败原因（供诊断，不抛出）

    # ------------------------------------------------------------------ #
    # 底层
    # ------------------------------------------------------------------ #
    def _post(self, state, questions):
        """POST /api/evaluate。任何失败返回 None（fail-open），不抛异常。"""
        if not self.enabled:
            self.last_error = "disabled"
            return None
        payload = json.dumps(
            {"state": _clip(state), "questions": questions},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/api/evaluate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            answers = (data.get("results") or [{}])[0].get("answers") or []
            return {"answers": {a.get("id"): a for a in answers},
                    "wall_ms": (data.get("usage") or {}).get("wall_ms")}
        except Exception as exc:  # noqa: BLE001 — fail-open 是铁律
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def health(self):
        """GET /health。返回 dict 或 None。"""
        if not self.enabled:
            return None
        try:
            with urllib.request.urlopen(self.base + "/health",
                                        timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    # ------------------------------------------------------------------ #
    # 三类决策原语（advisory）
    # ------------------------------------------------------------------ #
    def boolean(self, state, question, qid="b1"):
        """二值语义判断。返回 {"prob_true","value","wall_ms","raw"} 或 None。

        用途示例：两条记忆是否语义冲突 / 该证据是否足以成核 / 该删除是否安全。
        """
        resp = self._post(state, [{"id": qid, "type": "boolean",
                                   "question": _clip(question)}])
        if resp is None:
            return None
        a = resp["answers"].get(qid)
        if not a or "probability" not in a:
            self.last_error = f"unexpected boolean answer: {a!r}"
            return None
        return {"prob_true": float(a["probability"]),
                "value": bool(a.get("value")),
                "wall_ms": resp.get("wall_ms"), "raw": a}

    def choice(self, state, question, options, qid="c1"):
        """多选一语义决策。options: {key: 描述} 或 [描述,...]。

        返回 {"best","description","top_probability","margin","probs",
              "wall_ms","raw"} 或 None。
        用途示例：冗余证据应 merge/keep/supersede 哪种治理动作。
        """
        if isinstance(options, dict):
            opts = {str(k): _clip(v) for k, v in options.items()}
        else:
            opts = {str(i): _clip(v) for i, v in enumerate(options)}
        if not 2 <= len(opts) <= 255:
            self.last_error = "choice requires 2..255 options"
            return None
        resp = self._post(state, [{"id": qid, "type": "choice",
                                   "question": _clip(question),
                                   "options": opts}])
        if resp is None:
            return None
        a = resp["answers"].get(qid)
        if not a or "value" not in a:
            self.last_error = f"unexpected choice answer: {a!r}"
            return None
        return {"best": a.get("value"),
                "description": a.get("description"),
                "top_probability": a.get("top_probability"),
                "margin": a.get("margin"),
                "probs": a.get("distribution"),
                "wall_ms": resp.get("wall_ms"), "raw": a}

    def score(self, state, question, levels, qid="s1"):
        """校准评分（levels 2..10 有序描述，索引即等级）。

        返回 {"score","level","legend","wall_ms","raw"} 或 None。
        用途示例：证据对稳态的贡献度 / 治理操作风险等级。
        """
        levels = [_clip(v) for v in levels]
        if not 2 <= len(levels) <= 10:
            self.last_error = "score requires 2..10 levels"
            return None
        resp = self._post(state, [{"id": qid, "type": "score",
                                   "question": _clip(question),
                                   "levels": levels}])
        if resp is None:
            return None
        a = resp["answers"].get(qid)
        if not a or "score" not in a:
            self.last_error = f"unexpected score answer: {a!r}"
            return None
        return {"score": a.get("score"), "level": a.get("level"),
                "legend": a.get("legend"),
                "wall_ms": resp.get("wall_ms"), "raw": a}


# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    adv = AgentJevAdvisor()
    h = adv.health()
    print("health:", json.dumps(h, ensure_ascii=False)[:300] if h else f"DOWN ({adv.last_error})")
    if h:
        r = adv.boolean(
            "Test results: 23 passed, 2 failed.",
            "Has the task been fully completed (whole suite passing)?")
        print("boolean smoke:", r and {k: r[k] for k in ("prob_true", "value", "wall_ms")})
