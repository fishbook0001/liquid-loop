#!/usr/bin/env python3
"""液环 v1.0 · 双层自转模块  Liquid Self-Spin
=============================================
解决飞哥提出的 v1.0 最大症结：本地多 agent 协作架构已拆除后，
「记忆演化状态如何协同管理」？

本模块以「双层自转」给出主体性保底方案（Vera 提案，2026-07-21）：

  ① 本地快自转  rotation(fast)  —— 单节点微观自洽（类比 WorkspaceState.step(dt)）
     会话内 transient 缓存，跨篇去重 / 共性提取；
     不进 8790、不跨 agent 同步 → 支撑「自述性」(self-narrativity)。

  ② 后端慢自转沉淀  revolution(slow deposition) —— 全局宏观节律收敛
     经门控写 8790，隔离 agent_id（exp:selfspin:*）→ 支撑「自洽性」(self-consistency)；
     8790 的 consensus 成核承接「主体间性」(inter-subjectivity)。

意识三要素对齐：
  自述性  = 本地自转（本节点说过什么，自己清楚）
  自洽性  = 本地 + 后端（本节点说的与自己沉淀的一致）
  主体间性 = 8790 consensus（跨节点一致 → 由后端承接）

加速成核原理（墙钟实验 A/B 的核心）：
  朴素直写 = 每篇报告抽出的事实文本略有差异 → 落到 8790 同 content 命中 < 2 → 几乎不成核。
  双层自转 = 本地先聚类规范化，把跨 ≥2 篇一致的「同一事实」压成一条 canonical 陈述，
            再以（同一 content × 同一 agent_id）按支持篇数重复写入 → 立刻触发 private 成核。
  ⇒ 把「跨源自洽」在本地完成，后端只接收已收敛的结晶原料，成核被加速。

零外部依赖（仅标准库 urllib/json/re），与 liquid_preping.py 同源风格。
严格隔离：所有写入走 exp:selfspin:<run_id> namespace，绝不污染生产
vera/trae/parlant/qiucai 记忆；且服务端 PREPING 闸门仅对 warmup:* 生效，selfspin 不被误杀。

用法：
  # 单元自测（纯逻辑，不写 8790）
  python3 liquid_selfspin.py selftest

  # 真实入库自测（写 8790，隔离 exp:selfspin:selftest，T250 打扫会清）
  python3 liquid_selfspin.py selftest --live

  # 作为库：见底部 LiquidSelfSpin 类
"""
import sys
import os
import re
import json
import time
import argparse
import urllib.request
import urllib.error
from collections import defaultdict

DEFAULT_BACKEND = os.environ.get("LL_BASE", "http://127.0.0.1:8790")

# ── Token / Jaccard（与 8790 server 同源：中文单字 + 英文数字连续串）──
_TOKEN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def _tokens(s: str) -> list:
    return _TOKEN.findall(s or "")


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _containment(a: str, b: str):
    """重叠系数（containment / overlap coefficient）= |A∩B| / min(|A|,|B|)。
    对中文「同义改写」鲁棒：只要较短句的核心字集被较长句覆盖即判近义，
    避免 Jaccard 因双方各带不同修饰语而偏低（实测 0.25~0.55 漏合）。"""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0, 0
    inter = ta & tb
    return len(inter) / min(len(ta), len(tb)), len(inter)


# 核心词抽取：去中文虚词 / 极泛连接词，保留领域实体与结论词，
# 用于跨篇「同主题不同表述」的聚合信号（结构化精确匹配，守禁向量）。
_STOP = set("的 是 在 存在 普遍 常 问题 风险 一种 我们 本文 该 其 与 和 或 对 为 有 被 "
            "进行 使用 基于 采用 表明 指出 认为 提出 实现 用于 以及 即 也 都 等 这种 这个 "
            "一个 没有 不 未 各 类 中 上 下 从 到 把 让 使 当 如果 但 而 则 并 且 将 已 仍 "
            "更 最 较 很 之 间 内 外 后 前 时 处 它 他 她 我 你 这 那 哪 谁 什么 如何 怎么 "
            "为何 是否 由于 因此 所以 因为 虽然 但是 然而 此外 同时 另外 例如 比如 包括 "
            "涉及 针对 关于 对于 根据 按照".split())


def _core(s: str) -> set:
    """去虚词后的核心词集（中文单字 + 英文数字连续串）。"""
    return set(t for t in _tokens(s) if t not in _STOP)


