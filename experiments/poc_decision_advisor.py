#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""poc_decision_advisor — P1 零风险 PoC：AgentJev 语义判断质量对照实测

数据源：液环真实 state（~/.liquidloop/memory/.liquid/state.json，只读）。
测试集（全部真实数据）：
  T1 冲突正样本：state.conflicts[0] 系统已判定的冲突锚点对 → 期望 boolean=true
  T2 无关负样本：随机不相干锚点对 ×2 → 期望 boolean=false
  T3 去重决策  ：同锚点两条证据（期望 merge）×2 + 跨锚点两条（期望 keep）×2
判定：AgentJev 对中文液环记忆的语义判断是否达到「可进 P2」水位（≥5/7）。
本脚本只读 state.json，不写液环任何状态。
"""
import json
import os
import random
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from decision_advisor import AgentJevAdvisor  # noqa: E402

STATE_PATH = os.path.expanduser("~/.liquidloop/memory/.liquid/state.json")
REPORT_PATH = os.path.expanduser(
    "~/output/AgentJev_液环加持_PoC_" + datetime.now().strftime("%Y-%m-%d") + ".md")

Q_CONFLICT = ("Do these two memory entries describe a semantic CONFLICT "
              "or redundancy that requires governance action (merge/supersede)?")
Q_DEDUP = "How should these two memory entries be handled?"
OPT_MERGE = "merge: they are semantically duplicate, consolidate into one anchor"
OPT_KEEP = "keep: they are semantically distinct, keep as separate entries"


def load_state():
    with open(STATE_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return d["anchors"], d["evidences"], d.get("conflicts") or []


def anchor_by_id(anchors, aid):
    for a in anchors:
        if a.get("id") == aid:
            return a
    return None


def anchor_sample(a, evs, n=1, limit=350):
    """锚点代表文本：优先取其下 evidence content，缺失则退 description。"""
    texts = [(e.get("content") or "").strip()[:limit]
             for e in evs if e.get("anchor_id") == a.get("id")
             and (e.get("content") or "").strip()]
    texts = [t for t in texts if t][:n]
    desc = (a.get("description") or "").strip()[:limit]
    return texts or ([desc] if desc else [a.get("name") or ""])


def pair_state(text_a, text_b):
    return f"[ENTRY-A] {text_a}\n[ENTRY-B] {text_b}"


def build_cases(anchors, evs, conflicts):
    cases = []  # (case_id, kind, state, question, options|None, expect)

    # T1 已知冲突正样本
    c = conflicts[0] if conflicts else None
    if c:
        ta, tb = "", ""
        aa, ab = anchor_by_id(anchors, c.get("anchor_a")), anchor_by_id(anchors, c.get("anchor_b"))
        if aa: ta = anchor_sample(aa, evs)[0]
        if ab: tb = anchor_sample(ab, evs)[0]
        if not (ta and tb):
            ta, tb = str(c.get("description", "")), str(c.get("description", ""))
        if ta and tb:
            cases.append(("T1-known-conflict", "boolean", pair_state(ta, tb),
                          Q_CONFLICT, None, True))

    # T2 无关负样本 ×2（随机取 evidence 文本差异大的两个锚点）
    rich = [a for a in anchors
            if any(e.get("anchor_id") == a.get("id") and (e.get("content") or "").strip()
                   for e in evs)]
    rnd = random.Random(42)
    tries = 0
    negs = 0
    while negs < 2 and tries < 60 and len(rich) >= 2:
        tries += 1
        a, b = rnd.sample(rich, 2)
        ta, tb = anchor_sample(a, evs)[0], anchor_sample(b, evs)[0]
        # 粗过滤：字符级重合度过高的对丢弃（避免抽到隐性同主题）
        overlap = len(set(ta) & set(tb)) / max(1, min(len(set(ta)), len(set(tb))))
        if overlap > 0.45:
            continue
        cases.append((f"T2-unrelated-{negs + 1}", "boolean", pair_state(ta, tb),
                      Q_CONFLICT, None, False))
        negs += 1

    # T3 去重决策：同锚点对（expect merge）×2 + 跨锚点对（expect keep）×2
    multi = [a for a in anchors
             if len([e for e in evs if e.get("anchor_id") == a.get("id")
                     and (e.get("content") or "").strip()]) >= 2]
    same = 0
    for a in multi[:8]:
        if same >= 2:
            break
        ts = [(e.get("content") or "").strip()[:300]
              for e in evs if e.get("anchor_id") == a.get("id")
              and (e.get("content") or "").strip()]
        if len(ts) < 2:
            continue
        cases.append((f"T3-same-anchor-{same + 1}", "choice",
                      pair_state(ts[0], ts[1]), Q_DEDUP,
                      {"merge": OPT_MERGE, "keep": OPT_KEEP}, "merge"))
        same += 1
    cross = 0
    while cross < 2 and len(rich) >= 2:
        a, b = rnd.sample(rich, 2)
        ta, tb = anchor_sample(a, evs)[0], anchor_sample(b, evs)[0]
        overlap = len(set(ta) & set(tb)) / max(1, min(len(set(ta)), len(set(tb))))
        if overlap > 0.45:
            continue
        cases.append((f"T3-cross-anchor-{cross + 1}", "choice",
                      pair_state(ta, tb), Q_DEDUP,
                      {"merge": OPT_MERGE, "keep": OPT_KEEP}, "keep"))
        cross += 1
    return cases


def main():
    adv = AgentJevAdvisor()
    health = adv.health()
    if not health:
        print(f"ABORT: AgentJev 服务不可达（{adv.last_error}）→ fail-open 生效，纯规则路径不受影响")
        return 1
    print(f"health ok: model={health.get('model')} wall基线 types={health.get('types')}")

    anchors, evs, conflicts = load_state()
    cases = build_cases(anchors, evs, conflicts)
    print(f"真实样本用例: {len(cases)} 个（state 只读）\n")

    rows, correct = [], 0
    for cid, kind, state, question, options, expect in cases:
        if kind == "boolean":
            r = adv.boolean(state, question)
            got = None if r is None else r["value"]
            ok = got is not None and got == expect
        else:
            r = adv.choice(state, question, options)
            got = None if r is None else r["best"]
            ok = got is not None and got == expect
        correct += ok
        rows.append({
            "case": cid, "kind": kind, "expect": expect, "got": got,
            "ok": ok, "detail": None if r is None else {
                k: r[k] for k in ("prob_true", "top_probability", "margin",
                                  "probs", "wall_ms") if k in r},
            "error": adv.last_error,
        })
        status = "PASS" if ok else ("SKIP(fail-open)" if got is None else "FAIL")
        print(f"[{status:15}] {cid:22} expect={expect!s:6} got={got!s:6}"
              + (f" wall={r['wall_ms']}ms" if r else f" err={adv.last_error}"))

    total = len(rows)
    hit = correct
    skip = sum(1 for x in rows if x["got"] is None)
    verdict = "达标(可进P2)" if hit >= 5 else "不达标(暂缓P2)"
    print(f"\n=== 结果: {hit}/{total} 正确, fail-open 跳过 {skip} → {verdict} ===")

    # 落盘报告（只读液环，报告写 ~/output/）
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(f"# AgentJev × 液环机制加持 · P1 PoC 报告\n\n")
        f.write(f"- 时间：{datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"- 数据源：液环真实 state（{STATE_PATH}，只读；anchors={len(anchors)} evidences={len(evs)} conflicts={len(conflicts)}）\n")
        f.write(f"- 服务：AgentJev-0.6B @ 127.0.0.1:8149（advisory·fail-open 旁路）\n")
        f.write(f"- 判定：**{hit}/{total} 正确（fail-open 跳过 {skip}）→ {verdict}**\n\n")
        f.write("| 用例 | 类型 | 期望 | 实得 | 判定 | 明细 |\n|---|---|---|---|---|---|\n")
        for x in rows:
            d = x["detail"] or {}
            brief = ", ".join(f"{k}={v}" for k, v in d.items()
                              if k in ("prob_true", "top_probability", "margin", "wall_ms"))
            if not brief and x["error"]:
                brief = x["error"]
            mark = "✅" if x["ok"] else ("⏭️" if x["got"] is None else "❌")
            f.write(f"| {x['case']} | {x['kind']} | {x['expect']} | {x['got']} "
                    f"| {mark} | {brief} |\n")
        f.write("\n## 结论\n")
        f.write(f"- 中文液环记忆喂英文底座 System-1 决策头的语义判断命中率 {hit}/{total}。\n")
        f.write("- advisory/fail-open 全程未触碰液环核心库与 state（只读）。\n")
        f.write("- 决策：达标则可议 P2（结晶语义 gate，需备份+回归+飞哥授权）；不达标则中文语义为硬边界，暂缓。\n")
    print(f"报告已落盘: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
