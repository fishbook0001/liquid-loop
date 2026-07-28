#!/usr/bin/env python3
"""液环 · 稳态可控液态杠杆  Liquid Lever（单旋钮 λ + 稳态守卫自动降档）
========================================================
动机（飞哥 2026-07-29：「我要稳态可控的液态杠杆」）：
  liquid_reweight 有 5 个自由参数（beta / topo_thresh / amp_cap /
  min_activation / τ带），散着调不可控、不可审计。本模块把它们收敛为
  **一个杠杆旋钮 λ∈[0,1]**，并加稳态守卫（HomeostasisGuard）：
  指标越界 → 自动降档 λ → 审计留痕 → 最坏退化为朴素召回（零风险地板）。

核心设计（三条铁律内建）：
  ① 单旋钮联动映射：λ=1 = 已 A/B 实证的甜区上限（97% 精度保留），
     λ=0 = beta=0 → liquid_recall 数学上恒等于 plain_recall（零风险地板）。
     所有派生参数单调、有界，不越已验证的甜区。
        beta(λ)           = 0.6·λ                  （唤醒强度）
        amp_cap(λ)        = 0.5·λ                  （传播幅度封顶）
        topo_thresh(λ)    = 0.35 − 0.15·λ          （λ小→连边更严）
        min_activation(λ) = 0.2 + 0.2·(1−λ)        （λ小→护栏更严）
        τ带(λ)            = [τmax − λ·(τmax−τmin), τmax]（λ小→带宽收窄→更黏滞）
  ② 稳态守卫（结构化指标，守禁向量）：
        precision_hold : liquid top-k 对 plain top-k 的保留率（A/B 同源指标）
        spread_ratio   : 激活>min_activation 的锚点占比（过激活=扩散失稳）
     任一越界 → λ ← λ·0.5（降档），连续合格 N 次 → λ 缓慢回升（×1.1，封顶 λ_target）。
     非对称速率（快降慢升）= 黏滞稳态，同 τ(x) 哲学。
  ③ 审计链：每次守卫动作记 {ts, reason, λ_old→λ_new, metrics}，
     随激活态一并 save/load（单一事实源），可回溯可复盘。

落地方式：组合（不改 LiquidReweight 一行），客户端纯本地，不碰 8790 server。

用法：
  python3 liquid_lever.py selftest    # 纯逻辑单测（不写 8790）
"""
import os
import sys
import json
import time
import argparse

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from liquid_loop.liquid_reweight import LiquidReweight, TAU_MIN, TAU_MAX
else:
    from .liquid_reweight import LiquidReweight, TAU_MIN, TAU_MAX

# 甜区上限（= liquid_reweight 已 A/B 实证的默认值，λ=1 时恰好等于它们）
BETA_MAX = 0.6
AMP_CAP_MAX = 0.5
TOPO_THRESH_MIN = 0.20   # λ=1 时的最宽松连边（已验证）
TOPO_THRESH_MAX = 0.35   # λ=0 时的最严连边
MIN_ACT_FLOOR = 0.2      # λ=1 时的护栏（已验证甜区）
MIN_ACT_CEIL = 0.4       # λ=0 时的最严护栏


def lever_params(lam: float) -> dict:
    """λ∈[0,1] → 5 参数联动映射。全单调、全有界、λ=1 恰为已验证甜区。"""
    lam = max(0.0, min(1.0, lam))
    return {
        "beta": round(BETA_MAX * lam, 4),
        "amp_cap": round(AMP_CAP_MAX * lam, 4),
        "topo_thresh": round(TOPO_THRESH_MAX - (TOPO_THRESH_MAX - TOPO_THRESH_MIN) * lam, 4),
        "min_activation": round(MIN_ACT_FLOOR + (MIN_ACT_CEIL - MIN_ACT_FLOOR) * (1 - lam), 4),
        "tau_min": round(TAU_MAX - lam * (TAU_MAX - TAU_MIN), 4),
        "tau_max": TAU_MAX,
    }


