"""v2.1 候选①：低稳定性自动召回（active inference 工程版）——受控实验原型。

理论背书：预测编码 active inference——低精度预测应被主动采样确认/否证。
液环对应：stability 低的记忆 = 未经验证的预测，应自动进入复核队列。

规则（零向量/零 LLM，结构化可审计）：
  复核对象：tier=fact & support_count=0 & confidence<0.8 & last_reinforced=''
  建议动作：根据置信度与证据画像给出 确认 / 降级 / 归档复核 建议

只读 state.json（隔离，不写生产）。输出复核队列报告。
"""
import json, re
from collections import Counter

DEFAULT_STATE = "/Users/feixubuke/.liquidloop/memory/.liquid/state.json"

def is_verification_gap(m) -> bool:
    """误差回路缺口：预测未经验证（无支持证据、无强化、低置信）。"""
    return (m.get("tier") == "fact"
            and m.get("support_count", 0) == 0
            and m.get("last_reinforced") in ("", None)
            and m.get("confidence", 1.0) < 0.8)

def suggest_action(m):
    conf = m.get("confidence", 0.5)
    content = m.get("content", "")
    # 批量待核实标记 → 建议批量复核
    if "待核实" in content or "batch" in content:
        return "批量复核（确认或驳回，勿长期滞留）"
    if conf < 0.4:
        return "驳回/归档（低置信无证据）"
    if "蒸馏" in content or "共识" in content:
        return "确认（有蒸馏链，补证据后强化）"
    return "复核（确认→s+1，驳回→c+1）"

def run(state_path=DEFAULT_STATE, top_n=10):
    d = json.load(open(state_path))
    mems = d["memories"]
    gaps = [m for m in mems if is_verification_gap(m)]
    print("=== v2.1 低稳定性自动召回 · 误差回路缺口扫描 ===")
    print(f"总记忆: {len(mems)} | 复核缺口: {len(gaps)} ({len(gaps)/len(mems)*100:.0f}%)")
    # 缺口分组
    by_sig = Counter(re.match(r"\[?(marvis fact batch|distill|distilled)", m["content"]).group(1)
                     if re.match(r"\[?(marvis fact batch|distill|distilled)", m["content"]) else "other"
                     for m in gaps)
    print(f"缺口类型: {dict(by_sig)}")
    print()
    print("建议动作分布:")
    acts = Counter(suggest_action(m) for m in gaps)
    for act, n in acts.most_common():
        print(f"  {n:>3}  {act}")
    print()
    print(f"复核队列样本（top {top_n}）:")
    for i, m in enumerate(gaps[:top_n]):
        c = m["content"]
        c = c[:70].replace("\n", " ")
        print(f"  [{i+1}] conf={m.get('confidence')} s={m.get('support_count')} → {suggest_action(m)}")
        print(f"      {c}")
    # 输出复核队列 JSON（供后续人工/流程消费）
    out = "/Users/feixubuke/output/液环v2.1_复核队列_低稳定性召回.json"
    queue = [{"id": m["id"], "confidence": m.get("confidence"),
              "support_count": m.get("support_count", 0),
              "action": suggest_action(m), "content": m["content"][:200]}
             for m in gaps]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=1)
    print(f"\n复核队列已落盘: {out}（{len(queue)} 条）")

if __name__ == "__main__":
    run()
