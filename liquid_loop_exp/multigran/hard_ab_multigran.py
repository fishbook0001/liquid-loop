"""v2.0 难场景对照实验：语义等价字面不重叠查询 + 相近主题干扰项。

设计意图（区分度核心）：
1. 5 个难查询用同义/释义表达，不含原文关键词——考验非字面检索能力
2. 5 条干扰项与目标记忆主题相近、且与难查询部分字面重叠——考验区分能力
3. 常规查询 15 个（字面重叠）作为基线对照

隔离运行，不碰生产。输出 Top-1 / Top-3 分场景。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from liquid_loop_exp.multigran.multigran_core import MultigranMemory
from liquid_loop_exp.multigran.sim_structured import structured_sim

# ── 记忆库：15 条原文 + 5 条干扰项 ──────────────────────
DOCS = [
    "军师调研 #358 PINN+LSTM 融合架构时序物理预测通用解决方案：PINN 嵌入物理准则损失约束+LSTM 时序依赖，年谐波振荡+ERA5 先验，PINT arXiv:2502.04018",
    "军师调研 #361 SkillX 自动构建技能知识库：三层技能(战略计划/功能/原子)+迭代改进+探索扩展，弱模型 +10 分，arXiv:2604.04804",
    "军师调研 #359 IceBreaker 对话破冰：RID 共鸣感知兴趣蒸馏+ISG 交互导向开场生成，冷启动首条消息壁垒，ACL26 字节",
    "军师调研 #360 TDMA 动作分割数据集压缩：DDIM 潜在轨迹锚定+自适应锚点分配，Breakfast 2.4% 追平全量，ECCV26",
    "军师调研 #362 重整化群：粗粒化+重标度+场重标度三步，2D 不动点 K*≈0.336，d=4 分水岭，Wilson 理论",
    "军师调研 #366 ai-memory 编程CLI长期记忆：本地存储+按需召回+省token，akitaonrails/ai-memory ★3007",
    "军师调研 #371 MemGAS 多粒度记忆：四粒度(session/turn/summary/keyword)+GMM关联+熵路由+PPR，超 HippoRAG2",
    "军师调研 #373 奖励大小决定强化学习效率：100μL vs 5μL 学习提速一个数量级，多巴胺信号时长，Science 2026",
    "军师调研 #374 预测编码：ε=x-μ 预测误差，能量最小化局部学习替代反向传播，IJCAI22 综述",
    "军师调研 #376 Skill路由四路口：可见入口/职责边界/执行门槛/真实路由测试，主编排者，100 skills 选对",
    "军师调研 #357 AI表情小球：纯 SVG+JS 零依赖桌面宠物，32 种表情状态追鼠标",
    "军师调研 #363 Cumora 多智能体协作：AI 作团队成员(身份/记忆/领任务)+看板/日历，BYOA Claude Code/Codex",
    "军师调研 #365 TimesFM 时序基础模型：Google 开源 Apache-2.0 200M 参数零样本预测",
    "军师调研 #367 Tabularis 数据库工作台：一键直连 Postgres/MySQL 多库，SQL 辅助+可视化图表",
    "军师调研 #369 常见神经网络科普：CNN/RNN/GNN/GAN/Transformer，按数据结构选网络",
    # ── 干扰项（主题相近，与难查询部分字面重叠）──────────
    "LSTM 气象温度时间序列预测：长短期记忆网络处理时序依赖，纯数据驱动无物理约束，训练集 ERA5 再分析数据",
    "提示词工程技巧合集：写更好的 prompt 让大模型输出更准，指令模板与少样本示例，token 效率优化",
    "寒暄问候语大全：早晨好/下午好/吃了吗，日常见面打招呼话术模板，社交破冰第一步",
    "反向传播算法详解：梯度下降训练深度网络的标准方法，链式法则逐层计算梯度，BP 是深度学习基石",
    "SKILL.md 编写规范：如何把工作流写成可复用技能文件，frontmatter 元数据与工具调用约定",
]

# ── 查询：15 常规（字面重叠）+ 5 难（语义等价不重叠）──
# (query, gold_idx)
QUERIES = [
    # 常规查询（字面重叠基线）
    ("物理约束时序预测 谐波振荡", 0),
    ("技能知识库 蒸馏 弱模型", 1),
    ("对话冷启动 个性化开场", 2),
    ("动作分割 数据压缩 轨迹", 3),
    ("粗粒化 相变 临界指数", 4),
    ("AI 编程 CLI 长期记忆", 5),
    ("多粒度记忆 检索 熵路由", 6),
    ("奖励大小 学习效率 多巴胺", 7),
    ("预测误差 局部学习 反向传播替代", 8),
    ("skill 选择 路由 职责边界", 9),
    ("SVG 表情小球 桌面宠物", 10),
    ("多智能体协作 看板 团队成员", 11),
    ("时序基础模型 零样本 预测", 12),
    ("数据库工作台 SQL 可视化", 13),
    ("卷积 循环 图 神经网络", 14),
    # ── 难查询（语义等价，字面不重叠）────────────────────
    ("预报天气总是不准，想让模型把物理定律嵌进去训练，误差能降三成", 0),
    ("把干过的活沉淀成可调用的技能，让笨一点的模型也能变聪明十分", 1),
    ("第一次聊天不知道说啥，想根据对方兴趣爱好自动生成开场白避免尬聊", 2),
    ("人脑不用反向传播也能学习，是靠什么机制逐层微调权重的", 8),
    ("装了上百个技能，怎么保证每次任务都挑到对的那一个而不选错", 9),
]

def run():
    docs, queries = DOCS, QUERIES
    n_all = len(queries)
    n_hard = 5
    hard_idx = list(range(15, 20))

    # ── baseline：单粒度（session 全文）──
    b_top1 = b_top3 = 0
    b_hard_top1 = 0
    for qi, (q, g) in enumerate(queries):
        sims = [(i, structured_sim(q, d, "mix")) for i, d in enumerate(docs)]
        sims.sort(key=lambda x: -x[1])
        top = [i for i, s in sims[:3] if s > 0]
        if top and top[0] == g:
            b_top1 += 1
            if qi in hard_idx:
                b_hard_top1 += 1
        if g in top:
            b_top3 += 1

    # ── multigran RRF：多路召回融合 ──
    m_rrf = MultigranMemory()
    for d in docs:
        m_rrf.add([d])
    r_top1 = r_top3 = 0
    r_hard_top1 = 0
    r_hits = []
    for qi, (q, g) in enumerate(queries):
        hits = m_rrf.retrieve(q, topk=3, fusion="rrf")
        idxs = [h["idx"] for h in hits]
        if idxs and idxs[0] == g:
            r_top1 += 1
            if qi in hard_idx:
                r_hard_top1 += 1
        if g in idxs:
            r_top3 += 1
        if qi in hard_idx:
            r_hits.append((q[:18], g, idxs, [h.get("via") for h in hits]))

    # ── multigran：四粒度+熵路由+图传播 ──
    m = MultigranMemory()
    for d in docs:
        m.add([d])
    mg_top1 = mg_top3 = 0
    mg_hard_top1 = 0
    mg_hits = []
    for qi, (q, g) in enumerate(queries):
        hits = m.retrieve(q, topk=3)
        idxs = [h["idx"] for h in hits]
        if idxs and idxs[0] == g:
            mg_top1 += 1
            if qi in hard_idx:
                mg_hard_top1 += 1
        if g in idxs:
            mg_top3 += 1
        if qi in hard_idx:
            mg_hits.append((q[:18], g, idxs, hits[0].get("g_weights") if hits else None))

    print(f"{'场景':<14} {'方法':<14} {'Top-1':>7} {'Top-3':>7}")
    print("-" * 46)
    print(f"{'全部(20)':<14} {'单粒度':<14} {b_top1/n_all*100:6.1f}% {b_top3/n_all*100:6.1f}%")
    print(f"{'全部(20)':<14} {'多粒度+熵路由':<14} {mg_top1/n_all*100:6.1f}% {mg_top3/n_all*100:6.1f}%")
    print(f"{'全部(20)':<14} {'多粒度+RRF':<14} {r_top1/n_all*100:6.1f}% {r_top3/n_all*100:6.1f}%")
    print(f"{'难查询(5)':<14} {'单粒度':<14} {b_hard_top1/5*100:6.1f}% {'-':>6}")
    print(f"{'难查询(5)':<14} {'多粒度+熵路由':<14} {mg_hard_top1/5*100:6.1f}% {'-':>6}")
    print(f"{'难查询(5)':<14} {'多粒度+RRF':<14} {r_hard_top1/5*100:6.1f}% {'-':>6}")
    # 单粒度难查询逐条（对照）
    print("── 难查询单粒度 Top3（对照）──")
    for qi in hard_idx:
        q, g = queries[qi]
        sims = [(i, structured_sim(q, d, "mix")) for i, d in enumerate(docs)]
        sims.sort(key=lambda x: -x[1])
        top = [i for i, s in sims[:3] if s > 0]
        mark = "✓" if top and top[0] == g else f"✗(→{top})"
        print(f"  [{mark}] gold={g} top={top}   Q: {q[:20]}")
    print()
    print("── 难查询逐条审计（多粒度）──")
    for q, g, idxs, via in r_hits:
        mark = "✓" if idxs and idxs[0] == g else f"✗(→{idxs})"
        print(f"  [RRF {mark}] gold={g} top={idxs} via={via}")
        print(f"      Q: {q}")
    for q, g, idxs, w in mg_hits:
        mark = "✓" if idxs and idxs[0] == g else f"✗(→{idxs})"
        print(f"  [{mark}] gold={g} top={idxs}")
        print(f"      Q: {q}")
        if w:
            print(f"      粒度权重 session={w['session']} summary={w['summary']} keyword={w['keyword']}")

if __name__ == "__main__":
    run()