class HomeostasisGuard:
    """稳态守卫：两个结构化指标越界 → 快降；连续合格 → 慢升。守禁向量。"""

    def __init__(self, precision_floor: float = 0.90, spread_cap: float = 0.35,
                 recover_after: int = 3, lam_floor: float = 0.0):
        self.precision_floor = precision_floor  # liquid 对 plain top-k 保留率下限
        self.spread_cap = spread_cap            # 激活扩散占比上限
        self.recover_after = recover_after      # 连续合格 N 次才回升
        self.lam_floor = lam_floor
        self._ok_streak = 0
        self.audit: list = []                   # 审计链

    def check(self, lam: float, lam_target: float, metrics: dict) -> float:
        """返回调整后的 λ。快降(×0.5)慢升(×1.1)，非对称黏滞。"""
        p = metrics.get("precision_hold", 1.0)
        s = metrics.get("spread_ratio", 0.0)
        reason = None
        if p < self.precision_floor:
            reason = f"precision_hold {p:.3f} < {self.precision_floor}"
        elif s > self.spread_cap:
            reason = f"spread_ratio {s:.3f} > {self.spread_cap}"

        new_lam = lam
        if reason:
            self._ok_streak = 0
            new_lam = max(self.lam_floor, round(lam * 0.5, 4))
            if new_lam < 0.05:
                new_lam = self.lam_floor  # 太低直接落地板=朴素基线
        else:
            self._ok_streak += 1
            if self._ok_streak >= self.recover_after and lam < lam_target:
                new_lam = min(lam_target, round(max(lam, 0.05) * 1.1, 4))
                reason = f"recovered ({self._ok_streak} ok streak)"
                self._ok_streak = 0

        if new_lam != lam or reason:
            self.audit.append({
                "ts": int(time.time()),
                "lam_old": lam, "lam_new": new_lam,
                "reason": reason or "hold",
                "metrics": {k: round(v, 4) for k, v in metrics.items()},
            })
        return new_lam


