"""
liquid_loop.mesh — 液环 MESH 进阶版（v2）共识协议集成
=====================================================
把"多智能体共识协议规范"从文档落地为可复用代码，作为 liquid-loop 的官方
MESH 集成层。零向量哲学：一致性判定走结构化精确相等 + 审计链哈希，绝不引入
任何 embedding / 相似度。

职责（详见 .v2）：
  - validate_evidence()    agent 侧契约自检（结构化证据 schema）
  - check_contract()       双向契约合规检查（身份 / 写 / 消费 / 冲突）
  - compute_cci()          主体间性共识指数（记忆层）
  - cognitive_health()     统一认知健康仪表盘（CCI / drift / H_e / P_mem）
  - detect_conflict()      同锚点冲突证据检测
  - fetch_state()          从 8790 REST /list 拉取当前状态
  - main()                 连接 8790 打印认知健康报告（CLI）

快速使用：
    from liquid_loop.mesh import validate_evidence, compute_cci, cognitive_health
    from liquid_loop.mesh import fetch_state, main

    ok, errs = validate_evidence({"agent_id": "vera", "content": "..."})
    health = cognitive_health(fetch_state())
"""
from .v2 import (
    REGISTRY,
    CLAIM_TYPES,
    SCOPES,
    Evidence,
    validate_evidence,
    check_contract,
    compute_cci,
    cognitive_health,
    detect_conflict,
    fetch_state,
    main,
)

__all__ = [
    "REGISTRY",
    "CLAIM_TYPES",
    "SCOPES",
    "Evidence",
    "validate_evidence",
    "check_contract",
    "compute_cci",
    "cognitive_health",
    "detect_conflict",
    "fetch_state",
    "main",
]
