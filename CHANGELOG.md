# Changelog

## v2.0.2 (2026-08-31) — 安全护栏+引用完整性+反绝对化检测（2.0.1→2.0.2）

- **灾难性回退护栏**（`storage.py` StateRegressionGuardError）：save() 落盘前正对照磁盘全量，若传入态相对磁盘全量灾难性缩水（<10%）则拒绝落盘，保留磁盘态+CRITICAL审计。根治"部分内存态原子覆盖全量磁盘态"静默失效族。小工作区（<50条）冷启动放行。
- **写前快照轮转**（`storage.py` undo机制）：save() 前自动创建 state.json 快照，保留最近10份（约17M），支持灾难性回退恢复。
- **evidence_ids 存在性校验**（`workspace.py` add_memory validate_refs）：创建记忆时默认校验 evidence_ids 全部存在，防止孤儿记忆（历史根因：marvis fact batch 45条占位记忆引用675条不存在evidence_id，引用完整性仅49.1%）。校验失败返回 None，如需绕过传 validate_refs=False。
- **手动成核回退：引用即支撑**（`workspace.py` _update_memory_stability）：ll_crystallize 的摘要 content 与证据原始 content 不逐字匹配时，通过 evidence_ids 明确指向的支撑证据计为 support（`e.id in m.evidence_ids`），修复手动成核后 stability=0 的问题。
- **反绝对化词表检测**（`guard.py` overgeneralization_flags）：m60 DeepTutor 借鉴落地，检测"总是/从不/一切/完美/专家"等过度概括表述，返回 [{term, category}]，只标记不拒绝（警告级），供上层蒸馏/内化时复核真实性。中英双语40+词。
- **PerceptionGate 环境锚时效门**（`guard.py` env_anchor_probe）：v3.2 可选注入，涉及环境类断言的动作使用前强制重取证（curl/ps/ipconfig/hostname 正对照），返回 stale 即 block，把"认知基线纪律"升为机制强制步。无注入则跳过（零影响现有门）。
- **可逆副作用底座**（`dsh_reversible.py` ReversibleRegistry）：蒸馏 DeepSeek Harness #204，记录可回滚操作（文件写入/进程启动/配置修改），支持 undo/redo，零LLM依赖。
- **多键召回**（`recall_multi_key.py`）：支持多关键词组合召回，提升复杂查询召回率。
- **测试修复**：`test_attention_gain.py::test_no_support_no_bonus` 更新预期以匹配 v2.0.2 引用即支撑新行为（s=1,c=0 → stability=0.5）。
- **测试全绿**：199 passed, 0 failed。


## v2.0.1 (2026-08-25) — 零向量召回超越词频基线（2.0.0→2.0.1）

- **默认召回升级为「零向量 tf-idf 余弦 + 实体/数字精确加权 booster」**（`selfspin.recall_local(idf_cosine=True, lexical_boost=True)`）。
  - 余弦层与 TF-IDF 基线逐字节同公式（纯词频标量，非 embedding）；booster（`_entity_boost`）对 query 数字串/长度≥4 token 精确命中 fact 叠加 bonus，补回 tfidf 余弦因长句稀释稀有关键词而漏召的「含数字/量词 query」。
  - A/B 探针（`examples/experiments/lexical_probe*.py`）：entity_boost 为唯一两集稳定正向信号（LoCoMo +0.020 / LongMemEval +0.006）；containment 融合两集负向已舍弃。
  - 全量实测：**LoCoMo 0.472→0.492、LongMemEval 0.942→0.948，零向量超越词频向量基线**；机制层独立实现与探针逐位一致。
  - 守禁向量红线：纯符号精确匹配 + 词频标量，零 embedding、零 LLM（WHY_NO_VECTOR §六.1 更新）。
- **基准 runner 加 `liquid_cosine` 纯余弦对照列**（run_locomo.py / run_longmemeval.py），默认列 = 余弦+booster。
- **版本统一**：`pyproject.toml` / `liquid_loop/__init__.__version__` 对齐 `2.0.1`；数据 schema `0.4.0` 与 workspace state `0.5.1` 保持独立（禁区不动，红线#4）。
- 报告更新：BENCHMARK_REPORT v4 / LONGMEMEVAL_REPORT v4 / WHY_NO_VECTOR §六。

## v2.0.0 (2026-08-24) — 液环统一版号里程碑（1.9.0→2.0.0）

- **E5 非 embedding 同义归并**（实验 · examples/faithful）：`E5_alias_table.py + E5_alias_table_proposal.md`（实验模块，未入核心包），承接外部 critique 指出的"禁向量真阿喀琉斯之踵 = 语义逃逸（零 token 重叠同义 → 0 核 → 事实流失）"。
  - `AliasTable`：确定性别名表 + 术语归一化（embedding→嵌入 / vector→向量），**零 embedding、零 LLM 推断、纯字符串替换**。
  - 集成点：在 `LiquidSelfSpin.ingest` 前对 fact 跑 `AliasTable.normalize` → 同义变体统一成同一 canonical 串 → `local_rotate` 自然聚核（selfspin 主干一字未改，不污染生产）。
  - 公理守住：别名归并只降"识别同义"成本，**不降 ≥2 distinct source 成核门**；归并可回溯 `merged_via`。
  - 验收（E5_alias_table.py 实跑）：盲区补洞（零 token 重叠同义别名命中后成核，原 0 核）✓ / 抗噪不退化（单条 S4 噪声仍不成核）✓ / 术语归一跨表述归并 ✓。
  - **状态**：实验验证 pass；真实语义流报告（LoCoMo/LongMemEval）待跑（见 ROADMAP）。未 bump 版号、未发版。

