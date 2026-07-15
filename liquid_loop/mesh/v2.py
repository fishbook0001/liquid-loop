#!/usr/bin/env python3
"""
liquid_loop_mesh_v2.py — 液环 MESH 进阶版（v2）参考实现
======================================================
把"共识协议规范"从文档落地为可复用代码。零向量哲学：一致性判定走
结构化精确相等 + 审计链哈希，绝不引入任何 embedding / 相似度。

职责：
  1. validate_evidence()      — agent 侧契约自检（结构化证据 schema）
  2. check_contract()         — 双向契约合规检查（身份/写/消费/冲突）
  3. compute_cci()            — 主体间性共识指数（记忆层）
  4. cognitive_health()       — 统一认知健康仪表盘（CCI / drift / H_e / P_mem）
  5. detect_conflict()        — 同锚点冲突证据检测
  6. fetch_state()            — 从 8790 REST /list 拉取当前状态

依赖：标准库 + 可选 liquid_loop（仅类型提示）。可直接 import 使用，
也可 `python3 liquid_loop_mesh_v2.py` 连 8790 打印健康报告。
"""
from __future__ import annotations
import json
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# ── agent-mesh 注册表（接入 agent 的唯一 id 白名单）──────────────
REGISTRY = {"workbuddy", "vera", "qwenpaw", "marvis", "parlant-ctrl", "cawpaw", "tabbit", "llama"}

CLAIM_TYPES = {"fact", "conclusion", "preference", "conflict"}
SCOPES = {"private", "consensus"}


# ── 1. 结构化证据 schema 校验 ──────────────────────────────────
@dataclass
class Evidence:
    agent_id: str
    content: str
    category: str = "fact"
    claim_type: str = "fact"
    value: Optional[float] = None
    session_id: str = ""
    prev_hash: str = ""
    id: str = ""

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "content": self.content,
            "category": self.category,
            "claim_type": self.claim_type,
            "value": self.value,
            "session_id": self.session_id,
            "prev_hash": self.prev_hash,
            "id": self.id,
        }


def validate_evidence(ev: dict) -> tuple[bool, list[str]]:
    """agent 侧契约自检：返回 (ok, 错误列表)。"""
    errs: list[str] = []
    if not ev.get("agent_id"):
        errs.append("agent_id 必填（共用机制要求声明写入者标识）")
    elif ev["agent_id"] not in REGISTRY:
        errs.append(f"agent_id '{ev['agent_id']}' 未在 mesh 注册表（禁止匿名污染）")
    if not ev.get("content"):
        errs.append("content 必填（一致性比对键）")
    ct = ev.get("claim_type", "fact")
    if ct not in CLAIM_TYPES:
        errs.append(f"claim_type '{ct}' 非法，须为 {sorted(CLAIM_TYPES)}")
    # content 精确相等判定 → 禁止任何 embedding/相似度
    if ev.get("content") and not isinstance(ev["content"], str):
        errs.append("content 必须为精确字符串（零向量一致性比对键）")
    return (len(errs) == 0, errs)


# ── 2. 双向契约合规检查 ────────────────────────────────────────
def check_contract(agent_id: str, did_recall: bool, wrote_structured: bool) -> tuple[bool, list[str]]:
    """①身份 ②结构化写 ③主动消费 ④冲突响应(由 detect_conflict 触发)。"""
    errs: list[str] = []
    if agent_id not in REGISTRY:
        errs.append("① 身份未声明/未注册")
    if not wrote_structured:
        errs.append("② 未结构化写入（缺 claim_type/session_id/prev_hash）")
    if not did_recall:
        errs.append("③ 会话未主动消费 consensus 视图（退化成孤岛）")
    return (len(errs) == 0, errs)


# ── 3. CCI 主体间性共识指数（记忆层）────────────────────────────
def compute_cci(items: list[dict]) -> dict:
    """CCI_mem = |consensus| / (|consensus| + |private|)。"""
    cons = [x for x in items if x.get("scope") == "consensus"]
    priv = [x for x in items if x.get("scope") == "private"]
    denom = len(cons) + len(priv)
    cci = (len(cons) / denom) if denom else 0.0
    # 共识强度 secondary：平均 contributors 基数
    strengths = [len(x.get("contributors", [])) for x in cons] or [0]
    avg_strength = sum(strengths) / len(strengths) if strengths else 0
    return {
        "cci": round(cci, 4),
        "consensus": len(cons),
        "private": len(priv),
        "avg_contributors": round(avg_strength, 2),
    }


# ── 4. 统一认知健康仪表盘 ─────────────────────────────────────
def cognitive_health(items: list[dict], drift_rate: float = 0.0,
                     H_e: float = 0.0, P_mem: float = 0.0) -> dict:
    cci = compute_cci(items)
    evs = [x for x in items if x.get("type") == "evidence"]
    from collections import Counter
    by_agent = Counter(x.get("agent_id") for x in evs)
    return {
        "CCI": cci["cci"],
        "consensus_crystals": cci["consensus"],
        "private_crystals": cci["private"],
        "drift_rate": drift_rate,
        "H_e": H_e,
        "P_mem": P_mem,
        "total_evidence": len(evs),
        "agents": dict(by_agent),
    }


# ── 5. 冲突检测 ───────────────────────────────────────────────
def detect_conflict(items: list[dict]) -> list[dict]:
    """同锚点下 claim_type=conflict 或同 content 反义 → 标记冲突 agent。"""
    conflicts = []
    by_cat = {}
    for x in items:
        if x.get("type") in ("evidence", "memory"):
            by_cat.setdefault(x.get("category", "?"), []).append(x)
    for cat, recs in by_cat.items():
        conflict_recs = [r for r in recs if r.get("claim_type") == "conflict"]
        if conflict_recs:
            agents = sorted({r.get("agent_id") for r in conflict_recs if r.get("agent_id")})
            conflicts.append({"anchor": cat, "conflict_agents": agents,
                              "kind": "explicit_conflict"})
    return conflicts


# ── 6. 从 8790 拉取状态 ───────────────────────────────────────
def fetch_state(base: str = "http://127.0.0.1:8790") -> list[dict]:
    req = urllib.request.Request(base + "/list", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.load(r)
    return d if isinstance(d, list) else d.get("result", d.get("items", []))


# ── CLI：打印健康报告 ──────────────────────────────────────────
def main():
    try:
        items = fetch_state()
    except Exception as e:
        print(f"[ERR] 无法连接 8790: {e}")
        sys.exit(1)
    health = cognitive_health(items)
    print("=== 液环 MESH v2 认知健康仪表盘 ===")
    for k, v in health.items():
        print(f"  {k:18}: {v}")
    conf = detect_conflict(items)
    if conf:
        print("\n=== 冲突锚点 ===")
        for c in conf:
            print(" ", c)
    else:
        print("\n[OK] 无显式冲突")


if __name__ == "__main__":
    main()
