# Liquid Loop

> **Self-Organizing Cognitive Memory for AI Agents** — Zero LLM dependency, pure Python implementation of the Liquid Loop theory.

[![PyPI](https://img.shields.io/pypi/v/liquid-loop.svg)](https://pypi.org/project/liquid-loop/)
[![Python](https://img.shields.io/pypi/pyversions/liquid-loop.svg)](https://pypi.org/project/liquid-loop/)
[![License](https://img.shields.io/pypi/l/liquid-loop.svg)](https://opensource.org/licenses/MIT)
[![Gitee](https://img.shields.io/badge/Gitee-Mirror-orange)](https://gitee.com/feixubuke/liquid-loop)

---

## 核心理念

**当前所有 Agent 记忆系统的共同缺陷：依赖外部编辑。**

- 图数据库 → 需要 LLM 诊断器做 Split/Merge/Update
- 向量检索 → 需要外部评分+排序
- LLM 摘要 → 需要外部提取+压缩
- 结构化 Schema → 需要外部设计+维护

**Liquid Loop 提出第三条路：自组织记忆。**

- 不需要外部编辑 → 证据一致性自动驱动结晶
- 不需要检索排序 → 熵值作为天然认知健康指标
- 不需要 LLM 介入管理 → LLM 只接触数据，不管理数据

---

## 核心概念

| 概念 | 物理隐喻 | 作用 |
|------|---------|------|
| **Anchor** 锚点 | 晶种 | 认知关注点，有稳定性值 s ∈ [0,1] |
| **Evidence** 证据 | 附着粒子 | 锚点下的具体观察，权重指数衰减 w×0.95ᵗ |
| **Memory** 结晶 | 结晶体 | 2+ 条一致 Evidence 自动凝聚，有置信度 c |
| **Entropy** 熵值 | 流体无序度 | 八维加权（锚点漂移 / 冲突密度 / 碎片 / 活跃间隔 / 价值衰减 / 锚定强度 / CPE 三维） |

**状态判定：**
```
GREEN  (entropy < 0.3)  — 认知健康
YELLOW (0.3 ≤ entropy < 0.6) — 需关注
RED    (entropy ≥ 0.6)  — 需清理
```

---

## 快速开始

### 安装
```bash
pip install liquid-loop
# 国内镜像自动加速：pip install -i https://pypi.tuna.tsinghua.edu.cn/simple liquid-loop
```

### 3 分钟上手
```python
from liquid_loop import WorkspaceState, load, save, calculate

# 1. 创建/加载工作区
state = WorkspaceState()  # 或 load(Path("."))

# 2. 添加锚点
anchor_id = state.add_anchor("核心使命", "系统的核心目标与约束")

# 3. 注入证据（自动触发：衰减 + 结晶 + 稳定性重算）
state.add_evidence(anchor_id, "用户偏好简洁输出，结论优先")
state.add_evidence(anchor_id, "用户偏好简洁输出，结论优先")  # 2次一致 -> 结晶
state.add_evidence(anchor_id, "用户厌恶过度工程化，够用就行")

# 4. 查看结晶记忆
for m in state.memories:
    print(f"结晶: {m.content[:50]}... (置信度={m.confidence:.2f})")

# 5. 监控认知健康
entropy = calculate(state)
print(f"熵值: {entropy:.4f} → {'🟢GREEN' if entropy < 0.3 else '🟡YELLOW' if entropy < 0.6 else '🔴RED'}")

# 6. 持久化
save(state, Path("."))
```

### CLI 使用
```bash
# 初始化工作区（创建 .liquid/state.json）
liquid-loop init

# 添加锚点（支持自动三维分类：密度 / 认知阶段 / 流动性）
liquid-loop anchor_add "项目目标" "完成液环论文与开源"

# 注入证据
liquid-loop evidence_add "项目目标" "已完成 11 轮实验与 4 个实证包"

# 查看状态（含审计链哈希）
liquid-loop status

# 列出所有记忆结晶
liquid-loop memory_list

# 审计：验证链式哈希完整性
liquid-loop audit

# 查看审计日志（最近 20 条）
liquid-loop audit-log --tail 20

# 快照（记录当前认知基线）
liquid-loop snapshot
```

---

## MESH 集成（多智能体共识）

液环从 v0.7.0 起内置官方 MESH 集成 `liquid_loop.mesh`，把"多智能体共识协议"落地为可复用代码，作为 agent-mesh 节点的标准接入层。

```python
from liquid_loop.mesh import validate_evidence, compute_cci, cognitive_health, fetch_state

# agent 写入前契约自检（零向量：content 必须精确字符串，禁 embedding）
ok, errs = validate_evidence({"agent_id": "vera", "content": "用户偏好简洁输出"})

# 从 8790 拉取记忆状态，算主体间性共识指数 CCI
items = fetch_state("http://127.0.0.1:8790")
health = cognitive_health(items)
print(health["CCI"], health["consensus_crystals"])
```

零向量哲学：一致性判定走**结构化精确相等 + 审计链哈希**，绝不引入任何 embedding / 相似度。规范详见 `mesh/liquid_loop_mesh_v2_spec.md`。

---

## 架构对比

```
记忆管理光谱：

[外力编辑] ←──────────────── [混合/零LLM检索] ──────────────→ [自组织]
  All-Mem                         Mandol (零LLM检索)              Liquid Loop
  GRAVITY                        CoreMem (检索优化)              (零LLM管理)
  AnchorMem                      MemForest (索引)
  T-Mem, GAM                     HeLa-Mem (联想)
  APEX-MEM, Synthius             DimMem (维度压缩)

Liquid Loop 是唯一完全自组织 + 零 LLM 管理的系统。
```

---

## 基准实验

| 实验 | 核心发现 | 关键指标 |
|------|----------|----------|
| **E1 认知负荷** | 100 证据 → 13 结晶，熵值维持 GREEN | 熵值 0.035→0.194，单条 0.01ms |
| **E2 噪声鲁棒性** | 0%/20%/50% 噪声下熵值完全相同 | **天然抗噪**（精确匹配机制） |
| **E3 遗忘曲线** | 5 轮衰减后权重保留 83.2% | 平滑指数衰减，无灾难性遗忘 |
| **E4 扩展性** | 1000 证据延迟 0.179ms | 500x 快于 LLM 调用 |

> 完整实验数据：`experiment/liquid_benchmark_results/`

---

## 理论来源

- **液环理论** — 飞哥原创，11 轮实验，4 个实证包
- **核心论文** — [Liquid Loop: Self-Organizing Cognitive Memory for AI Agents](docs/液环论文框架.md)
- **竞品调研** — 2024-2026 Agent Memory 50+ 篇论文全景扫描

---

## 项目结构

```
liquid-loop/
├── liquid_loop/
│   ├── __init__.py      # 公共 API 导出
│   ├── workspace.py     # 核心数据模型 + AuditChain + auto_classify + decay
│   ├── storage.py       # JSON 持久化 + 审计链写入
│   ├── entropy.py       # 八维熵值计算（含 CPE 三维）
│   ├── mesh/            # MESH v2 多智能体共识协议集成（validate_evidence / compute_cci / ...）
│   └── cli.py           # Click CLI (11 命令)
├── examples/
│   └── quickstart.py
├── tests/               # 待补充
├── pyproject.toml
├── README.md
├── LICENSE
└── CHANGELOG.md
```

---

## 开发

```bash
git clone https://gitee.com/feixubuke/liquid-loop.git
cd liquid-loop
pip install -e ".[dev]"
pytest -v
```

---

## 路线图

- [ ] 语义一致性结晶（嵌入相似度替代精确匹配）
- [x] 多 Agent 液环耦合（`liquid_loop.mesh` v2 共识协议，2026-07-15 落地）
- [ ] LX 扩展：世界模型预测纳入液环
- [ ] LoCoMo / LongMemEval 基准对比
- [ ] 边缘端部署优化（<50KB）

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE)

---

## 致谢

液环理论源自飞哥 2026 年 6-7 月对抗训练与实战项目的 11 轮实证沉淀。
感谢开源社区提供的竞品参考：All-Mem, Mandol, CoreMem, HeLa-Mem 等。

> **引用**
> ```
> @misc{liquid-loop-2026,
>   title={Liquid Loop: Self-Organizing Cognitive Memory for AI Agents},
>   author={Fei Ge},
>   year={2026},
>   url={https://gitee.com/feixubuke/liquid-loop}
> }
> ```