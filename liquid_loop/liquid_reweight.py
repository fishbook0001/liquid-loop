#!/usr/bin/env python3
"""液环 v1.2 · 液态重排引擎  Liquid Reweighting Layer（激活态持久化 · 跨会话保持「活」）
========================================================
实质提升：把 LNN 的「液态」特征接入液环成核 / 双轨逻辑，
让记忆本身能**流动重排**（非仅观测）。

现状短板（已读 workspace.py / selfspin.py 确认）：
  - 成核是静态集合式（_nucleate：同 content ≥2 即结晶，不流动）
  - step() 衰减是常数率，无 τ(x) 自适应
  - anchor 间虽有 AnchorRelation 拓扑骨架，但从未用于动态重排 → 记忆不会因邻接被「唤醒」
  - 检索是纯字面 overlap top-N，相关但字面不重叠的记忆漏召

本引擎补这三处，核心三机制（全部守禁向量：用结构化 token 重叠 / 离散特征，非 embedding）：

  ① 激活拓扑传播（Activation Topology Propagation）
     — 锚点间建立关联图（基于 keyword containment 结构性推断，非向量）
     — 新证据注入某 anchor 时，其**邻居 anchor** 按边权(拓扑关联度)被 τ(x) 黏滞唤醒
       → 相关记忆被「唤醒」，对应 LNN 的 dh/dt = -h/τ(x) + f(h,x,θ)

  ② 液态时间常数 τ(x)（Liquid Time Constant）
     — 重排敏感度随「新证据与自身重叠度」自适应：
       重叠高 → τ 小 → 快吸收（强相关立即并入）
       重叠低 → τ 大 → 慢渗透（弱相关缓慢演化）
     — 黏滞窄带约束（参考 lnn_cfc_demo 实测 τ∈[0.656,0.78]）：重排幅度封顶，防震荡

  ③ 液态召回（Liquid Recall）
     — 给定 query，不仅按字面 overlap 召回，还把「被激活唤醒的邻接记忆」加权并入
     — 即：记忆因之前成核/注入而「活」(激活态)，在后续字面不直接匹配的查询中被唤醒
     — 这是成核前置增强：若把激活加成注入 selfspin 聚类阈值，弱相关记忆会被合并成核

落地方式（守极致稳态 + 不碰生产 server）：
  — 客户端引擎，操作本地 WorkspaceState 内存态（不改动 server 成核逻辑）
  — 重排快照结构化写回 8790（隔离 namespace exp:liquid-reweight:*，守禁向量）
  — 与 selfspin 双层自转正交：selfspin 管「跨源聚类加速成核」，本引擎管「成核后拓扑流动唤醒」
  — 不污染生产 vera/trae/parlant/qiucai/mirofish 记忆

用法：
  python3 liquid_reweight.py selftest            # 纯逻辑单测（不写 8790）
  python3 liquid_reweight.py selftest --live     # 真实写 8790（隔离 ns）
"""
import sys
import os
import re
import json
import time
import hashlib
import argparse
import urllib.request
import urllib.error
from collections import defaultdict

DEFAULT_BACKEND = os.environ.get("LL_BASE", "http://127.0.0.1:8790")

# 与 workspace.py 同源：中文单字 + 英文数字连续串
_TOKEN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def _tokens(s: str) -> list:
    return _TOKEN.findall(s or "")


def _keyword_overlap(a: str, b: str) -> float:
    """关键词重叠度（Jaccard），零依赖。守禁向量。"""
    if not a or not b:
        return 0.0
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _containment(a: str, b: str) -> float:
    """重叠系数（containment）：|A∩B| / min(|A|,|B|)。对中文同义改写鲁棒。"""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _anchor_id_of(a: dict) -> str:
    """内容寻址 anchor id：优先用显式 id，否则对 name+description 哈希（跨会话稳定）。

    这让同一事实在跨会话/跨进程时共享同一激活身份 → 激活态持久化可恢复。
    """
    if a.get("id"):
        return str(a["id"])
    h = hashlib.sha1(f"{a.get('name', '')}|{a.get('description', '')}".encode("utf-8")).hexdigest()[:12]
    return f"h:{h}"


# 参考 LNN 实测 τ 窄带（lnn_cfc_demo 跑出 [0.656, 0.780]）
TAU_MIN = 0.656
TAU_MAX = 0.780
# 单次激活传播幅度硬封顶（黏滞：防邻居被一次注入瞬间拉满）
AMP_CAP = 0.5


