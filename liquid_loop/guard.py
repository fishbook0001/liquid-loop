"""蒸馏 #197 + #198 守卫原语：能力菜单 + 升级刹车 + 确认闸。

#197 MCP 安全边界：能力描述/标签只是提示语，不是沙箱；隔离靠部署层。
      → CapabilityMenu：声明带名字/参数/风险等级的能力；高危调用前插确认闸。
#198 Agent 停下问人：升级条件 = 低置信 ∨ 不可逆 ∨ 超预算；审批=方向盘。
      → should_escalate(confidence, irreversible, over_budget) + confirm_gate。

液环 cli 已有 --force 确认雏形（cpe_check）；本模块提供通用原语，供破坏性操作接入，
让「动作前问人」成为可配置策略而非散落各处的硬编码。零依赖：仅标准库。
"""
from __future__ import annotations

RISK_LEVELS = ("low", "medium", "high")


def validate_content(content: str) -> str | None:
    """内容质量 ValidationRule（08-18 审计补盲）：挡无意义/垃圾写入。

    返回拒绝原因字符串；通过返回 None。
    规则（纯规则零依赖，只挡"无意义"，不评"价值"）：
      1) 空内容；
      2) 无字母/汉字 且 过短(<8) 的纯占位/符号噪音（如 "1"、"!"、"!!!"）；
      3) 字符多样性过低(熵代理)的重复噪音（如 "aaaaaa"、"111111"）。
    单点维护：server ll_remember 与 workspace.add_evidence 共用本函数。
    """
    c = (content or "").strip()
    if not c:
        return "content 为空: 疑似无意义写入"
    has_text = any(not ch.isdigit() and ch.isalnum() for ch in c)
    if not has_text and len(c) < 8:
        return "content 无文字内容且过短: 疑似占位噪音"
    if len(set(c)) / len(c) < 0.2:
        return "content 字符多样性过低(熵代理): 疑似重复噪音"
    return None


# ── 反绝对化词表（m60 DeepTutor 借鉴落地 · 2026-08-29）──
# DeepTutor 训练师提示词要求"避免绝对化、过度自信表述"（不/不要/绝不/总是/完全/彻底/
# 专家/热爱/恨 等），并配引用池约束（结论必须能锚定到已蒸馏引文）。
# 液环落地为「警告级」检测：只标记、不拒绝——液环允许主体自由表达，但把过度概括
# 显式暴露给写入方（og_flags），供上层蒸馏/内化时复核真实性，而非静默放行。
OVERGEN_TERMS: tuple[tuple[str, str], ...] = (
    # (命中词, 类别)
    ("总是", "absolute"), ("从来不", "absolute"), ("从不", "absolute"), ("永远", "absolute"),
    ("所有", "absolute"), ("全部", "absolute"), ("一切", "absolute"), ("任何", "absolute"),
    ("完全", "absolute"), ("彻底", "absolute"), ("绝对", "absolute"), ("一定", "absolute"),
    ("必须", "prescriptive"), ("禁止", "prescriptive"), ("绝不", "absolute"),
    ("专家", "claim"), ("大师", "claim"), ("完美", "claim"), ("精通", "claim"),
    ("热爱", "claim"), ("深爱", "claim"), ("憎恨", "claim"), ("讨厌", "claim"),
    ("always", "absolute"), ("never", "absolute"), ("every", "absolute"),
    ("all", "absolute"), ("none", "absolute"), ("completely", "absolute"),
    ("totally", "absolute"), ("absolutely", "absolute"), ("perfectly", "claim"),
    ("expert", "claim"), ("master", "claim"), ("perfect", "claim"),
    ("love", "claim"), ("hate", "claim"), ("passion", "claim"),
    ("fully understands", "claim"), ("deeply", "claim"), ("truly", "claim"),
)


def overgeneralization_flags(content: str) -> list[dict]:
    """反绝对化词表检测（m60 建议2 落地）：返回命中的过度概括表述。

    返回 [{term, category}]，按出现顺序去重；无命中返回 []。
    只标记不拒绝（警告级）：液环允许自由表达，但显式暴露给上层复核。
    零依赖，纯规则，与 validate_content 同一风格。
    """
    c = (content or "").lower()
    flags: list[dict] = []
    seen: set = set()
    for term, cat in OVERGEN_TERMS:
        if term in c and term not in seen:
            seen.add(term)
            flags.append({"term": term, "category": cat})
    return flags