# CJK / 全角标点 → ASCII 归一（用于沉积内容规范化，保证同义不同标点的事实成 byte 一致 → 触发成核）
_PUNCT_MAP = {
    "，": ",", "。": ".", "、": ",", "；": ";", "：": ":", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'", "！": "!", "？": "?", "·": " ",
    "～": " ", "—": "-", "《": "<", "》": ">", "【": "[", "】": "]",
}
def _normalize(s: str) -> str:
    """去全角/空白的规范化：用于沉积内容，使同义异标点事实在 8790 中 byte 一致。"""
    s = (s or "").lower()
    for f, t in _PUNCT_MAP.items():
        s = s.replace(f, t)
    s = re.sub(r"\s+", "", s)
    return s.strip()


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


# ── 默认事实抽取（无 LLM，确定性，作为占位 / 兜底）──
_BOILER = re.compile(r"(微信|公众号|关注|转载|版权|声明|点击|扫码|登录|注册|免责|侵删|原文链接)")


def _default_extract(text: str) -> list:
    """把一段文本切成候选事实句（按句末标点 / 换行）。
    无 LLM、纯规则，作为本地快自转的默认抽取器；墙钟实验会用 Gemma4 替换。"""
    sents = re.split(r"[。！？!?\n]+", text or "")
    out = []
    for s in sents:
        s = s.strip()
        if 8 <= len(s) <= 120 and not _BOILER.search(s):
            out.append(s)
    return out