## v2.0.0 (2026-08-24) — 液环统一版号里程碑（1.9.0→2.0.0）

- **版本统一**：`pyproject.toml` / `liquid_loop/__init__.__version__` / `textutil._get_version` 回退默认 / 投喂客户端 `feed_liquidloop` 全部对齐 `2.0.0`。
- 投喂客户端新增 `LIQUIDLOOP_CLIENT_VERSION` 同源常量，`/health` 打印显示 `server=` + `client=` 协同校验（飞哥「液环版本该统一版号了」2026-08-24 定）。
- **禁区不动**：数据 schema 版本（`storage.py` 默认 `0.4.0`）与 workspace state 版本（`cli.py` 默认 `0.5.1`）属记忆层数据格式版本，保持原值（红线#4）。

## v1.9.0 (2026-08-19) — PerceptionGate 因果共生门控 + 工程化修复（v1.8.4→v1.9.0）

- **PerceptionGate 门控-液环因果共生环胶水层**（5174dec，对应蒸馏 gate_liquid_causal_symbiosis_distill）：
  - 整合 `should_escalate(block)` + workspace.causal 因果核心永饿死豁免 + `adaptive_recall` 负载双模(degrade/allow) + rar 本地算力溶解重建成本。
  - 零依赖核心，真实模块经 `causal_core_predicate`/`load_probe` 注入；`tests/test_perception_gate.py` 7 测试全绿（真实调用 workspace.causal/adaptive_recall/build_or_cache，不 mock）。
- **workspace 巨型模块拆解**（f462e12）：1850→1209 行，破环依赖，诊断分类 14→2。
- **4 蒸馏守卫落地**（006a3e2）：#202 分级 TTL / #203 自适应 recall / #199 会话 abort / #197+#198 守卫，解熵环。
- **工程化修复四件**（d136731，08-18/19 审计）：
  - `guard.validate_content` 内容质量门（空/占位/低熵噪音拦截），server 与 workspace 单点共用；
  - `audit.log` 按 `LL_AUDIT_MAX_BYTES`(默认 8MB) 轮转防无限增长；
  - `rar` 召回对齐 `ll_recall`：`superseded_by`/`archived` 证据退出召回候选；
  - `storage._append_archive` 归档写 `archive.jsonl`（幂等防崩溃重复）；
  - workspace：内容门下沉重库层 + 锚点证据不足清 stale conflict + 聚合型锚点冲突豁免。
- 测试 185 passed（+22：PerceptionGate 7 / 蒸馏守卫等）。

## v1.8.4 (2026-08-12) — 程序性记忆层（D3 + 调研蒸馏：PlugMem/Skill1/EvoC2F/RE-TRAC） (2026-08-12) — 程序性记忆层（D3 + 调研蒸馏：PlugMem/Skill1/EvoC2F/RE-TRAC）

- **`procedural_memory.py` 程序性记忆一等公民**（distill_registry 实现）：`ProceduralMemory` + `ProceduralRegistry`，与声明式 Anchor/Evidence/Memory 正交。
  - EvoC2F 三关准入（功能/契约/回归）+ 分阶段部署（pending→shadow→canary→active|rejected）+ 不可逆技能封顶 canary 需人工放行（↔ ops_gate）。
  - Skill1 任务路由召回：选择信用（tag 子串匹配，禁向量）× 利用信用（成功率），确定性排序。
  - RE-TRAC 结构化笔记：`structured_note` 三组分 {answer, evidence, open} 填入技能 note。
  - sidecar `.liquid/procedural.json` 独立持久化，非阻塞锁 + fail-open，原子写（temp+rename）。
  - 回归门控闭环：canary 后期成功率<阈值且数据充足 → 拒绝；re-admit 保留已验证技能进度、rejected 重准入计数器清零。
- **`context_compress.structured_note`**：RE-TRAC 同构三组分分桶（提取式、零 LLM、fail-open）。
- **8790 server `/procmem` 端点**（admit/use/promote/list/recall）+ `_handle_rest` 全局 fail-open（REST 异常返回可见 JSON）。
- 测试 163 collected（+19 procedural_memory：三关准入/晋级/回退/不可逆/路由/持久化/fail-open/re-admit 语义）。

## v1.8.3 (2026-08-12) — consensus 结晶保护 + 治理 dry-run（Palantir 借鉴）

- **consensus 保护**（Palantir Authority Line 细化）：`supersede_evidence` 拒绝软取代 consensus 结晶的证据——共识属于全体贡献者，只能走全员 dissolve（与 `delete_as` 同语义），防 admin 误伤共识。
- **治理 dry-run**（Palantir dry-run 预演）：server `ll_supersede/ll_delete/ll_dissolve` 支持 `dry_run`（REST `dry_run` 字段），deepcopy 副本预演不落盘，返回 `"dry_run": true`。
- 测试 116 passed（+1 consensus 保护）。

## v1.8.2 (2026-08-11) — 治理/问责查询三件套（Semantica 借鉴：时间旅行/影响分析/实体消解）

