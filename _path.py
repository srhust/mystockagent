# -*- coding: utf-8 -*-
"""把工作区内 .libs（pip --target 安装的依赖）加入 sys.path。"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
LIBS = os.path.join(ROOT, ".libs")
if os.path.isdir(LIBS) and LIBS not in sys.path:
    sys.path.insert(0, LIBS)