def _http_post(url: str, payload: dict, timeout: int = 15) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"content-type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"http_{e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class LiquidReweight:
    """液态重排引擎：让记忆拓扑流动重排（不碰 server，本地内存态 + 隔离写回）。

    Args:
      tau_min / tau_max : τ 窄带（黏滞约束），重叠=1→τ=tau_min(快)，重叠=0→τ=tau_max(慢)
      beta              : 液态召回的唤醒加成系数
      topo_thresh       : 拓扑连边的最低 containment（低于此不连，防噪声边）
      amp_cap           : 单次传播幅度封顶
    """

    def __init__(self, tau_min: float = TAU_MIN, tau_max: float = TAU_MAX,
                 beta: float = 0.6, topo_thresh: float = 0.20, amp_cap: float = AMP_CAP,
                 min_activation: float = 0.2, persist_path: str = None,
                 half_life: float = 86400):
        self.tau_min = tau_min
        self.tau_max = tau_max
        self.beta = beta
        self.topo_thresh = topo_thresh
        self.amp_cap = amp_cap
        self.min_activation = min_activation  # 精度护栏：激活低于此的弱边唤醒视为噪声滤除
        self.persist_path = persist_path      # 激活态持久化文件路径（None=不落盘）
        self.half_life = half_life            # 激活态半衰期(秒)，TTL 黏滞冷却：默认 1 天
        self.activation: dict = {}        # anchor_id -> float（流动重排态，跨会话持久）
        self.topo: dict = {}              # anchor_id -> [(nbr_id, weight)]
        self._anchors: dict = {}          # anchor_id -> {id,name,description}
        self.last_ts: int = int(time.time())

    # ── 文本视图：拓扑用 name（短·主题级），召回用 name+desc（含信息）──
    def _topo_text(self, a: dict) -> str:
        return a.get("name") or ""

    def _anchor_text(self, a: dict) -> str:
        return f"{a.get('description') or ''} {a.get('name') or ''}".strip()

    # ── 载入锚点（来自 8790 /list 或本地构造）──
    def load_anchors(self, anchors: list):
        """anchors: list of dict {id?, name, description}。

        anchor id 走内容寻址（_anchor_id_of）：显式 id 优先，否则哈希 →
        跨会话同一事实共享同一激活身份，配套 save/load 可恢复激活态。
        """
        self._anchors = {_anchor_id_of(a): a for a in anchors}
        for a_id in self._anchors:
            self.activation.setdefault(a_id, 0.0)  # 保留已加载的持久激活（load 后调用）
        self._build_topology()

    def _build_topology(self):
        """结构化拓扑推断（守禁向量：纯 keyword containment，非 embedding）。"""
        ids = list(self._anchors.keys())
        self.topo = {i: [] for i in ids}
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                c = _containment(self._topo_text(self._anchors[ids[i]]),
                                 self._topo_text(self._anchors[ids[j]]))
                if c >= self.topo_thresh:
                    w = round(min(c, 1.0), 3)
                    self.topo[ids[i]].append((ids[j], w))
                    self.topo[ids[j]].append((ids[i], w))

    # ── 因果演化循环成核（v1.3）：显式因果边注入 ──
    def add_causal_edges(self, edges: list):
        """注入结构化因果边（守禁向量：确定性符号关系，非 embedding）。

        edges = [(src_id, dst_id, weight), ...]，src/dst 为锚点 id。
        因果边绕过 topo_thresh 噪声过滤直接并入拓扑 → propagate / liquid_recall
        天然沿因果链唤醒邻接记忆，实现「因果邻近加成」：相关但字面不重叠的
        记忆因因果链被召回（对照 #93 大脑「检索因果相关过往事件」机制）。
        """
        for src, dst, w in edges:
            if src not in self._anchors or dst not in self._anchors or src == dst:
                continue
            wt = round(min(max(float(w), 0.0), 1.0), 3)
            self.topo.setdefault(src, [])
            self.topo.setdefault(dst, [])
            if (dst, wt) not in self.topo[src]:
                self.topo[src].append((dst, wt))
            if (src, wt) not in self.topo[dst]:
                self.topo[dst].append((src, wt))

    # ── ② 液态时间常数 τ(x) ──
    def tau_x(self, overlap: float) -> float:
        """重叠高→τ小→快吸收；重叠低→τ大→慢渗透。黏滞窄带封顶。"""
        ov = max(0.0, min(1.0, overlap))
        tau = self.tau_max - ov * (self.tau_max - self.tau_min)
        return round(max(self.tau_min, min(self.tau_max, tau)), 4)

    # ── ① 激活拓扑传播 ──
    def propagate(self, anchor_id: str, new_content: str, steps: int = 1) -> dict:
        """新证据注入 anchor_id 后，沿拓扑做 τ(x) 黏滞激活传播。

        机制：注入锚点自身激活=1.0（被直接命中）；其每个邻居按「边权(拓扑关联度)
              × (1/τ) × 0.5」被唤醒，τ 取新证据与该邻居主题的重叠自适应
              （重叠高→τ小→快唤醒；重叠低→τ大→慢渗透），幅度封顶 amp_cap。
        返回各锚点激活增量（黏滞校验用）。
        """
        if anchor_id not in self._anchors:
            return {}
        # 注入锚点自身拉满
        self.activation[anchor_id] = min(1.0, self.activation[anchor_id] + 1.0)
        deltas = {anchor_id: 1.0}
        for nbr_id, w in self.topo.get(anchor_id, []):
            ov = _keyword_overlap(new_content, self._topo_text(self._anchors[nbr_id]))
            tau = self.tau_x(ov if ov > 0 else 0.3)  # 无字面重叠用中等 τ（慢渗透）
            amp = min(w * (1.0 / tau) * 0.5, self.amp_cap)
            self.activation[nbr_id] = min(1.0, self.activation[nbr_id] + amp)
            deltas[nbr_id] = round(amp, 4)
        # steps>1：多跳传播（邻居的邻居），封顶仍生效
        for _ in range(steps - 1):
            cur = dict(self.activation)
            for a_id, nbrs in self.topo.items():
                if a_id == anchor_id:
                    continue
                best = 0.0
                for nbr_id, w in nbrs:
                    ov = _keyword_overlap(new_content, self._topo_text(self._anchors[nbr_id]))
                    tau = self.tau_x(ov if ov > 0 else 0.3)
                    best = max(best, w * (1.0 / tau) * 0.5)
                amp = min(best, self.amp_cap)
                if amp > 0:
                    cur[a_id] = min(1.0, self.activation[a_id] + amp)
                    deltas[a_id] = round(cur[a_id] - self.activation[a_id], 4)
            self.activation = cur
        return deltas

    # ── ③ 液态召回 ──
    def liquid_recall(self, query: str, top_k: int = 5) -> list:
        """液态召回 = 字面 overlap + 邻接激活唤醒加成。

        对比纯字面召回（仅 _keyword_overlap），本方法把被激活唤醒的记忆
        加权拉入候选 → 弱相关但已「活」(激活)的记忆不再漏召。
        精度护栏：字面无重叠且唤醒低于 min_activation 的锚点判为弱边噪声，滤除
        （防止 topo_thresh 过低导致的过激活噪声，见 A/B 实证）。
        """
        scored = []
        for a_id, a in self._anchors.items():
            lit = _keyword_overlap(query, self._anchor_text(a))
            wake = self.beta * self.activation.get(a_id, 0.0)
            if lit <= 0 and wake < self.min_activation:
                continue  # 纯噪声弱边唤醒：滤除
            score = lit + wake
            if score > 0:
                scored.append({
                    "anchor_id": a_id,
                    "name": a.get("name"),
                    "score": round(score, 4),
                    "literal": round(lit, 4),
                    "activation": round(self.activation.get(a_id, 0.0), 3),
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def plain_recall(self, query: str, top_k: int = 5) -> list:
        """纯字面召回基线（无激活唤醒），用于 A/B 对照。"""
        scored = []
        for a_id, a in self._anchors.items():
            lit = _keyword_overlap(query, self._anchor_text(a))
            if lit > 0:
                scored.append({"anchor_id": a_id, "name": a.get("name"),
                               "score": round(lit, 4), "literal": round(lit, 4),
                               "activation": 0.0})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def snapshot(self) -> dict:
        """结构化激活态快照（全离散特征，守禁向量）→ 写回 8790 用。"""
        return {
            "ts": int(time.time()),
            "mechanism": "liquid_reweight",
            "tau_band": [self.tau_min, self.tau_max],
            "activation": {k: round(v, 3) for k, v in self.activation.items()},
            "topo_edges": sum(len(v) for v in self.topo.values()) // 2,
            "anchor_count": len(self._anchors),
        }

    # ── 激活态持久化（跨会话保持「活」，守禁向量：全离散特征）──
    def save(self, path: str = None) -> str:
        """把激活态 + 参数写本地 JSON（不碰 server、不增 daemon）。

        返回实际写入路径；persist_path 未设且无参数则跳过返回空串。
        激活态按当前值原样落盘，冷却在 load() 时按时间重算（单一事实源=时间戳）。
        """
        path = path or self.persist_path
        if not path:
            return ""
        self.last_ts = int(time.time())
        data = {
            "version": 2,
            "mechanism": "liquid_reweight",
            "ts": self.last_ts,
            "tau_band": [self.tau_min, self.tau_max],
            "beta": self.beta,
            "topo_thresh": self.topo_thresh,
            "min_activation": self.min_activation,
            "half_life": self.half_life,
            "activation": {k: round(v, 4) for k, v in self.activation.items()},
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def load(self, path: str = None) -> bool:
        """从本地 JSON 恢复激活态，按 half_life 做 TTL 黏滞冷却。

        冷却模型：cooled = raw * 0.5 ** (dt / half_life)，dt=now-ts。
        隔夜(≈12h, half_life=1d)→≈0.71；3天→≈0.125（记忆淡忘但可重激活）。
        返回是否成功加载；文件不存在/损坏返回 False。
        注意：调用方应先 load_anchors() 建骨架，再 load() 覆盖激活（setdefault 不会丢）。
        """
        path = path or self.persist_path
        if not path or not os.path.exists(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        self.tau_min, self.tau_max = data.get("tau_band", [self.tau_min, self.tau_max])
        self.beta = data.get("beta", self.beta)
        self.topo_thresh = data.get("topo_thresh", self.topo_thresh)
        self.min_activation = data.get("min_activation", self.min_activation)
        self.half_life = data.get("half_life", self.half_life)
        self.last_ts = data.get("ts", int(time.time()))
        raw_act = data.get("activation", {})
        now = int(time.time())
        dt = max(0, now - self.last_ts)
        decay = 0.5 ** (dt / max(1, self.half_life))
        self.activation = {k: round(v * decay, 4) for k, v in raw_act.items()}
        return True

    # ── 写回 8790（隔离 namespace，守禁向量）──
    def snapshot_to_8790(self, backend: str = DEFAULT_BACKEND,
                         agent_ns: str = "exp:liquid-reweight",
                         run_id: str = "", category: str = "liquid-reweight") -> dict:
        """把激活态快照结构化写回 8790（隔离 ns，不污染生产）。"""
        b = backend.rstrip("/")
        snap = self.snapshot()
        agent_id = f"{agent_ns}:{run_id}" if run_id else agent_ns
        content = json.dumps(snap, ensure_ascii=False)
        resp = _http_post(f"{b}/remember",
                          {"content": content, "category": category, "agent_id": agent_id})
        return {"ok": resp.get("ok", False),
                "nucleated": bool(resp.get("nucleated")),
                "agent_id": agent_id, "raw": resp}


# ── 自测 ──
def _selftest(live: bool = False):
    print("━━━ LiquidReweight selftest ━━━")
    lr = LiquidReweight(beta=0.6, topo_thresh=0.20)

    # 3 个拓扑相邻锚点（共享「液环」+ 各自领域词，containment 应连边）
    anchors = [
        {"id": "A", "name": "液环禁向量一致性判定", "description": "液环禁用向量embedding做一致性判定与成核"},
        {"id": "B", "name": "液环稳态演化机制", "description": "液环记忆状态是演化对象而非被管理数据"},
        {"id": "C", "name": "液环双轨成核", "description": "液环private与consensus双轨成核机制"},
    ]
    lr.load_anchors(anchors)
    print(f"  拓扑边数: {lr.snapshot()['topo_edges']}（应≥2，A-B/A-C/B-C 相邻）")
    assert lr.snapshot()["topo_edges"] >= 2, "自测失败：拓扑未连边"

    # 注入新证据（命中 A 主题）
    new = "液环禁止向量做一致性判定"
    print(f"\n  注入证据(命中A): {new}")
    deltas = lr.propagate("A", new, steps=1)
    print(f"  激活增量: {deltas}")
    print(f"  激活态: A={lr.activation['A']:.3f} B={lr.activation['B']:.3f} C={lr.activation['C']:.3f}")

    # 断言①：拓扑传播发生（邻居被唤醒）
    assert lr.activation["B"] > 0, "自测失败：邻居B未被唤醒"
    assert lr.activation["C"] > 0, "自测失败：邻居C未被唤醒"
    # 断言③：黏滞封顶（单次 amp ≤ amp_cap，且邻居不瞬间拉满）
    assert lr.activation["B"] <= lr.amp_cap + 1e-9, f"自测失败：B 激活未封顶({lr.activation['B']})"
    assert lr.activation["C"] <= lr.amp_cap + 1e-9, f"自测失败：C 激活未封顶({lr.activation['C']})"
    print(f"  ✓ 传播+黏滞断言通过（B={lr.activation['B']:.3f} C={lr.activation['C']:.3f} ≤ 封顶 {lr.amp_cap}）")

    # 断言②：液态召回唤醒字面不重叠但已激活的记忆
    # query 字面只重叠 B（稳态演化），但 A 因注入已激活满 → 液态版把 A 拉进候选
    q = "演化机制的状态如何"
    plain = lr.plain_recall(q, top_k=3)
    liquid = lr.liquid_recall(q, top_k=3)
    plain_ids = [r["anchor_id"] for r in plain]
    liquid_ids = [r["anchor_id"] for r in liquid]
    print(f"\n  查询: {q}")
    print(f"  plain 召回: {plain_ids}（纯字面，A 应缺席）")
    print(f"  liquid召回: {liquid_ids}（唤醒，A 应入榜）")
    assert "A" not in plain_ids, "自测失败：plain 竟召回 A（基线异常）"
    assert "A" in liquid_ids, "自测失败：液态召回未唤醒已激活的 A"
    # A 经激活排在 B 之前（记忆流动：激活态主导召回）
    assert liquid_ids[0] == "A", "自测失败：A 未因激活排首位"
    print(f"  ✓ 液态唤醒断言通过（A 经激活入榜且排首，记忆非死存储）")

    # ── v1.3 因果演化循环成核：因果边独立于 keyword 拓扑唤醒远端记忆 ──
    D = {"id": "D", "name": "量子白骨观", "description": "与液环无字面重叠的远端主题，仅靠因果边连接"}
    lr.load_anchors(anchors + [D])  # 重建拓扑（含 D，D 与 A/B/C 无 keyword 连边）
    lr.add_causal_edges([("A", "D", 0.6)])  # A→D 显式因果边，绕过 keyword 噪声过滤
    lr.propagate("A", "液环禁止向量", steps=1)
    print(f"\n  因果边 A→D(0.6)，D 激活={lr.activation['D']:.3f}（应>0，尽管字面零重叠）")
    assert lr.activation["D"] > 0, "自测失败：因果边未唤醒远端 D"
    # 因果召回：查询命中 D 主题但因因果边已激活，D 入榜
    q2 = "量子白骨观是什么"
    cr = lr.liquid_recall(q2, top_k=5)
    cr_ids = [r["anchor_id"] for r in cr]
    print(f"  因果查询[{q2}] 召回: {cr_ids}（D 应入榜）")
    assert "D" in cr_ids, "自测失败：因果边未把远端 D 拉入召回"
    print(f"  ✓ 因果演化循环成核断言通过（因果边唤醒+召回远端记忆）")

    # τ(x) 自适应方向校验
    t_hi = lr.tau_x(0.9)   # 高重叠 → 小 τ（快）
    t_lo = lr.tau_x(0.1)   # 低重叠 → 大 τ（慢）
    print(f"\n  τ(x) 自适应: overlap=0.9→τ={t_hi}  overlap=0.1→τ={t_lo}（应 t_hi<t_lo）")
    assert t_hi < t_lo, "自测失败：τ(x) 方向反了"
    assert TAU_MIN <= t_hi <= TAU_MAX and TAU_MIN <= t_lo <= TAU_MAX, "自测失败：τ 越窄带"
    print(f"  ✓ τ(x) 黏滞窄带断言通过")

    # 写回实证
    if live:
        print(f"\n  → 真实写 8790（隔离 ns exp:liquid-reweight:selftest）...")
        rid = f"selftest:{int(time.time())}"
        res = lr.snapshot_to_8790(run_id=rid)
        print(f"    返回: {json.dumps(res, ensure_ascii=False)}")
        assert res["ok"], f"自测失败：写 8790 未 ok（{res['raw']}）"
        print(f"  ✓ 写回 8790 实证通过（agent_id={res['agent_id']}）")
    else:
        snap = lr.snapshot()
        print(f"\n  → dry-run 快照: {json.dumps(snap, ensure_ascii=False)[:220]}...")
        print(f"  ✓ 逻辑自测通过（--live 可真实写 8790 验证）")
    return True


def main():
    ap = argparse.ArgumentParser(description="液环 v1.0 液态重排引擎")
    sub = ap.add_subparsers(dest="cmd")
    st = sub.add_parser("selftest", help="单元自测（默认 dry-run）")
    st.add_argument("--live", action="store_true", help="真实写 8790（隔离 exp:liquid-reweight:*）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        _selftest(live=args.live)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
