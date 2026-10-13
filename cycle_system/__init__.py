# -*- coding: utf-8 -*-
"""周期股量化交易系统核心包。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIBS = os.path.join(_ROOT, ".libs")
if os.path.isdir(_LIBS) and _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)

__all__ = ["universe", "data", "factors", "cycle_engine", "analytics", "strategy", "backtest", "report"]
