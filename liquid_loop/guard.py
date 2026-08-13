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
