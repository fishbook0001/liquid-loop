#!/usr/bin/env python3
"""
D4/D5 写侧接线模块（8791 进程内后台喂送）
=========================================
治理背景（T2 D4/D5 写侧接线，2026-09-21）：
  8790 侧 orc_probe.py 已实现 D4/D5 的**只读观测端**（_probe_hebbian /
  _probe_regeneration，标注 write_side=delegated_to:5insights(8791)），
  但 8791 只有 HTTP 端点、**无任何生产调用方**在喂数据，导致：
    - hebbian_associations.json 长期停留在 2026-09-02 的 3 条测试数据
    - regeneration_metrics.json 只有 1 条 history
  即「观测端已通、写侧空转」。本模块把写侧落到 8791 进程内，
  由 8791 后台线程周期性从液环主状态**只读**派生激活信号并写入 D4/D5 store。

D4（赫布关联）喂送策略：
  - 数据源：state.json evidences 的 last_recall_at / recall_hits（真实召回痕迹，只读）
  - 增量模式：按「小时桶」聚合同一召回窗口内被激活的记忆 id，
    同窗口内 ≥2 个 id 视作一次共激活事件 → update_from_activation(ids, context)
  - 冷启动：首次运行（无游标）时，取 recall_hits>0 中 last_recall_at 最新的 Top-K
    作为一次种子共激活（context=bootstrap_recent_recall），只执行一次
  - 游标持久化于 insight_feeder_state.json，避免重复计数

D5（再生抗衰指标）喂送策略：
  - 周期内新蒸馏数：新增 evidence 且锚点名含 distill/军师调研 的数量
  - 周期内新领域数：锚点名单相对上一周期的新增名数
  - 周期内新关联数：赫布引擎 total_associations 的周期增量（D4 产出的直接度量）
  - 激活多样性：周期内被召回记忆 id → 次数映射
  - total_memories：evidences + memories 总数
  → collect_metrics(...) 落 regeneration_metrics.json

安全边界（硬约束）：
  1. 本模块**只写** STORE_DIR 下三个文件：hebbian_associations.json /
     regeneration_metrics.json / insight_feeder_state.json；
     **绝不写** state.json / junshi_state.json / agent_registry.json 等业务状态。
  2. 对 state.json 全程只读，解析失败即跳过本轮并记录错误，不做任何修补。
  3. 所有异常 fail-open：单轮失败不影响 8791 主服务（HTTP 端点）运行。
  4. 无任何删除行为。

生效条件：本模块由 liquid_loop_5insights_api.py 在进程启动时装载并起线程，
**须重启 8791 才生效**（进程内代码 + plist 环境变量）。
"""
import json
import os
import threading
from datetime import datetime, timedelta

# ── 路径与开关（环境变量可覆盖，默认与生产一致）──────────────────────────
STORE_DIR = os.path.expanduser(os.environ.get("LIQUID_5I_STORE_DIR", "~/.liquidloop/memory/.liquid"))
STATE_FILE = os.path.expanduser(os.environ.get("LIQUID_5I_STATE_FILE", os.path.join(STORE_DIR, "state.json")))
HEBBIAN_STORE = os.path.join(STORE_DIR, "hebbian_associations.json")
REGEN_STORE = os.path.join(STORE_DIR, "regeneration_metrics.json")
FEEDER_STATE = os.path.join(STORE_DIR, "insight_feeder_state.json")

FEEDER_ENABLED = os.environ.get("LIQUID_5I_FEEDER_ENABLED", "1") != "0"
FEEDER_INTERVAL = int(os.environ.get("LIQUID_5I_FEEDER_INTERVAL", "300"))       # 喂送周期（秒）
REGEN_PERIOD = int(os.environ.get("LIQUID_5I_REGEN_PERIOD", "21600"))           # D5 采集周期（秒，默认6h）
BOOTSTRAP_TOP_K = int(os.environ.get("LIQUID_5I_BOOTSTRAP_TOPK", "20"))
MAX_BATCH = int(os.environ.get("LIQUID_5I_MAX_BATCH", "30"))

_DISTILL_HINTS = ("distill", "军师调研")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _parse_ts(s: str) -> datetime | None:
    """解析 ISO 时间戳（容忍 Z 结尾与无时区），失败返回 None。"""
    if not s:
        return None
    try:
        t = str(s).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


