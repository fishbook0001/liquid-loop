"""液环长会话提取式压缩落地测试（蒸馏自 Octomind condense.rs）"""
from liquid_loop.context_compress import (
    ExtractiveCondenser,
    compress_context,
    estimate_tokens,
    structured_note,
)


def _big_text() -> str:
    """单条 ≥ 512 token（≈2048 字符）才入候选。"""
    return (
        "error: connection refused\n"
        + "okay sure let me think\n" * 5
        + "def fix(): return True\n/path/to/module.py:42 changed\n"
        + "result: 3 rows updated\n"
        + "blah blah noise line\n" * 100
    )


# ───────────── 核心不变量 ─────────────

def test_no_trigger_below_threshold():
    cond = ExtractiveCondenser(tokens_threshold=100000)
    texts = ["short line one", "short line two"]
    out, rep = cond.condense_round([{"tool": "", "content": t} for t in texts])
    assert rep.triggered is False
    assert rep.condensed == 0


def test_candidate_below_min_not_touched():
    # 整轮超阈值，但单条 < 512 token → 不压缩任何条
    small = "error: x\n" * 10  # 远小于 512 token
    cond = ExtractiveCondenser(tokens_threshold=1)
    out, rep = cond.condense_round([{"tool": "shell", "content": small}])
    assert rep.triggered is True
    assert rep.candidates == 0
    assert rep.condensed == 0


def test_condense_saves_tokens():
    cond = ExtractiveCondenser(tokens_threshold=200)
    out, rep = cond.condense_round([{"tool": "shell", "content": _big_text()}])
    assert rep.triggered is True
    assert rep.candidates == 1
    assert rep.condensed == 1
    assert rep.saved_tokens > 0
    assert estimate_tokens(out[0]["content"]) < estimate_tokens(_big_text())


def test_no_gain_keeps_original():
    # 全是高价值短行（无可裁剪噪声）→ 不应越压越大，保留原文
    cond = ExtractiveCondenser(tokens_threshold=1)
    val = "def f(): return 1\n" * 5  # 每条短且高价值，压缩无收益
    out, rep = cond.condense_round([{"tool": "x", "content": val}])
    # 单条 < 512 token 不入候选，直接 untouched
    assert rep.candidates == 0


def test_fail_open_on_bad_input():
    cond = ExtractiveCondenser(tokens_threshold=1)
    # content 为非 str 真值（如 int）→ estimate_tokens 抛 TypeError，
    # 被 try 捕获，fail-open 返回原样（不崩、不丢数据）
    bad = [{"tool": "x", "content": 12345}]
    out, rep = cond.condense_round(bad)
    assert rep.notes and "fail-open" in rep.notes[0]
    assert out == bad


def test_none_content_safe_no_trigger():
    # content=None 视为空，安全返回未触发（非异常路径）
    cond = ExtractiveCondenser(tokens_threshold=1)
    out, rep = cond.condense_round([{"tool": "x", "content": None}])
    assert rep.triggered is False
    assert rep.notes == []


# ───────────── 便捷入口 + env 阈值 ─────────────

def test_compress_context_env_threshold(monkeypatch):
    monkeypatch.setenv("LIQUID_CONTEXT_COMPRESS_TOKENS", "200")
    out, rep = compress_context([_big_text()])
    assert rep.triggered is True
    assert rep.condensed == 1


def test_compress_context_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LIQUID_CONTEXT_COMPRESS_TOKENS", raising=False)
    texts = [_big_text()]
    out, rep = compress_context(texts)
    assert rep.triggered is False  # 默认 0 = 不压缩
    assert out == texts


def test_compress_context_multi_round():
    # 多条文本作为一轮，总 token 超阈值才触发
    cond = ExtractiveCondenser(tokens_threshold=300)
    texts = [_big_text(), _big_text()]
    out, rep = cond.condense_round([{"tool": "", "content": t} for t in texts])
    assert rep.triggered is True
    assert rep.candidates == 2
    assert rep.condensed == 2


# ───────────── RE-TRAC 同构：结构化三组分笔记 ─────────────

def test_structured_note_buckets():
    texts = [
        "result: 3 rows updated successfully",
        "error: connection refused during retry",
        "/path/to/module.py:42 the handler logic",
        "todo: verify the edge case still holds",
    ]
    note = structured_note(texts, threshold=0)  # 不压缩，直接分桶
    assert "result: 3 rows updated successfully" in note["answer"]
    assert any("error: connection refused" in x for x in note["open"])
    assert any("todo: verify" in x for x in note["open"])  # todo → open
    assert any("/path/to/module.py" in x for x in note["evidence"])


def test_structured_note_no_loss_when_disabled(monkeypatch):
    # 默认不压缩时，所有行都进入某一桶（保底 evidence），不丢行
    monkeypatch.delenv("LIQUID_CONTEXT_COMPRESS_TOKENS", raising=False)
    texts = ["result: ok", "error: x", "plain line"]
    note = structured_note(texts)
    total = len(note["answer"]) + len(note["evidence"]) + len(note["open"])
    assert total == 3


def test_structured_note_fail_open():
    # 坏输入（非 str）→ 分桶循环抛 AttributeError → fail-open 返回空桶，不崩
    note = structured_note([12345], threshold=0)
    assert note == {"answer": [], "evidence": [], "open": []}
