"""锚点自进化补强 + 回归测试（v0.5.3）

覆盖：
1. CPE MERGE 分支不再抛 NameError（回归）
2. 成核回流：高置信结晶自动回填锚点空描述
3. 群内自洽：低一致性证据自动生成 Conflict
"""
from liquid_loop.workspace import WorkspaceState, CPERegularizer


def test_cpe_merge_no_nameerror():
    """回归：命中'近似重复'证据时，MERGE 分支不得抛 NameError"""
    s = WorkspaceState()
    a = s.add_anchor("简洁偏好", "desc")
    s.add_evidence(a, "用户偏好简洁输出")
    s.add_evidence(a, "用户偏好简洁输出")
    reg = CPERegularizer(s)
    # 第三条与已有证据高度重叠 → 命中 MERGE 分支
    result = reg.regularize(a.name, "用户偏好简洁输出")
    assert result["action"] == "MERGE"
    # 修复前此分支引用未定义变量 protection_weight → NameError
    assert "protection_weight" not in result.get("details", {})


def test_nucleate_feedback_to_anchor():
    """成核回流：结晶 confidence >= 0.8 时回填锚点空描述"""
    s = WorkspaceState()
    a = s.add_anchor("核心使命", "")  # 描述为空，等待回流
    assert a.description == ""
    s.add_evidence(a, "系统的核心目标是认知自组织")
    s.add_evidence(a, "系统的核心目标是认知自组织")  # 一致 → 成核
    # 两条一致 → confidence = min(2/2, 1.0) = 1.0 >= 0.8
    assert len(s.memories) == 1
    assert s.memories[0].confidence >= 0.8
    assert a.description == "系统的核心目标是认知自组织"


def test_nucleate_no_overwrite_manual_description():
    """尊重人工设定：锚点已有描述时不被结晶覆盖"""
    s = WorkspaceState()
    a = s.add_anchor("核心使命", "人工写好的描述")
    s.add_evidence(a, "系统的核心目标是认知自组织")
    s.add_evidence(a, "系统的核心目标是认知自组织")
    assert a.description == "人工写好的描述"


def test_conflict_detection_on_low_consistency():
    """群内自洽：同锚点 3 条互不相关证据 → 自动生成 Conflict"""
    s = WorkspaceState()
    a = s.add_anchor("混合主题", "一个容纳多主题的锚点")
    s.add_evidence(a, "苹果是一种常见的水果")
    s.add_evidence(a, "地球是太阳系中的一颗行星")
    s.add_evidence(a, "量子纠缠是一种非局域关联现象")
    # 三条证据互不相干 → 平均重叠度极低 < 0.2
    assert len(s.conflicts) == 1
    assert s.conflicts[0].anchor_a == a.id
    # stability 被下调
    assert a.stability < 1.0


def test_no_conflict_when_consistent():
    """一致证据不成冲突"""
    s = WorkspaceState()
    a = s.add_anchor("稳定主题", "desc")
    s.add_evidence(a, "用户偏好简洁输出")
    s.add_evidence(a, "用户偏好简洁输出")
    s.add_evidence(a, "用户偏好简洁输出")
    assert len(s.conflicts) == 0
