#!/bin/bash
# 8790 记忆服务本地 wrapper（launchd 只解析本地已授权路径，绕开 ~/Projects symlink 与外挂盘开机时序）
# 盘未挂载时 exec 失败，KeepAlive 重试；盘挂载后下次重试自动成功。
export PYTHONPATH=/Users/feixubuke/liquid-loop
export LIQUID_CONTEXT_COMPRESS_TOKENS=4000
export LIQUID_LEXICAL_RERANK=1

TARGET=/Volumes/Fishbook/Projects/marvis_memory/run_liquidloop.sh
# 等待外挂盘就绪（最多 90s），避免开机盘未挂载即失败
for _ in $(seq 1 90); do
    [ -x "$TARGET" ] && break
    sleep 1
done
exec /bin/bash "$TARGET"