def should_escalate(confidence: float = 1.0, irreversible: bool = False,
                    over_budget: bool = False) -> bool:
    """升级刹车：命中任一红线即交回人。低置信 ∨ 不可逆 ∨ 超预算。

    全自动不是高级，敢于在关键时刻说"我需要你"才是设计出来的安全网。
    """
    return (confidence < 0.5) or bool(irreversible) or bool(over_budget)


class CapabilityMenu:
    """能力菜单：声明带风险等级的能力，隔离靠部署层而非标签。"""

    def __init__(self):
        self._caps: dict = {}

    def declare(self, name: str, risk: str = "low", description: str = "",
                 params: list | None = None) -> None:
        if risk not in RISK_LEVELS:
            raise ValueError(f"risk 必须是 {RISK_LEVELS}，收到 {risk!r}")
        self._caps[name] = {
            "risk": risk,
            "description": description,
            "params": params or [],
        }

    def risk_of(self, name: str) -> str | None:
        cap = self._caps.get(name)
        return cap["risk"] if cap else None

    def requires_confirm(self, name: str) -> bool:
        """high 风险能力调用前必须确认（对齐液环确认闸门）。"""
        return self.risk_of(name) == "high"

    def as_menu(self) -> dict:
        """供协议层展示的菜单（提示语，非沙箱）。"""
        return dict(self._caps)


def confirm_gate(action: str, risk: str = "high", auto_approve: bool = False) -> bool:
    """确认闸：high 风险且未 auto 确认 → 返回 False（拦截，交回人）。

    auto_approve=True 用于 dry-run / 测试；真实高危操作应默认拦截。
    返回 True=放行，False=拦截（需人工确认）。
    """
    if risk not in RISK_LEVELS:
        risk = "high"  # 未知风险按最高处置
    # 保留显式分支（不合并为 `return not (...)`）：确认闸的"先拦后放"语义
    # 需在源码上一眼可读，机械化简反而降低守卫逻辑的可审计性。
    if risk == "high" and not auto_approve:  # noqa: SIM103
        return False
    return True


