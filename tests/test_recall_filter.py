"""液环召回相关性过滤（蒸馏自 RobSelf）落地测试"""
from liquid_loop.recall_filter import (
    cosine_dict,
    content_aware_filter,
    recall_content_filter,
    _vec,
)


def test_cosine_identical():
    a = _vec("液环 禁 向量")
    assert abs(cosine_dict(a, a) - 1.0) < 1e-9


def test_cosine_orthogonal_zero():
    a = _vec("苹果 香蕉")
    b = _vec("西瓜 葡萄")
    assert cosine_dict(a, b) == 0.0


def test_content_aware_filter_suppress_vs_keep():
    hi = _vec("液环 禁 向量 记忆")
    lo = _vec("苹果 香蕉 西瓜")
    tgt = _vec("液环 禁 向量 自检")
    w_hi, s_hi = content_aware_filter(hi, tgt, theta=0.3)
    w_lo, s_lo = content_aware_filter(lo, tgt, theta=0.3)
    assert w_hi == 1.0 and s_hi >= 0.3
    assert w_lo < 1.0 and s_lo < 0.3  # 低相似被软抑制


def test_augment_only_attaches_metadata():
    # theta=0 → 仅增强，不抑制，所有候选保留且附字段
    cands = [
        {"memory_id": "e1", "type": "evidence", "content": "液环禁向量记忆系统"},
        {"memory_id": "m1", "type": "memory", "content": "共识结晶内容"},
    ]
    out, rep = recall_content_filter("液环 向量", cands, theta=0.0)
    assert rep["suppressed"] == 0
    assert len(out) == 2
    assert "content_aware_weight" in out[0]
    assert "aligned_score" in out[0]
    # 记忆候选也附加 metadata（未抑制）
    assert "content_aware_weight" in out[1]


def test_suppress_noise_at_theta():
    # theta=0.3 → 内容无关候选被抑制
    cands = [
        {"memory_id": "e1", "type": "evidence", "content": "液环禁向量记忆系统"},
        {"memory_id": "e2", "type": "evidence", "content": "苹果 香蕉 西瓜 葡萄"},
    ]
    out, rep = recall_content_filter("液环 向量", cands, theta=0.3)
    assert rep["suppressed"] == 1
    assert len(out) == 1
    assert out[0]["memory_id"] == "e1"


def test_aligned_score_absorbs_subset_misalignment():
    # query 是候选的子集 → 错位容忍对齐分应 > 纯 jaccard（子集被识别为相关）
    cands = [{"memory_id": "e1", "type": "evidence",
              "content": "液环 禁 向量 记忆 系统 自检 环节"}]
    out, _ = recall_content_filter("液环 向量", cands, theta=0.0)
    # aligned_score = max(jaccard, containment(q⊆c), containment(c⊆q))
    # query⊆candidate → containment(q⊆c) = 1.0
    assert out[0]["aligned_score"] == 1.0


def test_recall_order_preserved_no_rerank():
    # 过滤绝不重排：输入顺序保持
    cands = [
        {"memory_id": "e1", "type": "evidence", "content": "苹果 香蕉"},
        {"memory_id": "e2", "type": "evidence", "content": "液环 向量 记忆"},
        {"memory_id": "e3", "type": "evidence", "content": "西瓜 葡萄"},
    ]
    # theta=0.3 → e1/e3 被抑制，仅 e2 保留，但顺序应紧随其原位置（输出顺序=输入顺序）
    out, rep = recall_content_filter("液环 向量", cands, theta=0.3)
    assert [r["memory_id"] for r in out] == ["e2"]
    assert rep["n_in"] == 3