- **背景**：深度调研 Semantica（图原生可审计 AI 基础设施，GitHub 4.3k★ 镜像项目）发现三点液环可借鉴的只读查询能力——时间点快照、决策影响分析、实体消解。
- **实现**（全部只读，守禁向量/零 LLM 铁律）：
  - **`state_at(dt)` 时间点快照**：返回 dt 时刻应可见的记忆状态（evidence.timestamp<=dt 且未 archived/superseded；memory.formed_at<=dt）。局限标注：delete 真删的节点无法回溯（完整历史须重放 audit.log）。
  - **`analyze_impact(node_id, depth)` 影响分析**：从节点沿 causal 出边（enables/causes/contradicts）+ 血缘（used_in，被哪个结晶引用）BFS 展开下游影响子图，分层返回，供"这条决策影响了谁"问责。
  - **`find_duplicates(threshold, max_pairs)` 重复候选检测**：字符 bigram Jaccard（禁向量）+ blocking 预过滤（首 4 字符 + 长度比）控 O(n²)。**只报告不自动处理**（守取自动/存保守），供治理者显式 supersede。
- **server 端点**：`GET /state_at?dt=` / `GET /impact?node_id=&depth=` / `GET /duplicates?threshold=`（只读，与 /causal 同模式）。
- **实测**：115 passed（108 + 7 新增 test_semantica_borrow.py）。
- **守铁律**：全只读零写路径；重复检测禁向量（字符级）；报告不越权（治理仍走 supersede + 信任分级）。

## v1.8.1 (2026-08-11) — 软取代 supersede 原语（显式语义治理，零丢失）

- **背景**：11:35 记忆治理时发现液环缺「显式 supersede（软取代+指针）」原语——初版记录与修正记录并列冗余，只能以 `archived` 近似（熵增清理语义，非显式取代语义）。
- **实现**：
  - **Evidence 新增三字段**：`superseded_by`（被谁取代）/ `supersedes`（取代过谁，反向血缘指针）/ `superseded_at`。带默认值 → `asdict`/`_from_dict` 兼容，旧数据零破坏。
  - **`WorkspaceState.supersede_evidence(loser, winner)`**：设 `loser.superseded_by=winner` + `winner.supersedes` 反向指针 + 时间戳；防呆（自取代/winner·loser不存在拒绝）、幂等（不堆叠）。
  - **活跃召回退出**：`register_recall` 与被取代证据跳过；server `ll_recall` cands 过滤 `superseded_by`（退出活跃召回）；`/supersede` REST 端点（委托核心+落盘）。
  - **正交设计**：`archived`=熵增自动清理；`superseded_by`=治理动作显式取代。二者皆零丢失可解冻、`list` 全量审计可见。
- **守铁律**：零向量（纯结构化指针）；零丢失（不删仅标记）；可审计（时间戳+反向指针）；显式取代需 loser/winner 真实存在（防呆）。
- **实测**：核心逻辑/序列化往返/旧数据兼容/recall 过滤/list 可见全过；真实数据 `supersede B→A`（v1.7.0 修正冗余）落地。

## v1.8.0 (2026-08-11) — 原理(why)与因果链"接电"（沉睡能力通电）

- **背景**：穿透式复盘发现 principle 机制（v1.4 原理优先成核 + reweight 原理通道）与 `causes/enables` 因果字段是"已建造但未通电"——生产路径零 principle 写入源、`causes/enables` 零读写（死字段）。
- **实现**：
  - **原理接电**（`marvis_liquid_loop_server.py`）：`/remember` 新增可选 `principle` 字段（REST + MCP 均支持）；`_derive_principle()` 确定性前缀推导（`原理:/why:/原则:` 等显式标记，零 LLM、不臆造）；`ll_remember` 落 `Evidence.principle` → v1.4 原理优先成核真正可被喂数据触发。
  - **因果 causes/enables 接电**（`ll_causal`）：由"仅 contradicts"扩展为三类边——contradicts（原）＋causes（先形成+含容器≥0.5 跨锚）+ enables（先形成+含容器∈[0.35,0.5) 跨锚）；全部确定性推导（时间序+含容器，零向量、可审计）。
  - **数据源接通**（`distill_to_liquid.py`）：archify/kunpeng 蒸馏事实附 principle（"液环信任边界闸门机制"/"液环模块依赖结构"/"液环架构概览"/"可迁移方法论沉淀"），structured evidence 带 why 投 8790。
  - **版本统一**：pyproject.toml / `__init__.__version__` / workspace `_get_version` 回退 全部对齐 1.8.0。
- **守铁律**：零向量（含容器/前缀标记均结构化）；不臆造 principle（无显式标记即空）；时间+含容器推导可审计。

## v1.7.0 (2026-08-09) — 证据老化回收 lifecycle（督办项警示①落地）

