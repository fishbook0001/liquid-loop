from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid
import re
import json
import hashlib
import os
from typing import Optional, List, Dict, Any

# ── 生命周期效率编码常量（原 workspace 顶部，随 _derive_lifecycle_thresholds 迁入）──
LIFECYCLE_ACCURACY_PRIORITY = 0.85        # α：偏向保留准确性（防误归档）
LIFECYCLE_ENTROPY_COST_WEIGHT = 0.15      # β：熵增成本权重 = 归档地板（默认 0.15）
LIFECYCLE_MEMORY_HORIZON = 180 * 86400    # 回忆效用半衰期（与 rar.py 180-day decay 对齐）

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def uid() -> str:
    return uuid.uuid4().hex[:12]

def _derive_lifecycle_thresholds(
    alpha: float = LIFECYCLE_ACCURACY_PRIORITY,
    beta: float = LIFECYCLE_ENTROPY_COST_WEIGHT,
    horizon: float = LIFECYCLE_MEMORY_HORIZON,
) -> tuple[float, float]:
    """从 efficient-coding trade-off 推导 lifecycle 双判据。

    映射：cost = α·(decoding_accuracy) + β·(memory_entropy_cost)。
    归一化约束 α+β=1 → floor_weight = β（熵增成本权重即归档地板）；
    ttl_eps = horizon（回忆效用半衰期，accuracy 侧容忍上限）。
    默认参数精确复现 v1.7 经验值 0.15 / 180d，向后兼容。
    调参用 efficient-coding 语言：β↑→更激进归档；horizon↑→更长保留。
    """
    floor = beta
    ttl = horizon
    return floor, ttl

def _get_version() -> str:
    """从 pyproject.toml 读取版本号"""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text())
        return data.get("project", {}).get("version", "2.0.0")
    return "1.8.4"

def _tokenize(text: str) -> List[str]:
    """关键词分词（零依赖替代 sentence-transformers）

    混合策略：英文按单词分，中文逐字分。
    例: "飞哥液环认知" → ['飞','哥','液','环','认','知']
         "hello world" → ['hello','world']
    """
    tokens = []
    i = 0
    text = text.lower()
    while i < len(text):
        c = text[i]
        # 中文字符 → 逐字
        if '\u4e00' <= c <= '\u9fff':
            tokens.append(c)
            i += 1
        # 英文/数字 → 连续词
        elif c.isalnum():
            j = i
            while j < len(text) and text[j].isalnum():
                j += 1
            word = text[i:j]
            if len(word) > 1:
                tokens.append(word)
            i = j
        else:
            i += 1
    return tokens

def _keyword_overlap(a: str, b: str, cache: dict | None = None) -> float:
    """关键词重叠度（Jaccard 系数），支持缓存"""
    if not a or not b:
        return 0.0
    if cache is not None:
        import hashlib
        h1, h2 = hashlib.md5(a.encode()).hexdigest()[:8], hashlib.md5(b.encode()).hexdigest()[:8]
        key = f"{min(h1,h2)}:{max(h1,h2)}"  # str key，JSON 兼容
        if key in cache:
            return cache[key]
    import re
    tokens1 = set(re.findall(r"[一-鿿]|[a-zA-Z0-9]+", a.lower()))
    tokens2 = set(re.findall(r"[一-鿿]|[a-zA-Z0-9]+", b.lower()))
    if not tokens1 or not tokens2:
        return 0.0
    result = len(tokens1 & tokens2) / len(tokens1 | tokens2)
    if cache is not None:
        cache[key] = result
    return result

def _judge_answer(gold: str, answer: str) -> bool:
    """简单判定答案是否正确（关键词命中）"""
    if not gold or not answer:
        return False
    gold_lower = gold.lower()
    answer_lower = answer.lower()
    if gold_lower in answer_lower or answer_lower in gold_lower:
        return True
    gold_kw = set(_tokenize(gold))
    answer_kw = set(_tokenize(answer))
    if not gold_kw:
        return False
    hit = len(gold_kw & answer_kw) / len(gold_kw)
    return hit >= 0.3

def _dissolve_votes_path(root: Path) -> Path:
    return Path(root) / ".dissolve_votes.json"

def _load_dissolve_votes(root: Path) -> dict:
    p = _dissolve_votes_path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_dissolve_votes(root: Path, votes: dict) -> None:
    p = _dissolve_votes_path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(votes, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.getLogger(__name__).warning("dissolve_votes persist failed: %s", e)