class LiquidLever:
    """稳态可控液态杠杆：单旋钮 λ 驱动 LiquidReweight，守卫自动降档。

    λ=0 → 数学上恒等于朴素召回（零风险地板）
    λ=1 → 已 A/B 实证甜区上限
    守卫越界 → λ 快降(×0.5)；连续合格 → 慢升(×1.1) 回 λ_target
    """

    def __init__(self, lam: float = 0.5, persist_path: str = None,
                 half_life: float = 86400, guard: HomeostasisGuard = None):
        self.lam_target = max(0.0, min(1.0, lam))   # 用户意图档位
        self.lam = self.lam_target                  # 当前生效档位（守卫可降）
        self.persist_path = persist_path
        self.half_life = half_life
        self.guard = guard or HomeostasisGuard()
        self.engine = self._make_engine(self.lam)
        self._anchors_cache: list = []

    def _make_engine(self, lam: float) -> LiquidReweight:
        p = lever_params(lam)
        return LiquidReweight(
            tau_min=p["tau_min"], tau_max=p["tau_max"], beta=p["beta"],
            topo_thresh=p["topo_thresh"], amp_cap=p["amp_cap"],
            min_activation=p["min_activation"],
            persist_path=None,  # 持久化由 Lever 统一管（λ+audit+激活一体落盘）
            half_life=self.half_life,
        )

    def set_lever(self, lam: float):
        """手动调档：重建引擎参数，保留激活态与拓扑锚点。"""
        lam = max(0.0, min(1.0, lam))
        old_act = dict(self.engine.activation)
        self.lam_target = lam
        self.lam = lam
        self.engine = self._make_engine(lam)
        if self._anchors_cache:
            self.engine.load_anchors(self._anchors_cache)
            for k, v in old_act.items():
                if k in self.engine.activation:
                    self.engine.activation[k] = v

    def load_anchors(self, anchors: list):
        self._anchors_cache = anchors
        self.engine.load_anchors(anchors)

    def propagate(self, anchor_id: str, new_content: str, steps: int = 1) -> dict:
        return self.engine.propagate(anchor_id, new_content, steps=steps)

    # ── 稳态指标（结构化，守禁向量）──
    def _metrics(self, query: str, top_k: int) -> dict:
        plain = self.engine.plain_recall(query, top_k=top_k)
        liquid = self.engine.liquid_recall(query, top_k=top_k)
        plain_ids = {r["anchor_id"] for r in plain}
        liquid_ids = {r["anchor_id"] for r in liquid}
        precision_hold = (len(plain_ids & liquid_ids) / len(plain_ids)) if plain_ids else 1.0
        n = max(1, len(self.engine._anchors))
        spread = sum(1 for v in self.engine.activation.values()
                     if v > self.engine.min_activation) / n
        return {"precision_hold": precision_hold, "spread_ratio": spread}

    def recall(self, query: str, top_k: int = 5) -> dict:
        """受控召回：先测稳态指标 → 守卫调档 → 再出结果。返回含档位与指标。"""
        metrics = self._metrics(query, top_k)
        new_lam = self.guard.check(self.lam, self.lam_target, metrics)
        if new_lam != self.lam:
            old_act = dict(self.engine.activation)
            self.lam = new_lam
            self.engine = self._make_engine(new_lam)
            if self._anchors_cache:
                self.engine.load_anchors(self._anchors_cache)
                for k, v in old_act.items():
                    if k in self.engine.activation:
                        self.engine.activation[k] = v
        results = (self.engine.plain_recall(query, top_k=top_k) if self.lam <= 0
                   else self.engine.liquid_recall(query, top_k=top_k))
        return {
            "results": results,
            "lam": self.lam, "lam_target": self.lam_target,
            "metrics": {k: round(v, 4) for k, v in metrics.items()},
            "params": lever_params(self.lam),
        }

    # ── 持久化：λ + audit + 激活态一体落盘（单一事实源）──
    def save(self, path: str = None) -> str:
        path = path or self.persist_path
        if not path:
            return ""
        data = {
            "version": 1,
            "mechanism": "liquid_lever",
            "ts": int(time.time()),
            "lam": self.lam, "lam_target": self.lam_target,
            "half_life": self.half_life,
            "activation": {k: round(v, 4) for k, v in self.engine.activation.items()},
            "audit": self.guard.audit[-50:],  # 审计链留最近 50 条
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def load(self, path: str = None) -> bool:
        path = path or self.persist_path
        if not path or not os.path.exists(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        self.lam_target = data.get("lam_target", self.lam_target)
        self.lam = data.get("lam", self.lam)
        self.half_life = data.get("half_life", self.half_life)
        self.guard.audit = data.get("audit", [])
        self.engine = self._make_engine(self.lam)
        if self._anchors_cache:
            self.engine.load_anchors(self._anchors_cache)
        # TTL 冷却（与 liquid_reweight.load 同模型）
        ts = data.get("ts", int(time.time()))
        dt = max(0, int(time.time()) - ts)
        decay = 0.5 ** (dt / max(1, self.half_life))
        for k, v in data.get("activation", {}).items():
            if k in self.engine.activation:
                self.engine.activation[k] = round(v * decay, 4)
        return True

    def snapshot(self) -> dict:
        return {
            "ts": int(time.time()),
            "mechanism": "liquid_lever",
            "lam": self.lam, "lam_target": self.lam_target,
            "params": lever_params(self.lam),
            "audit_len": len(self.guard.audit),
            "engine": self.engine.snapshot(),
        }


# ── 自测 ──
def _selftest():
    print("━━━ LiquidLever selftest ━━━")
    anchors = [
        {"id": "A", "name": "液环禁向量一致性判定", "description": "液环禁用向量embedding做一致性判定与成核"},
        {"id": "B", "name": "液环稳态演化机制", "description": "液环记忆状态是演化对象而非被管理数据"},
        {"id": "C", "name": "液环双轨成核", "description": "液环private与consensus双轨成核机制"},
    ]

    # ① λ=0 地板：liquid 恒等 plain（零风险退化）
    lv0 = LiquidLever(lam=0.0)
    lv0.load_anchors(anchors)
    lv0.propagate("A", "液环禁止向量做一致性判定")
    q = "演化机制的状态如何"
    r0 = lv0.recall(q, top_k=3)
    plain = lv0.engine.plain_recall(q, top_k=3)
    assert [x["anchor_id"] for x in r0["results"]] == [x["anchor_id"] for x in plain], \
        "自测失败：λ=0 未退化为朴素召回"
    print(f"  ✓ λ=0 地板：召回恒等朴素基线（{[x['anchor_id'] for x in r0['results']]}）")

    # ② λ=1 甜区：参数恰等于已验证默认值
    p1 = lever_params(1.0)
    assert abs(p1["beta"] - 0.6) < 1e-9 and abs(p1["amp_cap"] - 0.5) < 1e-9
    assert abs(p1["topo_thresh"] - 0.20) < 1e-9 and abs(p1["min_activation"] - 0.2) < 1e-9
    assert abs(p1["tau_min"] - TAU_MIN) < 1e-3
    print(f"  ✓ λ=1 甜区：参数=已A/B实证默认值 {p1}")

    # ③ 单调性：映射函数本身单调有界（beta↑ amp_cap↑ / topo_thresh↓ min_activation↓）
    lams = [0.0, 0.25, 0.5, 0.75, 1.0]
    ps = [lever_params(x) for x in lams]
    betas = [p["beta"] for p in ps]
    caps = [p["amp_cap"] for p in ps]
    ths = [p["topo_thresh"] for p in ps]
    mas = [p["min_activation"] for p in ps]
    assert betas == sorted(betas) and caps == sorted(caps), "自测失败：λ→beta/amp_cap 非单调增"
    assert ths == sorted(ths, reverse=True) and mas == sorted(mas, reverse=True), \
        "自测失败：λ→topo_thresh/min_activation 非单调减"
    print(f"  ✓ 单调性：λ={lams} → beta={betas} topo_thresh={ths}")

    # ④ 守卫降档：构造过激活 → spread 越界 → λ 自动砍半
    lv = LiquidLever(lam=1.0, guard=HomeostasisGuard(spread_cap=0.10))
    lv.load_anchors(anchors)
    lv.propagate("A", "液环禁止向量做一致性判定")  # 3 锚点全被激活 → spread 1.0 > 0.10
    r = lv.recall(q, top_k=3)
    assert lv.lam < 1.0, f"自测失败：守卫未降档（λ={lv.lam}）"
    assert lv.guard.audit and "spread_ratio" in lv.guard.audit[-1]["reason"]
    print(f"  ✓ 守卫降档：spread 越界 → λ 1.0→{lv.lam}，审计={lv.guard.audit[-1]['reason']}")

    # ⑤ 慢速回升：连续合格 → λ 缓慢回向 lam_target
    lv2 = LiquidLever(lam=0.8, guard=HomeostasisGuard(recover_after=2))
    lv2.load_anchors(anchors)
    lv2.lam = 0.4  # 模拟曾被降档
    for _ in range(4):
        lv2.recall(q, top_k=3)
    assert lv2.lam > 0.4, f"自测失败：未回升（λ={lv2.lam}）"
    assert lv2.lam <= lv2.lam_target, "自测失败：回升越过目标档"
    print(f"  ✓ 黏滞回升：0.4 → {lv2.lam}（封顶 target={lv2.lam_target}）")

    # ⑥ 持久化：λ + audit + 激活一体 save/load
    import tempfile
    fp = os.path.join(tempfile.gettempdir(), f"lever_selftest_{int(time.time())}.json")
    lv.persist_path = fp
    saved = lv.save()
    lv_new = LiquidLever(lam=1.0, persist_path=fp)
    lv_new.load_anchors(anchors)
    assert lv_new.load(), "自测失败：load 失败"
    assert lv_new.lam == lv.lam and len(lv_new.guard.audit) == len(lv.guard.audit[-50:])
    os.remove(fp)
    print(f"  ✓ 持久化：λ={lv_new.lam} + audit({len(lv_new.guard.audit)}条) + 激活态跨会话恢复")

    print("\n  全部 6 项断言通过 ━━ 稳态可控液态杠杆就绪")
    return True


def main():
    ap = argparse.ArgumentParser(description="液环 稳态可控液态杠杆")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("selftest", help="单元自测（纯本地，不写 8790）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        _selftest()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
