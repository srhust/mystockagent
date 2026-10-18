# -*- coding: utf-8 -*-
"""_path — 兼容旧非包模块的 sys.path 引导(仅插仓库根与 cycle_system 目录)。

关键陷阱(实测): 旧模块内部 `import config` 解析到**仓库根** config.py(30板块CYCLE_SECTORS)。
绝不可把 ashare_v2 目录本身插入 sys.path——本包 config.py 会遮蔽根 config。
本模块只做两件事: 保证 ROOT 在 path(包导入), 保证 cycle_system 在 path(旧模块互引)。
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_CYC = os.path.join(_ROOT, "cycle_system")
for p in (_ROOT, _CYC):
    if p not in sys.path:
        sys.path.insert(0, p)
ROOT = _ROOT