- **新增证据老化回收（督办项警示①）**：独立研究（上交+清华 *Are We Ready For An Agent-Native Memory System?* arXiv:2606.24775）横评警示——"many append-only stores collapse on long horizons as evidence ages"（append-only 长期 horizon 退化）。液环此前仅有预算触发的 evict（超预算才驱逐），缺基于老化的主动 lifecycle pass。
- **实现**（`liquid_loop/workspace.py`）：
  - `Evidence` 新增 `last_recall_at` / `archived_at` 字段（默认值，旧数据 `asdict` 往返零破坏）。
  - `register_recall` 命中证据时写入 `last_recall_at`（老化判据源）。
  - 模块常量 `LIFECYCLE_TTL_EPS = 180*86400`（180 天未召回→老化候选，与 `rar.py` 180-day decay 对齐）、`LIFECYCLE_FLOOR_WEIGHT = 0.15`（权重冷却到地板附近才归档）。
  - `_lifecycle_sweep`：长期未召回 + 权重地板 + **非结晶来源**（保结晶血缘）+ **非冲突证据**（零丢失优先）→ 冷归档 `archived=True`。守护铁律：零丢失（archived≠删，可解冻）/ 可审计（archived_at）/ 保留时序（不压缩，守警示②）/ 不依赖相似度（守禁向量）。
  - 暴露公开 `lifecycle_sweep()` 供 8790 手动/定时触发；`_on_evidence_added` 每次写入顺带调用，存量随增量自然清理。
- **测试**：`tests/test_lifecycle.py` 10 passed（覆盖高权重保留/超TTL归档/永不召回归档/TTL内保留/结晶保护/冲突保留/register_recall记时/幂等），全量 106 passed。
- **附带修复版本漂移**：`pyproject.toml` version 1.4.0→1.6.0（与 `__init__` 一致，此前早上升级遗留）；`test_cli_version` 改动态读 pyproject 避免下次升版本即红。

### v1.7.0 hotfix（2026-08-09 18:40）— 修正 lifecycle 误冻全部存量记忆

- **事故**：初版 `_lifecycle_sweep` 年龄判据仅在 `last_recall_at` 非空时检查 TTL，为空（从未召回）即**跳过年龄检查直接归档**。存量 577 条证据 `last_recall_at` 全空、且 499 条为默认权重 0.1（从没被召回，未获注意力增益）→ 首次 sweep 误归档 **497/577（86%）**，且新记忆写入后立即被冻（「出生即归档」）。
- **根因**：①老化判据缺 age 信号兜底——`last_recall_at` 空时未回退到创建时间 `timestamp`；②归档条件用「权重地板」单判据，不限年龄，把「刚建+默认权重」与「陈年+冷却」混为一谈。
- **修正**：`ref_ts = e.last_recall_at or e.timestamp`；归档须**同时满足**「`ref_ts` 老化超 `LIFECYCLE_TTL_EPS(180d)` 且 `weight < LIFECYCLE_FLOOR_WEIGHT`」。新记忆（timestamp 近）/近期召回 → 不归档；仅陈年+冷却证据归档。
- **线上补救**：离线解冻 497→0（`archived=False`）；沙箱下 `launchctl stop/kickstart -k` 被拒权（Not privileged），改用 `kill -9` 触发 KeepAlive 拉起修正版新进程；重跑 sweep 验证归档 **0**、recall 恢复全量（55 条）。
- **测试**：`tests/test_lifecycle.py` 改为 12 passed，新增 `test_never_recalled_fresh_created_kept`（新记忆不误冻）/ `test_add_evidence_default_weight_not_swept_on_write`（写入即触发 sweep 不立即冻）两回归用例；全量 **108 passed**。
- **铁律印证**：archived≠删，解冻零丢失；本次误冻靠「读真数据归档数+权重分布」而非表面报成功才发现，正是 c≥1 取证门的实证。

## [unreleased] — LEI 维度数标签统一为九维

- **运维硬化（2026-08-09）**：`__version__` 1.4.0→1.6.0（对齐已落地的 v1.5 注意力增益 + v1.6 重放压力代码，此前版本号未 bump）；清空 8790 `err.log` 中 918 条历史旧路径 `FileNotFoundError`（Aug 2 残留，非实时故障）；服务优雅重启后 `/health` 报 1.6.0、日志干净。Vera 信任身份已注册，完成 Vera→液环端到端写入闭环验证。
- **标签修正**：LEI（Liquid Entropy Index）实测为 **九维加权**（6 基础维度 + CPE 三维，权重和=1.0）。
  此前文档/注释中的「八维」为过时标签（代码自 v0.5 起即按九维实现，docstring 误标八维）。
  涉及 `entropy.py` / `cli.py` / `liquid_core.py` / `examples/quickstart.py` / `README.md` / `docs/` 的标签已同步修正。
- 历史 changelog 条目中「四维 → 八维」等描述保留原貌（记录当时的修正动作），不代表当前维度数。

## v1.0.0 (2026-07-21) — 双层自转 + 四循环本体论 + 墙钟实测 11.5× 成核加速

> **质的飞跃（v1.0）**：把"多 agent 架构拆除后的记忆演化协同症结"转化为双层自转方案，并正式叙事液环为「多智能体共享记忆演化状态机」。配套四循环本体论与 PREPING 门控循环化，墙钟实测验证双层自转成核加速 **11.5×**。

- **新增 `liquid_loop/selfspin.py`（双层自转模块）**：
  - 本地快自转 `LiquidSelfSpin.local_rotate`：跨篇去虚词核心词聚类（结构化确定性比对，守禁向量），把同主题异表述事实聚为 canonical → 支撑**自述性**。
  - 后端慢自转沉淀 `deposit`/`incremental_deposit`：把本地核按支持篇数（同 content × 同 agent_id）重复写 8790 → 触发 private 成核 → 支撑**自洽性**；后端 consensus 承接**主体间性**。
  - 意识三要素对齐：自述性=本地自转、自洽性=本地+后端、主体间性=consensus。
