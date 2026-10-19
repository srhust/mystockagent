# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "cycle_system")
import ashare_stock_pipeline as p
r = p.fetch_one("600000", start="20261015", adjust="")
print("PROBE_OK", len(r))