class InsightWriteSide:
    """D4/D5 写侧喂送器（单实例，绑定 api 进程内的引擎实例）。"""

    def __init__(self, hebbian_engine=None, regeneration_engine=None,
                 store_dir: str = None, state_file: str = None):
        self.store_dir = store_dir or STORE_DIR
        self.state_file = state_file or STATE_FILE
        self.hebbian_state = os.path.join(self.store_dir, "hebbian_associations.json")
        self.regen_state = os.path.join(self.store_dir, "regeneration_metrics.json")
        self.feeder_state_path = os.path.join(self.store_dir, "insight_feeder_state.json")
        self._hebbian = hebbian_engine
        self._regen = regeneration_engine
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.last_result = {"status": "never_run"}
        self.run_count = 0

    # ── 引擎惰性获取（便于独立测试注入临时 store）──────────────────────
    def _engines(self):
        if self._hebbian is None:
            from liquid_loop.hebbian_association import HebbianAssociation
            self._hebbian = HebbianAssociation(association_store_path=self.hebbian_state)
        if self._regen is None:
            from liquid_loop.regeneration_metrics import RegenerationMetrics
            self._regen = RegenerationMetrics(metrics_store_path=self.regen_state)
        return self._hebbian, self._regen

    # ── 喂送器自身游标状态（仅记录游标/计数，不含业务数据）─────────────
    def _load_feeder_state(self) -> dict:
        try:
            with open(self.feeder_state_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_feeder_state(self, st: dict):
        os.makedirs(self.store_dir, exist_ok=True)
        tmp = self.feeder_state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.feeder_state_path)

    def _read_state(self) -> dict | None:
        """只读液环主状态。任何解析失败返回 None（本轮跳过）。"""
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    # ── D4：赫布共激活喂送 ────────────────────────────────────────────
    def feed_d4(self, liquid_state: dict, fstate: dict) -> dict:
        hebbian, _ = self._engines()
        evidences = liquid_state.get("evidences", []) or []
        cursor = fstate.get("hebbian_cursor", "")
        cursor_dt = _parse_ts(cursor)

        recalled = []
        for e in evidences:
            dt = _parse_ts(e.get("last_recall_at"))
            if dt is None:
                continue
            recalled.append((dt, e.get("id"), e.get("recall_hits") or 0))
        recalled.sort(key=lambda x: x[0])

        batches = []
        if cursor_dt is None:
            # 冷启动种子：最近被召回的记忆 Top-K，一次性共激活
            seed = [m for _, m, _ in recalled[-BOOTSTRAP_TOP_K:] if m]
            if len(seed) >= 2:
                batches.append(("bootstrap_recent_recall", seed[:MAX_BATCH]))
            fstate["hebbian_bootstrap_done"] = True
        else:
            # 增量：按小时桶聚合召回窗口
            buckets: dict[str, list[str]] = {}
            for dt, mid, _ in recalled:
                if dt <= cursor_dt or not mid:
                    continue
                buckets.setdefault(dt.strftime("%Y-%m-%dT%H"), []).append(mid)
            for key in sorted(buckets):
                ids = buckets[key][:MAX_BATCH]
                if len(ids) >= 2:
                    batches.append((f"recall_window:{key}", ids))

        pairs = 0
        for ctx, ids in batches:
            pairs += hebbian.update_from_activation(ids, ctx)

        if recalled:
            fstate["hebbian_cursor"] = recalled[-1][0].isoformat()
        # 周期激活计数（供 D5 多样性计算）
        counts = fstate.setdefault("period_activation_counts", {})
        for _, mid, hits in recalled:
            if mid:
                counts[mid] = counts.get(mid, 0) + int(hits or 0)
        fstate["last_hebbian_run"] = _now_iso()
        return {"batches": len(batches), "pairs_updated": pairs,
                "recalled_pool": len(recalled), "cursor": fstate.get("hebbian_cursor", "")}

    # ── D5：再生抗衰指标采集 ──────────────────────────────────────────
    def feed_d5(self, liquid_state: dict, fstate: dict) -> dict:
        _, regen = self._engines()
        anchor_name = {}
        for a in liquid_state.get("anchors", []) or []:
            aid = a.get("id") or a.get("anchor_id")
            anchor_name[aid] = a.get("name") or a.get("anchor_name") or ""

        evidences = liquid_state.get("evidences", []) or []
        memories = liquid_state.get("memories", []) or []
        now = datetime.now()
        last_regen_dt = _parse_ts(fstate.get("last_regen_collect")) or (now - timedelta(seconds=REGEN_PERIOD))

        # 新蒸馏：周期内新增且锚点属蒸馏族
        new_distill = 0
        for e in evidences:
            dt = _parse_ts(e.get("timestamp"))
            if dt is None or dt <= last_regen_dt:
                continue
            nm = anchor_name.get(e.get("anchor_id"), "")
            if any(h in nm for h in _DISTILL_HINTS):
                new_distill += 1

        # 新领域：锚点名相对上周期快照的新增数
        seen = set(fstate.get("seen_anchors", []) or [])
        current = {nm for nm in anchor_name.values() if nm}
        new_domains = len(current - seen) if seen else 0

        # 新关联：赫布引擎周期增量（D4 产出的直接度量）
        hebbian, _ = self._engines()
        total_assoc = hebbian.get_stats().get("total_associations", 0)
        prev_assoc = fstate.get("last_assoc_total")
        new_assoc = max(0, total_assoc - prev_assoc) if isinstance(prev_assoc, int) else 0
        fstate["last_assoc_total"] = total_assoc

        activation_counts = dict(fstate.get("period_activation_counts", {}) or {})
        metrics = regen.collect_metrics(
            new_distillations=new_distill,
            new_associations=new_assoc,
            new_domains=new_domains,
            activation_counts=activation_counts,
            total_memories=len(evidences) + len(memories),
        )
        fstate["last_regen_collect"] = now.isoformat()
        fstate["seen_anchors"] = sorted(current)
        fstate["period_activation_counts"] = {}
        fstate["last_d5_run"] = _now_iso()
        return {"metrics": metrics, "new_distillations": new_distill,
                "new_associations": new_assoc, "new_domains": new_domains,
                "activation_diversity_pool": len(activation_counts)}

    # ── 单轮喂送 ──────────────────────────────────────────────────────
    def run_once(self, force_d5: bool = False) -> dict:
        with self._lock:
            cycle_start = _now_iso()
            result = {"status": "ok", "started_at": cycle_start, "steps": {}}
            liquid_state = self._read_state()
            if liquid_state is None:
                result.update({"status": "skipped", "reason": "state_unreadable",
                               "state_file": self.state_file})
                self.last_result = result
                return result
            fstate = self._load_feeder_state()
            try:
                result["steps"]["d4"] = self.feed_d4(liquid_state, fstate)
            except Exception as e:
                result["steps"]["d4"] = {"error": f"{type(e).__name__}: {e}"}
            due = force_d5
            if not due:
                last = _parse_ts(fstate.get("last_regen_collect"))
                due = (last is None) or ((datetime.now() - last).total_seconds() >= REGEN_PERIOD)
            if due:
                try:
                    result["steps"]["d5"] = self.feed_d5(liquid_state, fstate)
                except Exception as e:
                    result["steps"]["d5"] = {"error": f"{type(e).__name__}: {e}"}
            else:
                result["steps"]["d5"] = {"skipped": "period_not_due"}
            try:
                self._save_feeder_state(fstate)
            except Exception as e:
                result["state_save_error"] = f"{type(e).__name__}: {e}"
            result["finished_at"] = _now_iso()
            if any(isinstance(v, dict) and v.get("error") for v in result["steps"].values()):
                result["status"] = "partial"
            self.run_count += 1
            self.last_result = result
            return result

    # ── 后台线程 ──────────────────────────────────────────────────────
    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                self.last_result = {"status": "error", "error": f"{type(e).__name__}: {e}",
                                    "at": _now_iso()}
            self._stop.wait(FEEDER_INTERVAL)

    def start(self) -> bool:
        if not FEEDER_ENABLED:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="d4d5-feeder", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def status(self) -> dict:
        return {
            "module": "insight_write_side",
            "version": "1.0.0",
            "enabled": FEEDER_ENABLED,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "run_count": self.run_count,
            "interval_sec": FEEDER_INTERVAL,
            "regen_period_sec": REGEN_PERIOD,
            "store_dir": self.store_dir,
            "state_file": self.state_file,
            "last_result": self.last_result,
        }


# ── 模块级单例（由 api 装载）────────────────────────────────────────────
feeder: InsightWriteSide | None = None


def init_feeder(hebbian_engine=None, regeneration_engine=None, autostart: bool = True) -> InsightWriteSide:
    global feeder
    feeder = InsightWriteSide(hebbian_engine=hebbian_engine,
                              regeneration_engine=regeneration_engine)
    if autostart:
        feeder.start()
    return feeder


def feeder_status() -> dict:
    if feeder is None:
        return {"module": "insight_write_side", "enabled": FEEDER_ENABLED,
                "thread_alive": False, "error": "feeder_not_initialized"}
    return feeder.status()


def feeder_run_once(force_d5: bool = False) -> dict:
    if feeder is None:
        return {"status": "error", "error": "feeder_not_initialized"}
    return feeder.run_once(force_d5=force_d5)
