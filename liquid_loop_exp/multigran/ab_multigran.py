"""v2.0 对照实验：单粒度检索 vs 多粒度+熵路由（今日蒸馏记忆数据集）。

隔离运行，不碰生产。输出 Top-1 正确率 / Top-3 命中率。
数据源：今日(2026-08-19)蒸馏进液环的记忆 + 负样本素材，人工构造 1:1 查询。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from liquid_loop_exp.multigran.multigran_core import MultigranMemory
from liquid_loop_exp.multigran.sim_structured import structured_sim

# ── 实验数据：15 条记忆 + 15 个查询（1:1 正确索引）────────
DATA = [
    # (记忆内容, 查询, 正确索引)
    ("军师调研 #358 PINN+LSTM 融合架构时序物理预测通用解决方案：PINN 嵌入物理准则损失约束+LSTM 时序依赖，年谐波振荡+ERA5 先验，PINT arXiv:2502.04018",
     "物理约束时序预测 谐波振荡", 0),
    ("军师调研 #361 SkillX 自动构建技能知识库：三层技能(战略计划/功能/原子)+迭代改进+探索扩展，弱模型 +10 分，arXiv:2604.04804",
     "技能知识库 蒸馏 弱模型", 1),
    ("军师调研 #359 IceBreaker 对话破冰：RID 共鸣感知兴趣蒸馏+ISG 交互导向开场生成，冷启动首条消息壁垒，ACL26 字节",
     "对话冷启动 个性化开场", 2),
    ("军师调研 #360 TDMA 动作分割数据集压缩：DDIM 潜在轨迹锚定+自适应锚点分配，Breakfast 2.4% 追平全量，ECCV26",
     "动作分割 数据压缩 轨迹", 3),
    ("军师调研 #362 重整化群：粗粒化+重标度+场重标度三步，2D 不动点 K*≈0.336，d=4 分水岭，Wilson 理论",
     "粗粒化 相变 临界指数", 4),
    ("军师调研 #366 ai-memory 编程CLI长期记忆：本地存储+按需召回+省token，akitaonrails/ai-memory ★3007",
     "AI 编程 CLI 长期记忆", 5),
    ("军师调研 #371 MemGAS 多粒度记忆：四粒度(session/turn/summary/keyword)+GMM关联+熵路由+PPR，超 HippoRAG2",
     "多粒度记忆 检索 熵路由", 6),
    ("军师调研 #373 奖励大小决定强化学习效率：100μL vs 5μL 学习提速一个数量级，多巴胺信号时长，Science 2026",
     "奖励大小 学习效率 多巴胺", 7),
    ("军师调研 #374 预测编码：ε=x-μ 预测误差，能量最小化局部学习替代反向传播，IJCAI22 综述",
     "预测误差 局部学习 反向传播替代", 8),
    ("军师调研 #376 Skill路由四路口：可见入口/职责边界/执行门槛/真实路由测试，主编排者，100 skills 选对",
     "skill 选择 路由 职责边界", 9),
    ("军师调研 #357 AI表情小球：纯 SVG+JS 零依赖桌面宠物，32 种表情状态追鼠标", "SVG 表情小球 桌面宠物", 10),
    ("军师调研 #363 Cumora 多智能体协作：AI 作团队成员(身份/记忆/领任务)+看板/日历，BYOA Claude Code/Codex",
     "多智能体协作 看板 团队成员", 11),
    ("军师调研 #365 TimesFM 时序基础模型：Google 开源 Apache-2.0 200M 参数零样本预测", "时序基础模型 零样本 预测", 12),
    ("军师调研 #367 Tabularis 数据库工作台：一键直连 Postgres/MySQL 多库，SQL 辅助+可视化图表", "数据库工作台 SQL 可视化", 13),
    ("军师调研 #369 常见神经网络科普：CNN/RNN/GNN/GAN/Transformer，按数据结构选网络", "卷积 循环 图 神经网络", 14),
]

def single_granularity(query, docs, topk=3):
    """baseline：单粒度（全文）结构化相似度检索。"""
    sims = [(i, structured_sim(query, d, "mix")) for i, d in enumerate(docs)]
    sims.sort(key=lambda x: -x[1])
    return [i for i, _ in sims[:topk] if _[1] > 0] if False else [i for i, s in sims[:topk] if s > 0]

def run():
    docs = [d[0] for d in DATA]
    queries = [d[1] for d in DATA]
    gold = [d[2] for d in DATA]

    # baseline：单粒度
    m_single = MultigranMemory()
    for d in docs:
        m_single.add([d])
    # 强制单粒度：只用 session_text 检索（绕过熵路由，等价单粒度 baseline）
    b_top1 = b_top3 = 0
    for q, g in zip(queries, gold):
        sims = [(i, structured_sim(q, d, "mix")) for i, d in enumerate(docs)]
        sims.sort(key=lambda x: -x[1])
        top = [i for i, s in sims[:3] if s > 0]
        if top and top[0] == g:
            b_top1 += 1
        if g in top:
            b_top3 += 1

    # multigran：四粒度+熵路由+图传播
    m = MultigranMemory()
    for d in docs:
        m.add([d])
    mg_top1 = mg_top3 = 0
    for q, g in zip(queries, gold):
        hits = m.retrieve(q, topk=3)
        idxs = [h["idx"] for h in hits]
        if idxs and idxs[0] == g:
            mg_top1 += 1
        if g in idxs:
            mg_top3 += 1

    n = len(DATA)
    print(f"{'':24} {'Top-1':>7} {'Top-3':>7}")
    print(f"{'单粒度(全文)':24} {b_top1/n*100:6.1f}% {b_top3/n*100:6.1f}%")
    print(f"{'多粒度+熵路由':24} {mg_top1/n*100:6.1f}% {mg_top3/n*100:6.1f}%")
    print()
    # 输出多粒度每个查询命中情况（可审计）
    for q, g in zip(queries, gold):
        hits = m.retrieve(q, topk=1)
        hit = hits[0]["idx"] if hits else -1
        mark = "✓" if hit == g else f"✗(→{hit})"
        print(f"  [{mark}] {q[:22]:24} gold={g}")
    return b_top1 / n, mg_top1 / n

if __name__ == "__main__":
    run()
