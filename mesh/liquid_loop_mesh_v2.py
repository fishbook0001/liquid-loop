#!/usr/bin/env python3
"""
liquid_loop_mesh_v2.py — 兼容薄壳（向后兼容入口）
==================================================
液环 MESH v2 的正式实现已迁入 `liquid_loop.mesh.v2`（随 pip 包发布）。
本文件保留为兼容入口：运行 `python3 mesh/liquid_loop_mesh_v2.py` 等价于
`python3 -m liquid_loop.mesh.v2`，连接 8790 打印认知健康仪表盘。
"""
import os
import sys

# 允许以脚本方式直接运行：把仓库根加入 sys.path 以便 import liquid_loop
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquid_loop.mesh.v2 import main

if __name__ == "__main__":
    main()
