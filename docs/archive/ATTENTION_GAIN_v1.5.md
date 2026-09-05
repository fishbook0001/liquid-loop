# 液环 v1.5 注意力增益机制（#115 启发工程化落地）

> 来源：军师调研 #115《注意力调制价值归一化·多看几眼价值更高》——Nature Communications 实证
> （Cecchi/Gluth/Palminteri 2026, DOI:10.1038/s41467-026-74747-w）：人类面对多选项系统性低估中间项，
> 但注意力（多看几眼）像增益旋钮因果重塑主观价值，关键窗口在奖励揭晓前。

## 机制（对齐神经科学实证）
- **多看几眼 = 价值升**：被反复召回的证据黏滞升稳（gaze 增益 ↔ τ(x) 黏滞吸收）。
- 在 `_update_memory_stability` 中把 `attn_bonus = Σ(0.15·min(recall_hits, 10))` 作为 **support 加成**：
  `stability = (s + attn_bonus) / (s + attn_bonus + 2c + 1)`。**只增 s 不削 c → 不破坏反证轨**。

## 改动点（`liquid_loop/workspace.py`）
1. `Evidence.recall_hits: int = 0`（被召回命中计数，持久化兼容旧数据）
2. `Memory.attn_bonus: float = 0.0`（审计展示用）
3. 常量 `ATTENTION_GAIN_WEIGHT=0.15` / `_ATTN_HIT_CAP=10`（CONTRADICTION_WEIGHT 旁）
4. `WorkspaceState.register_recall(ids)`：黏滞升 `weight`（gaze 增益饱和曲线）；**archived 证据不升权**（守关键窗口）
5. `WorkspaceState.recall(query, agent_id, top_k)`：统一入口，封装 `RARIndex.build` + 命中 evidence 自动 `register_recall`

## 守边界（不破液环铁律）
- 禁向量：纯标量计数 + 字段，无 embedding
- 零丢失：archived 证据仅降权不增权
- 关键窗口：证据吸收须在 crystallization 前（archived 不升）
- 反证轨：attn 只增 s 不削 c

## 测试（`tests/test_attention_gain.py`，6 用例全过）
recall 触发升稳 / 多次 recall 封顶 / 反证不被掩盖 / 无 support 不凭空升 / 归档不升 / 持久化。
**全量 89 passed（原 83 + 6），零回归。**

## 对接 server 建议
`LiquidReweight` / `LiquidSelfSpin` 为 client 引擎/意识层，不经此入口；server `ll_recall` 可逐步切到
`state.recall()` 使"多看几眼"增益覆盖主召回路径。

## 落地踩坑（已修）
`RARIndex.build_or_cache` 缓存 key 仅 `(version, agent_id)` → 跨 state 复用旧 idx → 新 state 的
evidence 不被 bump；`recall` 改用 `RARIndex.build` 每次独立构建，规避污染。
