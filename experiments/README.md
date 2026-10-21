# experiments/ — AgentJev × 液环 轻量旁路（advisory·fail-open·零核心侵入）

> 撤除方式：删除本目录即可，液环核心库与编排层零改动、零引用。

## 文件
| 文件 | 作用 |
|---|---|
| `decision_advisor.py` | 8149 AgentJev 决策服务封装（boolean/choice/score，fail-open） |
| `semantic_gate.py` | 语义原语：①`crystallize_gate(new, match)` → duplicate/conflict/distinct 三值裁决（阈值 0.75）②`governance_risk_gate(content)` → 删除风险 safe/risky |
| `advisory_audit.py` | 旁路审计：最近 N 条证据 vs 同锚点最新既有证据，结果追加 `advisory_log.jsonl` |
| `poc_decision_advisor.py` | P1 PoC 对照脚本（真实 state 只读，6/7 达标） |

## 运行
```bash
unset http_proxy https_proxy   # 本机 49183 代理必须 unset（回环直连）
python3 advisory_audit.py --n 10   # 接活判据：exit=0 + advisory_log.jsonl 追加
```
前置：AgentJev 服务 @127.0.0.1:8149（`curl --noproxy '*' http://127.0.0.1:8149/health`）。

## 已知行为特征（PoC + 实测）
- 对「同主题不同日期的流水/快照」判 distinct（如 09-06 vs 09-07 评审快照、两天各一次 health 记录）——模型把时间快照视为独立事件，与液环「时间序列证据」语义一致，非 bug。
- 中文液环记忆（含压缩引用格式）判断有效：PoC 6/7，唯一 FAIL 为期望标签错。

## 边界（红线）
- advisory-only：输出仅为建议，调用方须把 None/低置信视作「模型弃权 → 走原规则」。
- 本目录脚本只读 `~/.liquidloop/memory/.liquid/state.json`，不写液环任何状态（remember_hook 日志除外）。

## 运行时接线状态（2026-09-26 三钩全接活）
- **① remember 结晶 gate**（`ll_remember_core.py`，备份 `.bak-20260926`）：结晶 `matched=True` 时调 `crystallize_gate(timeout=2.0)`，裁决附响应 `semantic_gate` 字段 + 日志（`source=remember_hook`）。
- **② governance 删除风险**（`ll_governance.py`，备份 `.bak-20260926`）：`ll_delete`/`ll_dissolve` 在**变更前取证**内容、执行后调 `governance_risk_gate(timeout=2.0)`，附响应 `risk_gate` 字段（risky>0.5 仅建议人工复核，**绝不阻断**）+ 日志（`source=governance_hook`）。dry_run 同样触发。
- **③ recall 去冗余灰区**（`ll_recall.py`，备份 `.bak-20260926`）：top-2 证据 jaccard 落灰区(0.15~0.75)才调 `crystallize_gate`（规则可判定零开销直通），附首条证据 `semantic_dedup` 字段 + 日志（`source=recall_hook`）。
- **开关**：env `LIQUID_SEMANTIC_GATE=0` 全局关闭三钩；任何异常/超时静默跳过，绝不阻断主路径。
- **端到端实测（09-26）**：① matched=True（score 0.35）→ `semantic_gate` 落响应+日志；② `/delete dry_run=true` → `risk_gate:{safe,0.159,328ms}`；服务启动自检（ll_server_selfcheck 内部 ll_delete）亦真实触发 `risk_hook` 日志；③ `/recall` pair_jaccard=0.657 灰区 → `semantic_dedup:{distinct,0.195}`。reload 后 `/health`+`/list` 正对照通过；双解释器 py_compile（homebrew 3.14.5 + venv 3.13）。
- **回滚方式**：三份 `.bak-20260926` 各自 `cp` 回原名后 reload 8790，或 `LIQUID_SEMANTIC_GATE=0` 一键关。
- **已知行为特征**：模型对「增量印证类相近证据」判 distinct（dup≈0.17~0.20）而非 duplicate——与「时间快照=独立事件」偏好一致；判定真实重复需 dup_prob 显著更高（阈值 0.75）。