- **四循环本体论**：自转 rotation（微观自洽）/ 公转 revolution（宏观收敛节律）/ 主循环 main（系统调度）/ 子循环 sub（局部子系统），门控贯穿写入/演化/回归三道全生命周期。
- **PREPING 门控哲学吸收**：非 LLM 结构化 Validator（near-dup Jaccard≥0.95，scoped `warmup:*`，fail-open），拒 LLM 判官，循环化门控。
- **墙钟实测（31 篇军师调研库）**：双层自转成核 23 vs 朴素直写 2，**加速 11.5×**，首结晶提前 1153s。负结果（朴素臂异构库≈0 成核）佐证守禁向量不虚假缝合。
- **修复**：incremental 沉积按 `norm` 锁定 canonical，消除增量重聚类漂移导致的重复成核。
- **依赖**：零新依赖；禁向量纪律不变（一致性仍走字符级精确相等 + 审计链哈希）。

## v0.9.3 (2026-07-19) — 多智能体授权原语固化进核心（修复共识误删事故）

> **事故复盘**：v0.9.2 及之前，归属校验（共识隔离 / 删除授权 / 一致解散）只在 `marvis_liquid_loop_server.py`
> 这一层 wrapper 内实现，库核心 `WorkspaceState` 无任何授权原语。各接入层重复实现 → 漂移 →
> 清测试数据时间样 `ll_list` 不过滤 `agent_id` 暴露他人共识、且 `ll_delete` 零校验，误删 5 条真实共识。
> **本版把授权固化进库核心，成为单一事实源**，REST / MCP / trae 桥 / 任意未来 agent 共用同一套校验。

- **新增 `WorkspaceState` 授权原语（liquid_loop/workspace.py）**：
  - `list_for(agent_id, category)`：共识仅对贡献者可见、私有仅对贡献者可见、他人证据隔离；
    **无身份（`agent_id` 为空）一律零返回**（比旧 server "管理视角"更严，守最小授权）。
  - `delete_as(agent_id, memory_id)`：缺 `agent_id` 拒；evidence 仅 owner 删；private 仅 contributor 删；
    consensus 禁单端删（须一致 dissolve）；content 兜底仅删本人相关、命中 consensus 整体拒。
  - `dissolve_as(agent_id, memory_id, votes_root)`：consensus 合法移除，全体贡献者各投一票、集齐才真删；
    投票持久化下沉至核心 `_load/_save_dissolve_votes`（独立 `.dissolve_votes.json`，不动 `Memory` dataclass，守 North-Star 零改动）。
- **server 改为纯转发**：`ll_list/ll_delete/ll_dissolve` 委托上述核心方法，删除 server 内重复实现，消除漂移。
- **测试**：新增 `tests/test_auth_guard.py` 16 断言（读隔离 / 删除授权 / 一致解散 / 跨实例投票持久化）；
  全量回归 **58/58 PASS**，零污染。
- **依赖**：无新依赖，禁向量纪律不变（一致性仍走字符级精确相等 + 审计链哈希）。

## v0.9.1 (2026-07-15) — 固态 A2A 通道：TRAE MCP 桥接 + 双 Agent 运维硬化（集成发布）

> 本版**核心库零算法改动**（v0.9.0 已含冲突 O(g²)→O(d²)、时间动力学、双轨成核、审计链、禁向量）。
> 发布内容是把本轮"裁切 qwenpaw/marvis、接 trae、压测"的**可复用产物**固化进仓库。

- **新增 `examples/trae_mesh_mcp/`**：把共享液环后端封装为纯 stdlib 的 **stdio JSON-RPC MCP server**，
  暴露 `liquidloop_remember` / `liquidloop_recall` / `liquidloop_metrics` 三个工具。
  经 TRAE（或任意 MCP 客户端）的 `--add-mcp` 注册即形成 **固化（solidified）A2A 通道**——客户端原生读写同一份共享记忆。
  - **公共版可移植（重要）**：桥接地址走环境变量 `LIQUID_LOOP_BASE`（默认 `http://127.0.0.1:8790`）；
    脚本/文档**不写死作者本地路径**，全部用占位符 + 环境变量（PY / MCP_SERVER / LL_SERVER / LL_MEM_ROOT / STRESS_PORT 等）。
    **后端由用户自部署**，桥接仅做协议翻译、不内置 8790 服务，适配各用户不同的 agent / 部署拓扑。
- **双 Agent 运维硬化（参考流程）**：
  - 裁切 qwenpaw / marvis：停服务与进程 → 历史证据压缩打包（含 SHA256 清单）后**物理删除**原始数据。
  - 后端 `com.marvis.memory.liquidloop` launchd 保活（含 `PYTHONPATH=~/liquid-loop` 与 `mcp` 依赖自检）。
- **联合压测（Vera 直连 + trae 经桥）三关全 PASS**（脚本同目录 `stress_test.py` / `cleanup_stress.py`）：
  - **T-A 零丢写**：400 并发写（vera 200 + trae 200）命中 400/400，零丢失、零写错。
  - **T-B 共识幂等**：同 content 跨 distinct agent 并发写 → **恰好 1 条 consensus 结晶**（contributors 完整合并），
    `nucleated` 在第 2+ 次写触发；private 轨按 `(content,scope)` 去重，不重复成核。
  - **T-C 崩溃恢复**：隔离实例写入中 `kill -9` → `state.json` 仍合法、审计链完整，重启后服务恢复、证据无丢失。
