#!/bin/bash
# 液环 5insights API 隔离启动器（对齐 8790 run_liquidloop.sh 范式）
#
# 设计目的：解释器绝对化 + ProgramArguments 路径恒定，彻底绕开 launchd 缓存旧
#           plist 定义导致的 EIO5 故障链。launchd 永远只 exec 本脚本（路径不变），
#           解释器/参数切换只改本文件内部，不触发 launchd 重读缓存。
#
# ⚠️ 解释器硬约束（2026-09-03 定因）：核心库 liquid_loop/storage.py:61 使用
#    PEP 604 语法 `_ARCHIVE_IDS: set | None = None`，需要 Python >= 3.10。
#    用系统 3.9.6 启动会直接 TypeError，服务秒退 — 这正是 8791 长期「假在线」
#    （文档标 ✅ 运行中，实测 http_code=000）的真因。此处锁 3.14.5，
#    与 8790 wrapper 保持同版本，避免双解释器行为分叉。
#
# env 自包含：launchd 托管时由 plist EnvironmentVariables 注入（此处覆盖同值，
#   无害）；手动起（nohup 绕过 launchd）时此处提供完整环境，两种启动方式都完整。

export PYTHONPATH=/Users/feixubuke/liquid-loop
export PYTHONUNBUFFERED=1

exec /opt/homebrew/bin/python3.14 \
    /Users/feixubuke/liquid-loop/liquid_loop_5insights_api.py \
    --host 127.0.0.1 --port 8791