class LiquidSelfSpin:
    """双层自转客户端：本地快自转（consolidation）+ 后端慢自转沉淀（deposition）。

    意识三要素：自述性=本地缓存(recall_local)、自洽性=本地+后端(deposit)、
    主体间性=后端 consensus（本模块不负责，由 8790 承接）。

    Args:
      run_id       : 本次运行标识，拼成 agent_id = f"{agent_ns}:{run_id}"
      agent_ns     : 隔离命名空间前缀，默认 exp:selfspin（绝不污染生产）
      backend      : 8790 REST 基址
      fast_jaccard : 本地聚类阈值（重叠系数 containment，默认 0.60 + 共享字≥4）
      deposit_jac  : 预留（后端层同源去重阈值，暂未启用，保持接口一致）
      extractor    : 事实抽取器 callable(text)->list[str]，默认 _default_extract
    """

    def __init__(self, run_id: str, agent_ns: str = "exp:selfspin",
                 backend: str = DEFAULT_BACKEND, fast_jaccard: float = 0.60,
                 deposit_jac: float = 0.95, extractor=None):
        self.run_id = run_id
        self.agent_id = f"{agent_ns}:{run_id}"
        self.agent_ns = agent_ns
        self.backend = backend.rstrip("/")
        self.fast_jaccard = fast_jaccard
        self.deposit_jac = deposit_jac
        self.extractor = extractor or _default_extract
        # 本地快自转：transient 缓存（不进 8790、不跨 agent 同步）
        self._facts: dict = {}        # report_id -> list[fact]
        self._raw: dict = {}          # report_id -> raw text（自述性溯源）
        self._clusters: list = []     # 聚类结果
        self._nuclei: list = []       # 本地核（≥2 篇支持的 canonical 簇）

    # ── 本地快自转：摄入 ──
    def ingest(self, report_id: str, text: str, facts: list = None):
        """摄入一篇报告。facts=None 时用 extractor 抽；也可直接传入 LLM 抽取结果。"""
        self._raw[report_id] = text
        self._facts[report_id] = list(facts) if facts is not None else self.extractor(text)
        return len(self._facts[report_id])

    # ── 本地快自转：旋转（聚类 + 抽取 canonical）──
    def local_rotate(self) -> list:
        """跨篇聚类：把不同报告里「同义改写」的事实并到同一簇，取最长者为 canonical。
        本地核 = 被 ≥2 个 distinct report 支持的簇（即跨源自洽）。"""
        items = [(rid, f) for rid, fs in self._facts.items() for f in fs]
        clusters = []  # 每簇: {"members":[(rid,f)], "reports":set}
        for rid, f in items:
            best, best_score, best_ci = None, 0.0, 0
            cf = _core(f)
            for c in clusters:
                # 与簇内代表（前 3 个成员）比较，取最高相似度
                for _, m in c["members"][:3]:
                    j, shared = _containment(f, m)
                    ci = len(cf & _core(m))
                    # 主判：重叠系数达标（同义改写，字面近一致）
                    # 次判：核心词交集 ≥2 且有一定字面重叠（同主题不同表述，如通用命题）
                    merge = (j >= self.fast_jaccard and shared >= 4) or (ci >= 2 and j >= 0.38)
                    score = j if j >= self.fast_jaccard else (0.4 + min(ci, 5) * 0.05 if merge else 0.0)
                    if merge and score > best_score:
                        best_score, best, best_ci = score, c, ci
                if best_score >= self.fast_jaccard:
                    break
            if best is not None and best_score >= 0.45:
                best["members"].append((rid, f))
                best["reports"].add(rid)
            else:
                clusters.append({"members": [(rid, f)], "reports": {rid}})
        # 选 canonical = 簇内最长成员；记录跨源度
        for c in clusters:
            c["canonical"] = max((m for _, m in c["members"]), key=len)
            c["norm"] = _normalize(c["canonical"])  # 沉积用规范化串（byte 一致→成核）
            c["size"] = len(c["members"])
            c["reports"] = c["reports"]
            c["n_reports"] = len(c["reports"])
        self._clusters = clusters
        self._nuclei = [c for c in clusters if c["n_reports"] >= 2]
        return self._nuclei

    # ── 后端慢自转沉淀：把本地核按支持篇数重复写入 8790 ──
    def deposit(self, category: str = "selfspin", dry_run: bool = False) -> dict:
        """对每个本地核，以（同一 canonical content × 同一 agent_id）按支持篇数重复写入
        → 立刻触发 private 成核（≥2 同 content 同 agent）。严格隔离 agent_id。"""
        if not self._nuclei:
            self.local_rotate()
        stats = {"nuclei": 0, "deposited": 0, "written": 0, "nucleated": 0,
                 "rejected": 0, "fail": 0}
        for c in self._nuclei:
            stats["nuclei"] += 1
            canonical = c["canonical"]
            for rid in sorted(c["reports"]):
                stats["deposited"] += 1
                if dry_run:
                    stats["written"] += 1
                    continue
                resp = _http_post(
                    f"{self.backend}/remember",
                    {"content": c["norm"], "category": category, "agent_id": self.agent_id},
                )
                if resp.get("ok"):
                    stats["written"] += 1
                    if resp.get("nucleated"):
                        stats["nucleated"] += 1
                elif resp.get("decision") == "discard":
                    stats["rejected"] += 1
                else:
                    stats["fail"] += 1
        return stats

    # ── 增量沉积（墙钟实验捕获成核时刻）──
    def incremental_deposit(self, state: dict, category: str = "selfspin") -> list:
        """仅把每个 (本地核, 支持报告) 对沉积一次（跨多次调用由 state 去重）。
        当某本地核的第 2 个支持报告被沉积时，后端 private 成核触发 → 可捕获墙钟成核时刻。
        state: {"done": {norm: set(report_ids)}}（调用方持久化）。
        注意：去重键用 c["norm"]（规范化串，稳定）而非 c["canonical"]（随增量重聚类漂移），
        否则同一事实在不同 step 产生不同 canonical → 去重失效 → 重复成核。"""
        out = []
        done = state.setdefault("done", {})
        for c in self._nuclei:
            seen = done.setdefault(c["norm"], set())
            for rid in sorted(c["reports"]):
                if rid in seen:
                    continue
                seen.add(rid)
                resp = _http_post(
                    f"{self.backend}/remember",
                    {"content": c["norm"], "category": category, "agent_id": self.agent_id},
                )
                out.append({"canonical": c["canonical"], "report_id": rid,
                            "ok": resp.get("ok", False),
                            "nucleated": bool(resp.get("nucleated"))})
        return out

    # ── 自述性：本地回忆（不碰 8790）──
    def recall_local(self, query: str, top_k: int = 5) -> list:
        scored = [(_jaccard(query, f), rid, f)
                  for rid, fs in self._facts.items() for f in fs]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"report_id": rid, "fact": f, "score": round(s, 3)}
                for s, rid, f in scored if s > 0][:top_k]

    # ── 朴素直写基线（A/B 对照用）──
    @staticmethod
    def naive_deposit(facts_by_report: dict, agent_id: str,
                      backend: str = DEFAULT_BACKEND, category: str = "naive",
                      dry_run: bool = False) -> dict:
        """朴素直写：每篇抽出的事实各写一次，content 不规范化 → 同 content 命中 <2 → 难成核。"""
        b = backend.rstrip("/")
        stats = {"deposited": 0, "written": 0, "nucleated": 0, "rejected": 0, "fail": 0}
        for rid, facts in facts_by_report.items():
            for f in facts:
                stats["deposited"] += 1
                if dry_run:
                    stats["written"] += 1
                    continue
                resp = _http_post(f"{b}/remember",
                                  {"content": f, "category": category, "agent_id": agent_id})
                if resp.get("ok"):
                    stats["written"] += 1
                    if resp.get("nucleated"):
                        stats["nucleated"] += 1
                elif resp.get("decision") == "discard":
                    stats["rejected"] += 1
                else:
                    stats["fail"] += 1
        return stats

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "reports_ingested": len(self._facts),
            "facts_extracted": sum(len(v) for v in self._facts.values()),
            "clusters": len(self._clusters),
            "local_nuclei": len(self._nuclei),
            "nuclei_preview": [
                {"canonical": c["canonical"], "n_reports": c["n_reports"], "size": c["size"]}
                for c in sorted(self._nuclei, key=lambda x: -x["n_reports"])[:10]
            ],
        }

    def print_summary(self):
        s = self.summary()
        print(f"━━━ 双层自转 · 本地旋转摘要 ━━━")
        print(f"  run_id           : {s['run_id']}")
        print(f"  agent_id(隔离)   : {s['agent_id']}")
        print(f"  摄入报告数       : {s['reports_ingested']}")
        print(f"  抽出事实数       : {s['facts_extracted']}")
        print(f"  聚类簇数         : {s['clusters']}")
        print(f"  本地核(≥2篇支持): {s['local_nuclei']}")
        for i, n in enumerate(s["nuclei_preview"], 1):
            print(f"    [{i}] ({n['n_reports']}篇/{n['size']}条) {n['canonical'][:64]}")