class PerceptionGate:
    """门控-液环因果共生环的胶水层（“单终端机器人”概念落地件）。

    设计定位（克制原则：精选 4 模块，不重新膨胀）：
      - 输入候选感知动作 + 预算余量 + 灵敏度，输出 allow / degrade / block + 理由。
      - 内部消费：
          1) guard.should_escalate —— 不可逆变 + 低置信 → 交回人（block）。
          2) 因果边（workspace.causal：enables/causes/contradicts）→ 「因果核心永饿死」
             豁免：命中因果核心的动作即便超预算也放行，保证液环主链路不被闸饿死。
          3) adaptive_recall 的负载双模（load<0.5 冗余验证 / ≥0.5 分工扩容）→
             超预算时按当前认知负载决定 degrade（省算力）还是 allow（精度优先）。
          4) build_or_cache（rar 本地索引缓存）→ 本地算力溶解「重建索引」成本，
             使高载降级 skip 无后顾之忧（详见测试 test_perception_gate.py）。
          5) env_anchor_probe（v3.2 环境锚时效门，可选）→ 涉及环境类断言的动作
             使用前强制重取证（curl/ps/ipconfig/hostname 正对照），返回 stale 即 block，
             把"认知基线纪律"升为机制强制步；无注入则跳过（零影响）。

    零依赖：真实因果边 / 负载 / 环境锚重取证来自 liquid-loop 的 workspace / recall_filter / rar
    或调用方注入，由 adapter 注入（causal_core_predicate / load_probe / env_anchor_probe），
    不在本模块 import 重型依赖。这样门控成为「可配置策略」，而非把整套液环塞进门里的膨胀方案。
    """

    def __init__(self, budget: float = 1.0, sensitivity: str = "normal",
                 causal_core_predicate=None, load_probe=None, env_anchor_probe=None):
        self.budget = float(budget)
        self.sensitivity = sensitivity
        self._spent = 0.0
        # 默认谓词：无注入时一律非因果核心（保守，闸正常生效）
        self.causal_core_predicate = causal_core_predicate or (lambda action: False)
        # 默认负载探测器：无注入时返回 0.0（低载，精度优先路径）
        self.load_probe = load_probe or (lambda: 0.0)
        # v3.2 环境锚时效门：无注入时返回 None（跳过，零影响现有门）
        self.env_anchor_probe = env_anchor_probe

    def decide(self, action, cost: float = 0.0, confidence: float = 1.0,
               irreversible: bool = False) -> dict:
        """对一条候选感知动作做门控裁决。

        返回 dict: {"decision": allow|degrade|block, "reason": str, ...}
          - block    : 需人工确认（交回人），不消耗预算。
          - allow    : 放行（因果核心豁免 / 正常 / 低载精度优先）。
          - degrade  : 超预算 + 高载 → 降采样 / 跳过，省算力；不消耗预算。
        """
        # 0) v3.2 环境锚时效门（最高优先）：涉及环境类断言须先重取证，stale 即拦截
        if self.env_anchor_probe is not None:
            try:
                _ap = self.env_anchor_probe(action)
            except Exception:
                _ap = None  # 重取证异常 fail-open，不误伤（零爆破半径）
            if _ap is True or (isinstance(_ap, dict) and _ap.get("stale")):
                return {
                    "decision": "block",
                    "reason": f"环境锚时效门: 须重取证 ({_ap.get('detail','') if isinstance(_ap, dict) else ''})",
                    "env_anchor_stale": True,
                }

        # 1) 不可逆变 + 低置信 → 交回人（should_escalate 红线）
        if should_escalate(confidence=confidence, irreversible=irreversible):
            return {
                "decision": "block",
                "reason": "irreversible+low_confidence→需人工确认",
                "escalate": True,
            }

        # 2) 因果核心 → 永放行（never starve causal core）
        if self.causal_core_predicate(action):
            return {
                "decision": "allow",
                "reason": "因果核心动作永放行（保护液环主链路）",
                "causal_core": True,
            }

        # 3) 预算检查
        remaining = self.budget - self._spent
        if cost > remaining:
            load = float(self.load_probe())
            if load >= 0.5:
                # 高载：降级 skip / 降采样，省算力
                # （rar.build_or_cache 已本地化索引重建成本，skip 无后顾之忧）
                return {
                    "decision": "degrade",
                    "reason": f"超预算+高载({load:.2f})→降采样/跳过省算力",
                    "load": load,
                    "over_budget": True,
                }
            # 低载：冗余验证，精度优先（仍消耗预算）
            self._spent += cost
            return {
                "decision": "allow",
                "reason": f"超预算+低载({load:.2f})→冗余验证精度优先",
                "load": load,
                "over_budget": True,
            }

        # 4) 正常放行
        self._spent += cost
        return {
            "decision": "allow",
            "reason": "正常放行",
            "load": float(self.load_probe()),
        }

    def remaining(self) -> float:
        """剩余预算。"""
        return self.budget - self._spent


# ── 评估准则：延迟跃变防误判（m69 过度递归衰减 + m65 世界模型自演化 蒸馏落地 · 2026-08-29）──
# 自演化系统最危险的评估错误是「过早判失败」：机制的效果常表现为延迟跃变
# （先震荡/持平，积累到阈值后跳变），若在跃变前按短期指标关停，就永远等不到
# 跃变。本准则供上层评测门控（如 SoMEval 接入的评估门）作为独立校验函数使用：
# 对某机制给出「是否继续运行」建议时，先检查评估窗口是否足以覆盖延迟跃变期。
EVAL_MIN_WINDOW_DAYS = 7      # 机制效果最小评估窗口（天）
EVAL_RECENCY_WEIGHT = 0.5     # 近端窗口权重阈值：近端占比过高说明评估窗口过短


