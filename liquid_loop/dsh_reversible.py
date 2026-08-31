#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh_reversible.py — Cordis 可逆副作用（Disposable Pattern）零依赖原语
==============================================================================
蒸馏自 DeepSeek Harness #204（2026-08-14 一手核验），2026-08-28 接活进液环运行时。

Cordis 的核心机制（Koishi/Shigma 元框架，DeepSeek vendored + 北大联合扩展）：
  - 每个插件运行在一个 Context 里，凡是它"占用的东西"都挂在 Context 上
  - dispose 时按"子先于父、注册逆序"自动撤销全部副作用
  - 副作用全追踪、卸载自动回滚、生命周期包含（provider 比 consumer 活得久）

本原语把它极简成可被液环/vera 脚本 import 的通用"可回滚状态变更"底座：
  register() 登记一个副作用 + 其撤销函数 → dispose() 时自动按序回滚。

与液环同构：append-only + supersede/tombstone（软删除可回滚）↔ 可逆副作用；
  "no privileged core" ↔ 本原语无特权对象，全部副作用一视同仁可回滚。
与 Vera 红线#1 safe_rm 同构：删除/移动先登记 undo，dispose 时 mv 回原位即无损回滚。

依赖：仅 Python 标准库。零外部依赖。
接活范式（08-12 三段式）：挂模块 + __init__ 暴露 + 自测 → server 经 liquid_loop 包 import。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List


@dataclass
class _Effect:
    """一个已登记的副作用：apply 时的动作名 + 撤销回调。"""
    label: str
    undo: Callable[[], None]


class ReversibleRegistry:
    """
    可逆副作用注册表（Cordis Context 的最小实现）。

    语义：
      - register(label, undo) 登记副作用；dispose() 按注册逆序回滚。
      - child() 派生子作用域；父 dispose 前先 dispose 全部子作用域（子先于父）。
      - require(other) 声明"我依赖 other"（生命周期包含）：
        被依赖方 dispose 前，先 dispose 依赖它的注册表（provider 活得比 consumer 久）。
      - dispose() 幂等；重复调用安全。
      - 无特权对象：本类不做任何"特殊保护"，一切副作用同等可回滚。

    设计约束（蒸馏铁律）：
      - 只处理"可逆副作用"，不实现事件总线/依赖注入容器——那是 Cordis 的其余部分，
        需要时另行蒸馏。保持单文件、零依赖、可独立测试。
    """

    def __init__(self, name: str = "root") -> None:
        self.name = name
        self._effects: List[_Effect] = []
        self._children: List["ReversibleRegistry"] = []
        self._dependents: List["ReversibleRegistry"] = []  # 依赖我的注册表
        self._disposed = False

    # ---- 登记与派生 ----

    def register(self, label: str, undo: Callable[[], None]) -> None:
        """登记一个副作用及其撤销回调。dispose 时按注册逆序调用 undo。"""
        self._ensure_alive()
        self._effects.append(_Effect(label=label, undo=undo))

    def child(self, name: str) -> "ReversibleRegistry":
        """派生子作用域。父 dispose 前先 dispose 全部子作用域（子先于父）。"""
        self._ensure_alive()
        c = ReversibleRegistry(name=f"{self.name}/{name}")
        self._children.append(c)
        return c

    def require(self, provider: "ReversibleRegistry") -> None:
        """
        生命周期包含：本注册表依赖 provider。
        provider 被 dispose 前，会先 dispose 所有声明依赖它的注册表（consumer）。
        避免"提供者已卸载、消费者还在调用"的悬空引用。
        """
        self._ensure_alive()
        provider._dependents.append(self)

    # ---- 回滚 ----

    def dispose(self) -> None:
        """按顺序回滚：依赖我的（consumer）→ 子作用域 → 自身副作用（注册逆序）。幂等。"""
        if self._disposed:
            return
        self._disposed = True
        for dep in reversed(self._dependents):
            dep.dispose()
        for c in reversed(self._children):
            c.dispose()
        for e in reversed(self._effects):
            try:
                e.undo()
            except Exception as exc:  # 单条撤销失败不阻断整体回滚
                print(f"[{self.name}] undo({e.label}) 失败: {exc!r}")
        self._effects.clear()

    def _ensure_alive(self) -> None:
        if self._disposed:
            raise RuntimeError(f"registry '{self.name}' 已 dispose，禁止再登记副作用")

    # ---- 上下文管理（with 语句自动回滚） ----

    def __enter__(self) -> "ReversibleRegistry":
        return self

    def __exit__(self, *exc) -> None:
        self.dispose()

    # ---- 只读状态 ----

    @property
    def disposed(self) -> bool:
        return self._disposed

    @property
    def effect_count(self) -> int:
        return len(self._effects)

    def __repr__(self) -> str:
        return (f"ReversibleRegistry(name={self.name!r}, effects={len(self._effects)}, "
                f"children={len(self._children)}, disposed={self._disposed})")


# ---------------------------------------------------------------------------
# 自测（运行时验证）
# ---------------------------------------------------------------------------
def _selftest() -> None:
    log: List[str] = []

    # 1. 基本可逆：注册逆序回滚
    r = ReversibleRegistry("base")
    r.register("a", lambda: log.append("undo-a"))
    r.register("b", lambda: log.append("undo-b"))
    r.dispose()
    assert log == ["undo-b", "undo-a"], f"逆序回滚失败: {log}"
    r.dispose()  # 幂等
    print("✓ 基本可逆 + 幂等")

    # 2. 子先于父
    log.clear()
    r = ReversibleRegistry("root")
    r.register("root-e", lambda: log.append("undo-root"))
    c = r.child("sub")
    c.register("sub-e", lambda: log.append("undo-sub"))
    r.dispose()
    assert log == ["undo-sub", "undo-root"], f"子先于父失败: {log}"
    print("✓ 子先于父回滚")

    # 3. 生命周期包含：provider 先卸 consumer
    log.clear()
    provider = ReversibleRegistry("provider")
    consumer = ReversibleRegistry("consumer")
    consumer.require(provider)  # consumer 依赖 provider
    provider.register("p", lambda: log.append("undo-provider"))
    consumer.register("c", lambda: log.append("undo-consumer"))
    provider.dispose()
    assert log == ["undo-consumer", "undo-provider"], f"生命周期包含失败: {log}"
    print("✓ 生命周期包含（consumer 先于 provider 回滚）")

    # 4. with 语句自动回滚
    log.clear()
    with ReversibleRegistry("ctx") as ctx:
        ctx.register("x", lambda: log.append("undo-x"))
    assert log == ["undo-x"], f"with 自动回滚失败: {log}"
    print("✓ with 语句自动回滚")

    # 5. 单条撤销失败不阻断整体
    log.clear()
    r = ReversibleRegistry("faulty")
    r.register("bad", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r.register("ok", lambda: log.append("undo-ok"))
    r.dispose()
    assert log == ["undo-ok"], f"失败隔离失败: {log}"
    print("✓ 单条撤销失败不阻断整体")

    print("SELFTEST ALL GREEN")


if __name__ == "__main__":
    _selftest()
