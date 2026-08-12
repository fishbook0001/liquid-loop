"""procedural_memory 测试：EvoC2F 三关准入 + 分阶段部署 + Skill1 任务路由 + RE-TRAC 笔记。

守则用：禁向量(结构化 tag 匹配)、确定性、fail-open、零丢失。
所有用例用 tmp_path 隔离，避免 sidecar 跨运行污染。
"""
import json
from pathlib import Path

import pytest

from liquid_loop.procedural_memory import (
    ProceduralMemory,
    ProceduralRegistry,
    procmem_admit,
    procmem_recall,
)


@pytest.fixture
def reg(tmp_path):
    return ProceduralRegistry(tmp_path)


def test_admit_functional_pass_goes_shadow(reg):
    rep = reg.admit("s1", ["deploy", "web"], "run deploy.sh --env prod", evidence="done")
    assert rep["ok"] is True
    assert rep["gate_status"] == "shadow"           # 无使用数据→shadow 观察
    assert rep["gates"]["functional"]["score"] >= 0.4
    assert reg._items["s1"].usable is False          # shadow 尚不可召回


def test_admit_functional_fail_rejected(reg):
    rep = reg.admit("bad", ["x"], "太短")            # 功能门不过
    assert rep["gate_status"] == "rejected"
    assert reg._items["bad"].usable is False
    assert "bad" in reg._items                        # 零丢失：rejected 仍保留可审计


def test_contract_gate_checks_evidence(reg):
    rep = reg.admit(
        "c1", ["query"], "run query.py --sql x",
        evidence="returned rows",
        contract={"outputs": ["rows", "schema"]},
    )
    assert rep["gates"]["contract"]["score"] < 1.0   # 只涵盖 rows，未涵盖 schema


def test_staged_promotion_shadow_canary_active(reg):
    reg.admit("p1", ["build"], "run make all")
    for _ in range(3):                               # 达 MIN_USES_SHADOW 且全成功
        reg.record_use("p1", True)
    assert reg._items["p1"].gate_status == "canary"
    for _ in range(3):                               # 达 MIN_USES_ACTIVE
        reg.record_use("p1", True)
    assert reg._items["p1"].gate_status == "active"
    assert reg._items["p1"].usable is True


def test_regression_fail_rejected(reg):
    reg.admit("r1", ["x"], "run risky.sh")
    reg.record_use("r1", True)
    reg.record_use("r1", False)                      # 成功率 0.5 < 0.6
    reg.record_use("r1", False)
    assert reg._items["r1"].gate_status == "rejected"


def test_irreversible_capped_canary_needs_human(reg):
    reg.admit("i1", ["transfer"], "run transfer_funds.sh", irreversible=True)
    for _ in range(6):                               # 充分成功使用
        reg.record_use("i1", True)
    pm = reg._items["i1"]
    assert pm.gate_status == "canary"                # 不可逆封顶 canary
    assert pm.requires_human is True
    pro = reg.promote("i1", by="human")              # 人工放行(↔ ops_gate 不可逆确认)
    assert pro["ok"] is True
    assert reg._items["i1"].gate_status == "active"
    assert reg._items["i1"].requires_human is False


def test_recall_for_task_tag_match_and_credit(reg):
    reg.admit("deploy_web", ["deploy", "web"], "run deploy.sh")
    for _ in range(6):
        reg.record_use("deploy_web", True)           # → active，成功率 1.0
    reg.admit("build_c", ["build", "c"], "run make")
    for _ in range(6):
        reg.record_use("build_c", True)
    out = reg.recall_for_task("deploy web service")
    ids = [d["skill_id"] for d in out]
    assert "deploy_web" in ids
    assert out[0]["skill_id"] == "deploy_web"        # 选择信用更高→排前
    assert out[0]["match_score"] > 0


def test_recall_excludes_non_usable(reg):
    reg.admit("pending_skill", ["x"], "run x.sh")    # shadow，未晋级
    assert reg.recall_for_task("x task") == []       # shadow 不可召回(禁噪声)


def test_retrac_note_populated(reg):
    reg.admit("n1", ["y"], "run y.sh", evidence="fixed bug in parser.py; result resolved")
    note = reg._items["n1"].note
    combined = note["answer"] + note["evidence"] + note["open"]
    assert any("parser.py" in s for s in combined)
    assert any("resolved" in s.lower() or "result" in s.lower() for s in combined)


def test_persistence_sidecar_written(tmp_path):
    reg = ProceduralRegistry(tmp_path)
    reg.admit("persist1", ["z"], "run z.sh")
    reg._save()
    sidecar = tmp_path / ".liquid" / "procedural.json"
    assert sidecar.exists()
    data = json.load(open(sidecar))
    assert "persist1" in data["skills"]