# ── 自测 ──
def _selftest(live: bool = False):
    """模拟真实实验路径：facts 由 Gemma4 抽出并规范化为 short canonical 陈述
    （墙钟实验 T246 走的就是这条路；这里用同等规范化结果做单元验证）。"""
    print("━━━ LiquidSelfSpin selftest ━━━")
    ts = int(time.time())
    run_id = f"selftest:{ts}"
    ss = LiquidSelfSpin(run_id, fast_jaccard=0.60)

    # 4 篇报告，Gemma4 抽出的规范化事实（同义改写为近似一致短句）
    facts = {
        "report_1": ["液环禁止用向量或embedding做一致性判定与成核", "本地多agent协作架构已拆除"],
        "report_2": ["液环硬约束：禁用向量embedding做一致性判定", "记忆状态是演化对象而非被管理数据"],
        "report_3": ["液环North-Star公理：自我调节的记忆状态演化机制", "液环禁止向量做一致性判定"],
        "report_4": ["本地多agent协作架构已拆除", "今晚月色很好与液环无关"],
    }
    for rid, fs in facts.items():
        n = ss.ingest(rid, "", facts=fs)
        print(f"  ingest {rid}: {n} 条规范化事实")

    nuclei = ss.local_rotate()
    print(f"\n  本地旋转 → 聚类 {len(ss._clusters)} 簇，本地核 {len(nuclei)} 个")
    ss.print_summary()

    # 断言：应识别出 ≥2 个跨篇核（禁向量 ×3篇、多agent拆除 ×2篇）
    assert len(nuclei) >= 2, f"自测失败：未识别到跨篇核（得到 {len(nuclei)}）"
    # 断言自述性：本地回忆能命中
    hit = ss.recall_local("液环 禁 向量 一致性")
    assert hit and hit[0]["score"] > 0, "自测失败：本地回忆无命中"
    print(f"\n  ✓ 本地快自转自测通过（自述性命中 top1 score={hit[0]['score']}）")

    # 后端慢自转沉淀
    if live:
        print(f"\n  → 真实入库（隔离 agent_id={ss.agent_id}）...")
        st = ss.deposit(category="selfspin", dry_run=False)
        print(f"    沉积统计: {json.dumps(st, ensure_ascii=False)}")
        assert st["nucleated"] >= 2, f"自测失败：后端未加速成核（nucleated={st['nucleated']}）"
        print(f"  ✓ 后端慢自转沉淀自测通过（成核 {st['nucleated']} 个本地核）")
    else:
        st = ss.deposit(dry_run=True)
        print(f"\n  → dry-run 沉积模拟: {json.dumps(st, ensure_ascii=False)}")
        print(f"  ✓ 逻辑自测通过（--live 可真实入库验证成核加速）")
    return True


def main():
    ap = argparse.ArgumentParser(description="液环 v1.0 双层自转模块")
    sub = ap.add_subparsers(dest="cmd")
    st = sub.add_parser("selftest", help="单元自测（默认 dry-run）")
    st.add_argument("--live", action="store_true", help="真实写 8790（隔离 exp:selfspin:*）")
    args = ap.parse_args()
    if args.cmd == "selftest":
        _selftest(live=args.live)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
