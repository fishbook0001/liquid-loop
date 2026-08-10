# 液环 v1.6：重放压力感知 + 反证轨局部阻尼

> 来源：军师调研 #116《记忆重放决定睡眠质量》（Science 2026-06-04，雷博/钟毅，DOI:10.1126/science.aed8630）
> 落地于 `liquid_loop/workspace.py` + `tests/test_replay_pressure.py`（7 用例，全量 96 passed）

## 1. 神经科学实证（#116 核心）

Science 论文用小鼠实验证明：睡眠中记忆印迹细胞的自发重放依据**情绪极性双向调节睡眠质量**。

- **负面记忆重放** → 激活促觉醒核团 → NREM 睡眠碎片化、微觉醒增加。
- **正面记忆重放** → 增强 NREM 连续性与抗干扰能力。
- **病理验证**：慢性压力模型中，压力记忆印迹的**病理性频繁自发重放**是睡眠碎片化的核心驱动力；**靶向单一记忆印迹细胞群即可逆转**病理性睡眠障碍。
- **物理分流**：正负记忆印迹细胞分道扬镳，各连不同下游网络（促睡眠 vs 促觉醒）。

## 2. 液环同构映射

| Science 实证 | 液环机制 |
|---|---|
| 记忆重放（recall/replay） | 证据被 `recall()` 命中 → `Evidence.recall_hits` 累积 |
| 负面记忆（矛盾） | `relation="contradiction"` 证据 → 降稳（反证轨 `s/(s+2c+1)`） |
| 正面记忆（支持） | `relation="support"` 证据 → 升稳（v1.5 注意力增益） |
| 病理超频重放→碎片化 | 单条 contradiction `recall_hits` 超阈 → 整体 stability 被过度拉低 |
| **靶向单印痕抑制即逆转** | **反证轨局部阻尼：超频 contradiction 局部降温，不破坏反证轨常态** |

## 3. 落地机制（workspace.py）

### 3.1 字段
- `Memory.replay_pressure: float` — 该 memory 上 over-replay 的 contradiction 累计超额召回次数（≥0，感知信号）。
- `Evidence.recall_hits` — 复用 v1.5 字段，同时驱动正面增益与负面压力感知。

### 3.2 常量（与 v1.5 注意力增益对齐）
```python
REPLAY_PRESSURE_THRESHOLD = 10  # 与 _ATTN_HIT_CAP 对齐：recall 超此视为病理超频
REPLAY_DAMP_RATE = 0.05         # 每超 1 次命中，降该 contradiction 对分母 c 的贡献权
REPLAY_DAMP_FLOOR = 0.3         # 阻尼地板：局部降温不归零（守零丢失）
```

### 3.3 `_update_memory_stability` 改动
对每个 contradiction 证据按 `recall_hits` 计算阻尼：
```python
for e in contradicts:
    hits = e.recall_hits
    if hits > REPLAY_PRESSURE_THRESHOLD:
        damp = max(REPLAY_DAMP_FLOOR, 1.0 - REPLAY_DAMP_RATE*(hits - REPLAY_PRESSURE_THRESHOLD))
        pressure += (hits - REPLAY_PRESSURE_THRESHOLD)
    else:
        damp = 1.0
    c_eff += CONTRADICTION_WEIGHT * damp
```
- **常态**（recall_hits≤10）：damp=1 → `c_eff = 2·c`，**完全等价于原反证轨公式**（零破坏）。
- **超频**（recall_hits>10）：局部降温该 contradiction 的降稳贡献，防其碎片化整体 stability。
- **地板 0.3**：即便极端超频，contradiction 仍计数（对应"局部抑制印痕"非"删除印痕"，守零丢失）。

### 3.4 感知 API
```python
WorkspaceState.perceive_replay_pressure() -> {mem_id: {content, replay_pressure, stability}}
```
仅返回压力>0 的 memory，供上层决策（局部阻尼已在 `_update_memory_stability` 自动触发）。

## 4. 设计守界

1. **守反证轨**：常态下公式与 v1.5 完全一致；阻尼仅在超频时介入（靶向单印痕）。
2. **守零丢失**：阻尼地板 0.3，contradiction 永不归零为 0。
3. **局部非全局**：仅超频的该条 contradiction 被降温，同 memory 其他 contradiction 不受影响。
4. **双向极性一致**：正面重放(v1.5)升稳、负面超频重放(v1.6)局部降温——与 Science 双向调节同构。
5. **关键窗口**：archived 证据不 recall（v1.5 已守），故超频压力只来自活跃印痕。

## 5. 测试覆盖（7 用例，全量 96 passed）

- 常态 contradiction 无阻尼、等价原公式
- 超频→局部降温(damp=0.9)、stability 高于未阻尼
- perceive_replay_pressure 暴露 over-replay
- 阻尼地板 0.3（极端超频仍计数）
- 多条超频各自局部降温、压力累加
- 真实召回路径：recall 命中 contradiction→bump→触发阻尼
- 持久化：save/load 后重算仍触发阻尼