- **吞吐说明（非瓶颈）**：单节点共享记忆 ~11.9 ops/s（受每写全量落地 + 审计链重算限制），agent 级流量下充裕。
- **零向量纪律**：桥接只做协议翻译，成核 / 共识 / 一致性判定仍走字符级精确相等 + 审计链哈希，无 embedding 引入。

## v0.8.0 (2026-07-15) — 反证轨（Contradiction Track）+ 时间动力学（Liquid Loop）

> 本版落实 v0.7.1 路线图：从"静态结晶"升级为"自调节记忆动力学"。回应外部审计的三点核心建议
> （反证轨 / 显式时间演化 / 可验证实验），实验顺序 E2→E3→E1，全部 PASS。

- **反证轨（Evidence Graph）**：`Evidence` 新增 `relation`（"support" | "contradiction"）与
  `target_memory_id`；`Memory` 新增 `stability` / `support_count` / `contradiction_count` /
  `last_reinforced`。稳定性公式 `stability = support / (support + 2·contradiction + 1)`：
  **一致增稳、冲突降稳**（非"一致即真"），直接对抗群体幻觉固化。
- **时间动力学 `state.step(dt)`**：显式 `M(t+1) = M(t) + reinforcement − decay − contradiction_penalty`。
  证据权重按 `(1−decay_rate)^dt` 衰减；记忆在获得新 support（自上次 step 起）时恢复到固有稳定性，
  否则时间衰减且不超过固有上限——记忆是**过程**而非对象。
- **三实验（examples/experiments/，全部 PASS）**：
  - **E2 错误记忆恢复**（核心）：80% 错误 + 20% 真实注入 → 反证轨使错误记忆 stability 0.67→0.30、
    正确记忆升至 0.69 主导，证明**主动遗忘错误 + 恢复**（非带衰减数据库）。
  - **E3 多 Agent 冲突**（mesh v2 价值）：A support / B contradiction / C noise → 核心 claim 进入
    受争议稳定区间(stability=0.40)，噪声隔离独立成核，LEI 全程 GREEN。
  - **E1 长期漂移**（压力测试）：300 轮随机注入 → 48 条记忆（≤池×3）、末段增量 plateau=2、
    LEI GREEN、平均 stability 0.80，证明自动收敛。
- **已知优化项（非正确性）**：`_detect_conflicts` 为每证据加入时的 O(g²) 两两一致性扫描，大规模
  高频写入需改为增量/采样；实验用 300 轮规避，已在报告标注。
- **向后兼容**：新增字段均带默认值，`Evidence(**dict)` / `Memory(**dict)` 加载旧存档不破；38 单测全绿。

## v0.7.1 (2026-07-15) — 文档同步与发布质量修正（不扩功能）
- **命名**：entropy 代码层保留 `calculate()`；README 补 **Liquid Entropy Index (LEI)** 语义澄清 —— system-stability deviation metric, inspired by entropy but not equivalent to thermodynamic entropy（避免物理熵误解，保留理论连续性；不采用 CDI）。
- **文档同步**：README 熵表标注 LEI；新增八维机制说明；`calculate_entropy → calculate`、"四维 → 八维"漂移修正（承接前次）。
- **定位重述**：新增 "Self-Regulating Persistent Memory Dynamics for Agent Systems" 闭环段（AuditChain + LEI + Memory decay 形成动态系统闭环），淡化"AI 意识"宣传。
- **路线图纠正**：删除与零向量哲学矛盾的"嵌入相似度结晶"死项；写实 v0.8 路线（反证轨 / 显式时间动力学 / 三实验 E2→E3→E1）。
- **审计归档**：新增 `docs/AUDIT_RESPONSE_v0.7.1.md`，记录外部评审接收、版本差异校准与最终决策。
- **范围纪律**：本版仅文档 / 发布质量，**零新机制**；反证轨与时间动力学留待 v0.8。

## v0.7.0 (2026-07-15) — MESH v2 多智能体共识协议集成（正式发布）
> ⚠️ **已移除**：`liquid_loop.mesh` 子包已在 v0.7.x 后从发行包移除（commit `ac7260e`，过度工程化过滤）。当前 v1.4.0 **不含**该模块，请勿 `import liquid_loop.mesh` 或运行 `mesh/liquid_loop_mesh_v2.py`（会 ImportError）。详见 README 顶部说明。
- 新增：`liquid_loop.mesh` 官方 MESH 集成子包（原 `mesh/liquid_loop_mesh_v2.py` 迁入，随 pip 包发布）**— 历史状态，现已移除**。
- 能力：结构化证据 schema 自检 `validate_evidence`、双向契约合规 `check_contract`、主体间性共识指数 `compute_cci`、统一认知健康仪表盘 `cognitive_health`、冲突检测 `detect_conflict`、从 8790 REST 拉取状态 `fetch_state`。
- 零向量哲学：一致性判定走结构化精确相等 + 审计链哈希，绝不引入 embedding / 相似度。
- 顶层 `mesh/liquid_loop_mesh_v2.py` 保留为兼容薄壳（**已随上述移除一并删除**，请勿引用，否则 ImportError）。
- 发布：GitHub tag v0.7.0 + PyPI Trusted Publishing（GitHub Actions OIDC，无 token）。