def eval_patience_check(
    history: list[dict] | None = None,
    window_days: float = EVAL_MIN_WINDOW_DAYS,
    recency_weight: float = EVAL_RECENCY_WEIGHT,
) -> dict:
    """评估准则：防「过早判失败」的延迟跃变检查。

    入参 history: [{"ts": 秒级时间戳, "metric": float}, ...]（按时间升序）。
    无历史/历史过短 → 返回 verdict="insufficient"（建议延长窗口再判）。
    返回:
      {
        "verdict": "ok" | "insufficient" | "too_short_window",
        "reason": str,
        "window_days": float,        # 覆盖的实际时间跨度（天）
        "recency_ratio": float | None,  # 近 1/3 窗口指标占整体比重（不稳定时显式暴露）
        "trend": "rising"|"flat"|"declining"|"unknown",
      }
    设计要点（蒸馏自 m69 过度递归衰减 / m65 世界模型自演化）：
      - 机制的自演化收益是延迟跃变而非线性累积 → 评估窗口必须覆盖跃变期；
      - 近端比重过高 = 评估窗口过短，直接建议延长而非判失败；
      - 本函数只做「评估是否充分」的裁决，不代替业务指标计算，保持零依赖纯函数。
    """
    if not history or len(history) < 3:
        return {
            "verdict": "insufficient",
            "reason": "评估历史不足(需≥3个采样点)，无法判断延迟跃变",
            "window_days": 0.0,
            "recency_ratio": None,
            "trend": "unknown",
        }
    try:
        ts0 = float(history[0]["ts"])
        ts1 = float(history[-1]["ts"])
        span_days = (ts1 - ts0) / 86400.0
    except (KeyError, TypeError, ValueError):
        return {
            "verdict": "insufficient",
            "reason": "历史采样点缺 ts/metric 字段，无法计算评估窗口",
            "window_days": 0.0,
            "recency_ratio": None,
            "trend": "unknown",
        }
    if span_days < window_days:
        return {
            "verdict": "too_short_window",
            "reason": f"评估窗口 {span_days:.1f} 天 < 建议最小 {window_days} 天，延迟跃变未到观察期，禁止判失败",
            "window_days": span_days,
            "recency_ratio": None,
            "trend": "unknown",
        }
    # 近端 1/3 采样点指标均值 vs 整体均值：近端占比过高 → 窗口仍在跃变爬升期
    n = len(history)
    recent = history[max(0, n - max(1, n // 3)):]
    try:
        avg_all = sum(float(h["metric"]) for h in history) / n
        avg_recent = sum(float(h["metric"]) for h in recent) / len(recent)
        ratio = (avg_recent / avg_all) if avg_all else 1.0
    except (KeyError, TypeError, ValueError):
        ratio = None
    trend = "unknown"
    if ratio is not None:
        if ratio > 1.05:
            trend = "rising"
        elif ratio < 0.95:
            trend = "declining"
        else:
            trend = "flat"
    if ratio is not None and ratio > (1.0 + recency_weight):
        return {
            "verdict": "too_short_window",
            "reason": f"近端指标占比 {ratio:.2f} 过高(>1+{recency_weight})，机制处于跃变爬升期，须延长窗口",
            "window_days": span_days,
            "recency_ratio": ratio,
            "trend": trend,
        }
    return {
        "verdict": "ok",
        "reason": f"评估窗口 {span_days:.1f} 天覆盖延迟跃变期，可正常评估",
        "window_days": span_days,
        "recency_ratio": ratio,
        "trend": trend,
    }


# ── v3.2 环境锚时效门：实现体（2026-09-13 缺口① 补齐）─────────────────────────
# 背景：PerceptionGate 早已留有 env_anchor_probe 参数插槽与 decide() 中最高优先
#       拦截分支，但本包内无实现体、调用方亦未注入 → 机制长期停留在「纸面」。
#       本段补齐实现体，保持零依赖（仅标准库）：真实锚记录与实时探针由调用方注入。
#
# 判定两类「真实漂移」（不构造假设值，全部来自真实锚记录 + 实时探测）：
#   1) 时效漂移：锚 last_verified(observed_at) 距今 > ttl_days → stale
#   2) 值漂移  ：live_probe(key) 实测 ≠ 锚记录值 → stale
# 无环境断言的动作 → 返回 None（零影响，不改变既有门行为）。
ENV_ANCHOR_TTL_DAYS_DEFAULT = 7.0


def _env_assertions(action):
    """从候选动作抽取环境断言 → {key: {"value": str, "observed_at": str|None}}。

    支持三种形态（均不新增依赖）：
      1) {"env_assert": {"ip": "192.168.1.93", ...}}
      2) {"env_assert": [{"key": k, "value": v, "observed_at": ts}, ...]}
      3) {"env_anchor": {"facts": {...}, "observed_at": ts}}
    非 dict / 无断言 → None。
    """
    if not isinstance(action, dict):
        return None
    raw = action.get("env_assert")
    default_ts = None
    if raw is None:
        blk = action.get("env_anchor")
        if isinstance(blk, dict):
            raw = blk.get("facts")
            default_ts = blk.get("observed_at")
    if isinstance(raw, dict):
        out = {}
        for k, v in raw.items():
            if isinstance(v, dict) and "value" in v:  # 形态 1b：值为记录 {value, observed_at}
                out[str(k)] = {"value": str(v.get("value", "")),
                               "observed_at": v.get("observed_at") or default_ts}
            else:
                out[str(k)] = {"value": str(v), "observed_at": default_ts}
        return out
    if isinstance(raw, list):
        out = {}
        for it in raw:
            if isinstance(it, dict) and it.get("key") is not None:
                out[str(it["key"])] = {"value": str(it.get("value", "")),
                                       "observed_at": it.get("observed_at") or default_ts}
        return out or None
    return None


def evaluate_env_anchor(action, anchor_lookup=None, live_probe=None,
                        ttl_days=None, now=None):
    """环境锚时效门判定核（真实漂移驱动，纯函数、零依赖）。

    参数：
      action       : 候选动作（形态见 _env_assertions）
      anchor_lookup: callable(key) -> {"value": str, "observed_at": iso} | None
                     真实锚记录来源（如液环 env_anchor 证据）；None=只用 action 内断言
      live_probe   : callable(key) -> str | None   实时探测（ipconfig/ps/lsof/curl 等）
      ttl_days     : 时效阈值；None → env LIQUID_ENV_ANCHOR_TTL_DAYS 或 7.0
      now          : 注入当前时间（测试用），None=time.time()

    返回：None（不涉环境断言，零影响）
          {"stale": bool, "detail": str, "reasons": [...], "checked": {...}, "ttl_days": float}
    """
    import os as _os
    import time as _time
    from datetime import datetime as _dt

    asserts = _env_assertions(action)
    if not asserts:
        return None
    if ttl_days is None:
        try:
            ttl_days = float(_os.environ.get("LIQUID_ENV_ANCHOR_TTL_DAYS", "")
                             or ENV_ANCHOR_TTL_DAYS_DEFAULT)
        except (TypeError, ValueError):
            ttl_days = ENV_ANCHOR_TTL_DAYS_DEFAULT
    _now = float(now if now is not None else _time.time())

    reasons, checked = [], {}
    for key, claim in asserts.items():
        ref = claim
        if anchor_lookup is not None:
            try:
                got = anchor_lookup(key)
            except Exception:
                got = None
            if isinstance(got, dict):
                ref = {"value": str(got.get("value", claim.get("value", ""))),
                       "observed_at": got.get("observed_at") or claim.get("observed_at")}
        item = {"claim": claim.get("value", ""), "observed_at": ref.get("observed_at"),
                "age_days": None, "ttl_ok": None, "live": None, "value_match": None}
        # 1) 时效漂移
        ts_raw = ref.get("observed_at")
        if ts_raw:
            try:
                _t = _dt.fromisoformat(str(ts_raw))
                _age_days = (_now - _t.timestamp()) / 86400.0
                item["age_days"] = round(_age_days, 3)
                item["ttl_ok"] = _age_days <= ttl_days
                if _age_days > ttl_days:
                    reasons.append(f"时效漂移: {key} last_verified {_age_days:.1f} 天 > "
                                   f"TTL {ttl_days:.0f} 天")
            except (TypeError, ValueError):
                item["ttl_ok"] = None
        # 2) 值漂移
        if live_probe is not None:
            try:
                live = live_probe(key)
            except Exception:
                live = None
            if live is not None:
                item["live"] = str(live)
                item["value_match"] = str(live).strip() == str(ref.get("value", "")).strip()
                if not item["value_match"]:
                    reasons.append(f"值漂移: {key} 锚记录={ref.get('value')!r} "
                                   f"实测={live!r}")
        checked[key] = item
    return {"stale": bool(reasons), "detail": "; ".join(reasons)[:300],
            "reasons": reasons, "checked": checked, "ttl_days": ttl_days}


def make_env_anchor_probe(anchor_lookup=None, live_probe=None, ttl_days=None):
    """构造 PerceptionGate(env_anchor_probe=...) 可注入的探针（缺口① 实现体入口）。

    返回 callable(action) -> None | {"stale": bool, ...}：语义与 PerceptionGate.decide
    第 170-181 行分支一致 —— stale 即 block（须重取证），None 即不涉环境断言（零影响）。
    """
    def _probe(action):
        return evaluate_env_anchor(action, anchor_lookup=anchor_lookup,
                                   live_probe=live_probe, ttl_days=ttl_days)

    _probe.__name__ = "env_anchor_probe"
    return _probe
