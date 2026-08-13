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
    if risk == "high" and not auto_approve:
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

    零依赖：真实因果边 / 负载来自 liquid-loop 的 workspace / recall_filter / rar，
    由 adapter 注入（causal_core_predicate / load_probe），不在本模块 import 重型依赖。
    这样门控成为「可配置策略」，而非把整套液环塞进门里的膨胀方案。
    """

    def __init__(self, budget: float = 1.0, sensitivity: str = "normal",
                 causal_core_predicate=None, load_probe=None):
        self.budget = float(budget)
        self.sensitivity = sensitivity
        self._spent = 0.0
        # 默认谓词：无注入时一律非因果核心（保守，闸正常生效）
        self.causal_core_predicate = causal_core_predicate or (lambda action: False)
        # 默认负载探测器：无注入时返回 0.0（低载，精度优先路径）
        self.load_probe = load_probe or (lambda: 0.0)

    def decide(self, action, cost: float = 0.0, confidence: float = 1.0,
               irreversible: bool = False) -> dict:
        """对一条候选感知动作做门控裁决。

        返回 dict: {"decision": allow|degrade|block, "reason": str, ...}
          - block    : 需人工确认（交回人），不消耗预算。
          - allow    : 放行（因果核心豁免 / 正常 / 低载精度优先）。
          - degrade  : 超预算 + 高载 → 降采样 / 跳过，省算力；不消耗预算。
        """
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