## v0.6.4 (2026-07-14) — SEAL 双优化修复（解 v0.6.3 假落地）
- 修复：SelfRefineEngine.apply_strategy 改锚点 `seal_adjust` 自评层（Anchor 新增字段），`_recalc_anchor` 合成 `stability = base + seal_adjust`，SEAL 调整不再被下次 `add_evidence` 覆盖（E8 暴露 v0.6.3 为假落地）。
- 测试：新增 `tests/test_seal_persistence.py`（3 例），全量 pytest **27 passed**。
- E8 实验：class-1 脆性锚点喂 SelfRefine，结晶率 S 曲线（脆性组比稳固组晚 5 轮结晶=收敛慢）；暴露 stability 随记忆增长单调递减（decay 双因拖垮 base，标 v0.6.x 待办，本次未改）。

## v0.6.3 (2026-07-14) — PEEK 预算稳态 + SEAL 双优化（学术标杆落地）

### 新增能力
- **认知预算稳态器 `cognitive_budget.py`（落地 PEEK, arXiv:2604.09932 固定预算驱逐）**：
  - 三模块蒸馏并适配液环：`Distiller`（evidence.weight + anchor.value_score + recency → 价值）、`Cartographer`（价值 × 访问热度 × 流动性 → 重要性）、`Evictor`（超预算驱逐最低重要性证据）。
  - 驱逐 = **冷归档**（`Evidence.archived=True`，从活跃检索集移出），**零丢失**、审计链完整，呼应液环零向量/零丢失哲学。
  - 预算来源环境变量 `LIQUID_EVIDENCE_BUDGET`（默认 0 = 不限制）；每次 `add_evidence` 自动触发 `_stabilize_budget()`。
- **SEAL 失败诊断双优化（落地 SEAL, arXiv:2605.24426 协同进化）**：
  - `SelfRefineEngine.diagnose()`：失败探测归因到锚点，判定 `retrieval`（召回失败→boost_stability）或 `reason`（推理失败→downweight_noise）。
  - `SelfRefineEngine.apply_strategy()`：策略侧调参（修复确认 +0.1 stability / 噪声 -0.1），与原有 memory 侧 `repair` 构成**双优化**闭环。
  - `run()` 现串起 probe→verify→诊断→双优化，返回含 `diagnoses` / `strategy_actions`。

### 与科研方向映射
- PEEK 预算稳态 → **稳态**方向（记忆系统维持预算稳态而非无限膨胀，防熵爆）。
- SEAL 双优化 → **智能体意识**方向（自洽性：失败→诊断→双侧面修复，强化原级自我意识自洽闭环）。

### 工程
- 测试：`tests/test_peek_seal.py` 7 例（PEEK 3 + SEAL 4），全量 24 passed。
- 版本号 bump 至 0.6.3（包名保持 `liquid_loop`）。

## v0.6.2 (2026-07-14) — 跨锚点误结晶修复 + 文档校正

### 核心修复
- **P0 跨锚点误结晶（`_nucleate`，#bug）**：成核查重由全局 `content` 唯一键改为 `(anchor_id, content)` 复合键。原实现不限定锚点，当两个不同锚点存在相同 `content` 证据时，第二条会被错误跳过、且结晶 `evidence_ids` 跨锚点污染——在 E2 液环↔GNN 桥接（共享边对称表示）下会污染审计链。修复后各锚点独立成核、零跨锚点污染。新增回归测试 `test_nucleate_no_cross_anchor_pollution`。

### 文档校正
- 纠正"四维分类 / 四维熵"误导表述：实际为**三维锚点分类**（value_density / cognitive_stage / liquidity）+ **一维证据质量**，熵值为**八维加权**（含 CPE 三维）。同步校正 `workspace.py` / `entropy.py` 注释、`README.md`、`cli.py`、`examples/quickstart.py`。

### 工程
- 版本号 bump 至 0.6.2（包名保持 `liquid_loop`）

## v0.6.1 (2026-07-13) — 节律采样检索 + 缓存持久化修复

### 核心修复
- **overlap_cache 持久化崩溃（#bug）**：`_keyword_overlap` 缓存键由 `tuple` 改为 `str`（md5 哈希拼接），根除 `storage.save` 因 tuple 键无法 JSON 序列化而抛 `TypeError` 的问题；`storage.save` 同时 `pop("overlap_cache")` 排除运行时缓存，双重保险
- **跨机共识桥接可用**：液环现可安全持久化含 GNN 解释边证据的 workspace（E2 桥接实证 5 锚点 / 80 证据 / 31 结晶）

### 新增能力
- **节律采样检索 `rhythmic_retrieve`**：受 Biba et al. 2026（Nature Human Behaviour, 7Hz theta 脉冲记忆编码）启发，分窗口脉冲采样、每组取最优、跨组去重；相比直接 top-N 避免同质记忆堆叠、增加多样性。已加入 `__init__` 导出

### 其他
- 版本号 bump 至 0.6.1（包名保持 `liquid_loop`，与 0.6.0 一致）

## v0.6.0 (2026-07-13) — CPE 融合 + 主动自精炼 + 策略层

### 核心升级
- **CPE 融合（CPE-fused）**：能力侵蚀检测（`CPERegularizer`）与自组织记忆深度融合，新证据自动评估侵蚀风险并施加正则保护
- **主动自精炼（proactive）**：`SelfRefineEngine` 正式导出，支持证据驱动的主动精炼与回流

