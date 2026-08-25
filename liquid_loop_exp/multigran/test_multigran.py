"""multigran 实验模块隔离测试（pytest）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from liquid_loop_exp.multigran.multigran_core import MultigranMemory
from liquid_loop_exp.multigran.sim_structured import ncd_sim, jaccard_bigram_sim, structured_sim

def _mk():
    m = MultigranMemory()
    m.add(["[User]: I moved to Seattle last month. [AI]: How is the weather there?",
           "I started a new job at a robotics startup and commute by bike."], {"id": 1})
    m.add(["[User]: I moved to Seattle in January. [AI]: Nice change!",
           "I now work on robot navigation systems."], {"id": 2})
    m.add(["今日调研 PINN 物理约束时序预测，报告已落盘", "蒸馏进液环，锚点 arXiv:2502.04018"],
          {"id": 3})
    return m

def test_sim_primitives():
    assert ncd_sim("hello world", "hello world") == 1.0
    assert ncd_sim("", "x") == 0.0
    assert 0.0 <= jaccard_bigram_sim("abc", "abd") <= 1.0
    assert structured_sim("记忆", "记忆") == 1.0

def test_units_built():
    m = _mk()
    assert len(m.units) == 3
    assert m.units[0]["keyword"]            # 关键词非空
    assert m.units[0]["summary"]            # 摘要非空
    assert len(m.units[0]["turn"]) == 2     # turn 切分

def test_entropy_route():
    m = _mk()
    w = m._granularity_weights("where did I move and what is my new job")
    assert abs(sum(w.values()) - 1.0) < 1e-6   # 归一化
    assert all(v > 0 for v in w.values())

def test_retrieve_hits_relevant():
    m = _mk()
    hits = m.retrieve("Seattle move and robotics job", topk=2)
    assert len(hits) >= 1
    assert hits[0]["idx"] in (0, 1)             # 相关条目优先

def test_retrieve_cross_domain():
    m = _mk()
    hits = m.retrieve("PINN 物理约束 时序预测 蒸馏", topk=1)
    assert hits[0]["idx"] == 2                   # 中文记忆精准命中

def test_graph_built():
    m = _mk()
    m._rebuild_graph_if_needed()
    assert isinstance(m.graph, dict)