def test_no_vector_recall_is_structural(reg):
    reg.admit("a", ["alpha", "beta"], "run a.sh")
    reg.admit("b", ["gamma"], "run b.sh")
    for _ in range(6):
        reg.record_use("a", True)
        reg.record_use("b", True)
    out = reg.recall_for_task("alpha")
    ids = [d["skill_id"] for d in out]
    # a 与 b 复用率完全相同(utilization 信用持平)，但 a 因标签命中(选择信用)
    # 排第一 → 排序由结构化 tag 匹配驱动，而非向量相似度(零向量铁律)
    assert ids[0] == "a"


def test_fail_open_bad_workspace(tmp_path):
    bad = tmp_path / ".liquid" / "procedural.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not valid json", encoding="utf-8")
    r = ProceduralRegistry(tmp_path)
    assert r._items == {}                            # 损坏→安全空
    assert r.recall_for_task("anything") == []


def test_convenience_functions(tmp_path):
    rep = procmem_admit(tmp_path, "cv", ["tag"], "run cv.sh", evidence="ok")
    assert rep["ok"] is True
    out = procmem_recall(tmp_path, "tag")
    assert isinstance(out, list)


def test_readmit_preserves_progression(reg):
    """P1 修复：已验证(active)技能重准入保留晋级进度与历史，不降回 shadow。"""
    reg.admit("r1", ["y"], "run y.sh")
    for _ in range(6):
        reg.record_use("r1", True)                   # → active，use_count=6
    assert reg._items["r1"].gate_status == "active"
    rep = reg.admit("r1", ["y", "v2"], "run y.sh v2")  # 重准入(仅更新内容)
    assert rep["gate_status"] == "active"
    assert reg._items["r1"].use_count == 6           # 历史保留
    assert reg._items["r1"].task_tags == ["y", "v2"]
    assert reg._items["r1"].invocation == "run y.sh v2"


def test_readmit_rejected_is_fresh_attempt(reg):
    """rejected 重准入视为新尝试(重置为 shadow)。"""
    reg.admit("r2", ["x"], "太短")                   # 功能门不过 → rejected
    assert reg._items["r2"].gate_status == "rejected"
    rep = reg.admit("r2", ["x"], "run x.sh --good")  # 修正后重准入
    assert rep["gate_status"] == "shadow"
    assert reg._items["r2"].use_count == 0


def test_readmit_regression_rejected_resets_counters(reg):
    """回归拒(曾用过)技能重准入 → 计数器清零，避免旧计数污染新 shadow 的晋级判定。"""
    reg.admit("r3", ["x"], "run x.sh")
    for _ in range(3):
        reg.record_use("r3", True)                   # → canary
    for _ in range(3):
        reg.record_use("r3", False)                  # → rejected, use_count=6
    assert reg._items["r3"].gate_status == "rejected"
    rep = reg.admit("r3", ["x"], "run x.sh --fixed") # 修正重准入
    assert rep["gate_status"] == "shadow"
    assert reg._items["r3"].use_count == 0           # 计数器清零(全新尝试)
    assert reg._items["r3"].success_count == 0


def test_canary_regression_rejected(reg):
    """P1 修复：canary 后期回归(成功率<阈值且数据充足)→ 拒绝，防已放行技能劣化。"""
    reg.admit("c2", ["x"], "run x.sh")
    for _ in range(3):
        reg.record_use("c2", True)                   # → canary
    assert reg._items["c2"].gate_status == "canary"
    for _ in range(3):
        reg.record_use("c2", False)                  # 成功率 0.5 < 0.6，达 MIN_USES_ACTIVE
    assert reg._items["c2"].gate_status == "rejected"


def test_contract_non_dict_rejected(reg):
    """P2 修复：contract 非 dict 返回明确错误而非内部异常。"""
    rep = reg.admit("cd1", ["z"], "run z.sh", contract="abc")
    assert rep["ok"] is False
    assert "dict" in rep["error"]


def test_promote_non_irreversible_rejected(reg):
    """promote 仅对不可逆技能生效；普通技能返回不可晋级错误。"""
    reg.admit("pn1", ["w"], "run w.sh")
    for _ in range(3):
        reg.record_use("pn1", True)                  # → canary(非不可逆)
    assert reg._items["pn1"].gate_status == "canary"
    rep = reg.promote("pn1", by="human")
    assert rep["ok"] is False
    assert reg._items["pn1"].gate_status == "canary"