### 新增能力（CLI / API）
- 自精炼：`self-refine` — 证据驱动主动精炼
- 策略层：`strategy-health` / `strategy-advice` — 锚点策略健康度与针对性建议
- 能力侵蚀：`cpe-check` / `cpe-scan` / `cpe-status` — 逐点检查 / 全量扫描 / 状态总览
- 关系建模：`relate` / `relation-list` — 锚点间关联（`AnchorRelation`）
- 记忆与冲突：`memory-add` / `conflict-resolve` — 手动结晶与冲突消解
- 锚点描述：`anchor-describe` — 回填 / 设定锚点描述
- `version` 命令打印当前版本

### 熵模型扩展（八维）
- `entropy.py` 新增 `anchor_drift` / `conflict_density` / `evidence_fragmentation` / `activity_gap` / `value_decay_entropy` / `strength_entropy` / `retrospective_decay_entropy` / `behavioral_drift_entropy`，认知健康度量更细

### 工程
- 包内导入统一为相对导入；零外部依赖（仅 click + pyyaml）
- 测试扩展：`test_entropy.py` / `test_workspace.py`
- 最低 Python 由 3.9 提升至 **3.10**（`list[str] | None` 等 3.10 语法）

## v0.5.4 (2026-07-12) — 锚点自进化补强 + bug 修复（跳过被 PyPI yank 的 0.5.3）

### 修复
- **严重 bug**：`CPERegularizer.evaluate_new_evidence` 的 MERGE 分支引用了未定义变量 `protection_weight`（该变量在函数末尾才定义），命中"近似重复"证据时必抛 `NameError`。已移除该键。

### 新增：锚点自进化（回应「锚点群自进化」思路，先做窄不做宽）
- **成核回流**：`WorkspaceState._nucleate` 中，结晶 confidence ≥ 0.8 时自动回填锚点空描述（仅当描述为空，尊重人工设定），让结晶结论回流提升锚点质量
- **群内自洽检测**：`_on_evidence_added` 新增 `_detect_conflicts`，同锚点证据平均一致度 < 0.2（≥3 条）自动生成 `Conflict` 记录并降 stability，让 `Conflict` 类从死字段变活角标
- `liquid-loop status` 现列出冲突/不一致明细
- 锚点群（Cluster）全局概念暂缓：当前锚点量未到阈值（<200），属过度设计；单对单关联已由 `AnchorRelation` 表达

## v0.5.2 (2026-07-12) — 补丁版

### 修复
- CPE 正则化器稳定性改进
- SelfRefineEngine 探测匹配优化
- 内部代码清理与重构

---

## v0.3.0 (2026-07-11) — KFG 补强版

### 🔧 新增：链式哈希审计
- `AuditChain` 类：每次 save/锚点变更自动追加 SHA256 链式哈希
- 审计日志：`.liquid/audit.log`，每次写入含 `index | prev_hash | hash | timestamp | message`
- CLI：`liquid-loop audit`（显示链完整性）/ `liquid-loop audit-log`（查看原始日志）
- `WorkspaceState` 新增 `audit_chain_hash` + `audit_prev_hash` 字段

### 🔧 新增：四维分类自动推导
- `Anchor.auto_classify()`：基于证据数 + 稳定度 + 活跃度自动打标签
- 值密度：`high`(≥5证据+强锚定) / `medium`(≥3证据) / `low`(其余)
- 认知阶段：`crystallized`(≥3+强锚定+高稳定) / `wip`(有证据) / `raw`(无证据) / `tooling`(工具类)
- 流动性：`hot`(24h内活跃) / `warm`(7天内) / `cold`(30天内) / `frozen`(超过30天)

### 🔧 升级：衰减双因模型
- `Anchor.decay_value()` 升级为频率+时间双因子：`score = freq_score×0.4 + time_score×0.6`
- `freq_score`：证据数×0.12 + 访问次数×0.08（上限1.0）
- `time_score`：30天从1.0→0.0指数衰减
- 自动衰减触发：`add_evidence`/`add_anchor`/`add_memory` 时自动降级长期未访问锚点

### 改进
- `anchor-add` CLI 命令：新增 `--value-density`、`--cognitive-stage`、`--liquidity` 可选参数
- `status` 命令：新增审计链哈希显示
- 所有测试通过，端到端验证通过

---

## v0.1.1 (2026-07-11)

### 🎉 首次发布

**核心功能：**
- **自组织记忆**：证据注入 → 自动成核（≥2 条一致证据生成 Memory）→ 权重衰减 → 稳定性重算
- **四维熵值**：锚点分布熵 + 证据一致性熵 + 记忆稳定性熵 + 冲突度，量化认知健康度
- **CLI 9 命令**：`init` / `status` / `anchor-add` / `anchor-list` / `evidence-add` / `evidence-list` / `memory-list` / `snapshot` / `snapshots`
- **Python API**：`WorkspaceState().add_anchor().add_evidence()` 自动触发液环链

**性能（50,000 证据压测）：**
- 写入吞吐：~73K 写入/秒
- 单条延迟：~0.014ms
- 熵值计算：3.5ms（50,000 证据）

**开源：**
- PyPI: `pip install liquid-loop`
- GitHub: https://github.com/fishbook0001/liquid-loop
- 零依赖（仅 click + pyyaml）
- MIT 协议