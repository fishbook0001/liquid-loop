#!/usr/bin/env python3
"""Liquid Loop Quickstart — 3 分钟跑通核心流程"""

from pathlib import Path
import sys

# 确保能导入本地 liquid_loop
sys.path.insert(0, str(Path(__file__).parent.parent))

from liquid_loop import (
    WorkspaceState, Anchor, Evidence, Memory,
    load, save, calculate_entropy,
)


def main():
    print("=" * 50)
    print("🧊 Liquid Loop Quickstart")
    print("=" * 50)

    # ── 1. 初始化/加载工作区 ────────────────────────────────
    workspace = Path(".liquid_quickstart")
    if workspace.exists():
        print(f"\n📂 加载已有工作区: {workspace}")
        state = load(workspace)
    else:
        print(f"\n🆕 创建新工作区: {workspace}")
        state = WorkspaceState()
        workspace.mkdir(exist_ok=True)

    # ── 2. 添加锚点 ────────────────────────────────────────
    print("\n📌 步骤 1: 创建锚点")
    anchor_id = state.add_anchor(
        name="用户偏好",
        description="记录用户的交互偏好与约束"
    )
    print(f"   ✅ 锚点已创建: 用户偏好 (id={anchor_id[:8]}...)")

    # ── 3. 注入证据（自动触发衰减+结晶+稳定性重算） ──────────
    print("\n📝 步骤 2: 注入证据（第 2 条将触发结晶）")
    evidences = [
        "用户偏好简洁输出，结论优先",
        "用户偏好简洁输出，结论优先",      # 重复 → 结晶
        "用户厌恶过度工程化，够用就行",
        "用户要求先理解再动手，不猜着做",
        "用户偏好简洁输出，结论优先",      # 第 3 次强化
    ]

    for i, content in enumerate(evidences, 1):
        state.add_evidence(anchor_id, content)
        print(f"   证据 #{i}: {content[:30]}...")

    # ── 4. 查看结晶记忆 ────────────────────────────────────
    print("\n💎 步骤 3: 自动结晶的记忆")
    for m in state.memories:
        print(f"   - {m.content[:50]}... (置信度={m.confidence:.2f}, 支撑证据={len(m.evidence_ids)})")

    # ── 5. 监控认知健康 ────────────────────────────────────
    print("\n📊 步骤 4: 认知健康度 (熵值)")
    entropy = calculate_entropy(state)
    status = "🟢 GREEN" if entropy < 0.3 else ("🟡 YELLOW" if entropy < 0.6 else "🔴 RED")
    print(f"   综合熵值(八维加权): {entropy:.4f} → {status}")
    print(f"   锚点稳定性: 用户偏好={state.anchors[0].stability:.2f}")

    # ── 6. 持久化 ──────────────────────────────────────────
    print("\n💾 步骤 5: 持久化到磁盘")
    save(state, workspace)
    print(f"   已保存至: {workspace}/state.json")

    # ── 7. 重新加载验证 ────────────────────────────────────
    print("\n🔄 步骤 6: 重新加载验证")
    state2 = load(workspace)
    print(f"   锚点数: {len(state2.anchors)}")
    print(f"   证据数: {len(state2.evidences)}")
    print(f"   结晶数: {len(state2.memories)}")
    print(f"   熵值: {calculate_entropy(state2):.4f}")

    print("\n" + "=" * 50)
    print("✅ Quickstart 完成！")
    print("=" * 50)
    print("\n下一步:")
    print("  - 运行 CLI: liquid-loop status")
    print("  - 查看实验: python3 experiment/liquid_benchmark.py run E1")
    print("  - 阅读论文框架: docs/液环论文框架.md")


if __name__ == "__main__":
    main()