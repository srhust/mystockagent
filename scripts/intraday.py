# -*- coding: utf-8 -*-
"""盘中实时周期温度计（同花顺行业实时汇总 + 本地富化缓存合成当日行）。

数据来源:
- 本地富化缓存 data/enriched.pkl（只读, 绝不写回）: 截至最后交易日的日线与因子
- akshare.stock_board_industry_summary_ths(): 同花顺行业板块盘中实时汇总（唯一联网调用）

算法: 对每个周期板块, 用实时涨跌幅把缓存最后收盘价换算成盘中点位
    close_syn = 缓存最后收盘 × (1 + 涨跌幅/100),
实时总成交额(亿元→×1e8=元)/总成交量(万手→×1e6=股)按"日内成交量分布曲线"折算成
全日估计后合成当日行情行, 重算 ma/momentum/price_pos/vol_surge 因子
（pb/pb_pct/breadth/new_high 沿用最后缓存值）, 再用 cycle_engine.compute_temperature
重算当日周期温度(0-100)。

安全防护:
- 休市/盘前去重: 当 summary 涨跌幅 ≈ 缓存最后一日的实际涨幅(≤0.05%)时, 判定 summary
  描述的就是缓存里已有的交易日（周末/节假日/盘前/缓存已刷新）, 直接展示收盘温度,
  绝不合成, 避免把同一交易日的涨跌重复计入。
- 量纲自检: 均价×量 vs 成交额 恒等式校验 + vol_surge 量级检查, 异常自动回退/报错。

导入顺序硬约束: 必须先 import cycle_system.data（设 NO_PROXY + 30s 超时补丁）
再 import akshare, 否则可能挂在本机已失效的系统代理上。

用法:
    $env:PYTHONIOENCODING='utf-8'; python scripts/intraday.py            # 一次性扫描
    python scripts/intraday.py --watch --interval 300                    # 循环监控(Ctrl+C退出)
    python scripts/intraday.py --no-vol-est                              # 量能不做日内折算(调试)
    python scripts/intraday.py --vol-table                               # 打印量纲校验明细表

退出码: 0 = 全部板块匹配且自检通过; 非 0 = 接口失败/匹配缺失/计算异常/量纲异常。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _path  # noqa: F401  (加入 .libs)

from cycle_system import data  # noqa: F401,E402  必须在 import akshare 之前（NO_PROXY + 超时）
import akshare as ak  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from cycle_system import factors  # noqa: E402
from cycle_system.cycle_engine import STAGE_CN, compute_temperature  # noqa: E402

PKL = os.path.join(config.DATA_DIR, "enriched.pkl")

# ---------------- 自检阈值 ----------------
VOL_SURGE_NORMAL = (0.05, 5.0)    # 量纲正常区间
VOL_SURGE_FATAL = (0.01, 100.0)   # 1e-3 / 1e3 量级 = 单位换算必错, 必须停下排查
UNIT_RATIO_TOL = 0.30             # 均价×量 vs 成交额 比值偏离 ±30% 告警
EOD_RET_TOL = 5e-4                # summary涨跌幅与缓存末日收益差 ≤0.05% → 同一会话

# 日内累计成交量分布锚点（小时为小数; A股 09:30-11:30 / 13:00-15:00）。
# 经验分布: 早盘密集、午前递增、午后放量。误差对温度影响约±1.5（量能权重仅10%）。
_VOL_ANCHORS = [(9.5, 0.00), (10.0, 0.20), (11.0, 0.40), (11.5, 0.55),
                (13.0, 0.60), (14.0, 0.80), (15.0, 1.00)]


def _num(v):
    """防御性数值转换, 失败返回 NaN。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def die(msg):
    print(f"\n[错误] {msg}")
    sys.exit(1)


def fetch_summary():
    """拉取同花顺行业板块盘中实时汇总, 失败即报错退出(非0)。"""
    try:
        df = ak.stock_board_industry_summary_ths()
    except Exception as e:  # noqa: BLE001
        die(f"akshare.stock_board_industry_summary_ths 调用失败: {type(e).__name__}: {e}")
    if df is None or len(df) == 0:
        die("实时接口返回空数据")
    need = {"板块", "涨跌幅", "总成交量", "总成交额", "均价"}
    miss = need - set(df.columns)
    if miss:
        die(f"实时接口缺少预期列 {sorted(miss)}; 实际列: {list(df.columns)}")
    return df


def session_state(now=None):
    """返回 (状态, 日内量能进度)。

    状态: 'in'=盘中(进度0-1) / 'pre'=盘前 / 'post'=已收盘(今日全日值) / 'off'=非交易日。
    法定节假日无法本地判断, 由"休市去重"逻辑兜底。
    """
    now = now or pd.Timestamp.now()
    if now.weekday() >= 5:
        return "off", None
    h = now.hour + now.minute / 60.0
    if h < 9.5:
        return "pre", None
    if h >= 15.0:
        return "post", 1.0
    for (t0, p0), (t1, p1) in zip(_VOL_ANCHORS, _VOL_ANCHORS[1:]):
        if t0 <= h <= t1:
            return "in", p0 + (p1 - p0) * (h - t0) / (t1 - t0)
    return "in", 0.0


def _fmt_pct(x):
    return f"{x * 100:.0f}%" if np.isfinite(x) else "-"


def _fmt_int(x):
    return f"{x:.0f}" if np.isfinite(x) else "-"


def scan_once(dfs, meta, today, vol_est=True, vol_table=False):
    """执行一轮扫描。返回退出码。"""
    state, prog = session_state()
    hint = {"in": f"盘中实时（日内量能进度≈{prog * 100:.0f}%）",
            "pre": "盘前——summary 为上一交易日收盘数据",
            "post": "已收盘——summary 为今日收盘全日值",
            "off": "非交易日——summary 为上一交易日收盘数据"}[state]

    print("拉取同花顺行业板块盘中实时汇总 ...")
    summary = fetch_summary()
    smap = {str(b).strip(): r for b, r in summary.set_index("板块").iterrows()}
    print(f"实时接口返回 {len(summary)} 个行业板块, 本系统覆盖 {len(config.CYCLE_SECTORS)} 个周期板块")

    rows, ratios, failures, unmatched, warnings = [], [], [], [], []
    eod_cnt, fatal_unit = 0, []

    for name in config.CYCLE_SECTORS:
        m = config.CYCLE_SECTORS[name]
        if name not in dfs:
            failures.append((name, "本地缓存缺该板块"))
            continue
        df = dfs[name]
        try:
            if len(df) < 60 or "temp" not in df.columns:
                failures.append((name, "缓存序列过短或缺温度列"))
                continue
            ths = str(m["ths"]).strip()
            if ths not in smap:
                unmatched.append((name, ths))
                continue
            sr = smap[ths]
            pct = _num(sr["涨跌幅"])
            amt_yi = _num(sr["总成交额"])
            vol_wan = _num(sr["总成交量"])
            avg_px = _num(sr["均价"])
            if np.isnan(pct) or np.isnan(amt_yi):
                failures.append((name, f"实时数值解析失败(涨跌幅={sr['涨跌幅']!r})"))
                continue

            # ---- 量纲校验证据: 均价(元/股) × 总成交量(万手×1e6=股) ≈ 总成交额(亿元×1e8=元) ----
            ratio = np.nan
            if np.isfinite(avg_px) and np.isfinite(vol_wan) and amt_yi > 0:
                ratio = avg_px * (vol_wan * 1e6) / (amt_yi * 1e8)
                ratios.append((name, avg_px, vol_wan, amt_yi, ratio))

            last_close = float(df["close"].iloc[-1])
            last_temp = float(df["temp"].iloc[-1])
            last_stage = STAGE_CN.get(str(df["stage"].iloc[-1]), "-")
            last_pbp = _num(df["pb_pct"].iloc[-1]) if "pb_pct" in df.columns else np.nan
            last_vs = _num(df["vol_surge"].iloc[-1]) if "vol_surge" in df.columns else np.nan
            prev_temp = float(df["temp"].iloc[-2]) if len(df) >= 2 else np.nan

            # ---- 休市/盘前/已刷新 去重: summary 描述的就是缓存已有的交易日 → 只展示收盘态 ----
            eod_reason = None
            if today <= df.index[-1]:
                eod_reason = "缓存已含今日"
            elif len(df) >= 2:
                last_ret = last_close / float(df["close"].iloc[-2]) - 1.0
                if abs(pct / 100.0 - last_ret) <= EOD_RET_TOL:
                    eod_reason = "汇总=缓存会话"
            if eod_reason:
                rows.append({
                    "板块": name, "类别": m.get("cat", ""),
                    "温度": last_temp, "昨日": prev_temp, "涨跌幅%": round(pct, 2),
                    "阶段": last_stage, "PB分位": _fmt_pct(last_pbp),
                    "vol_surge": round(last_vs, 3) if np.isfinite(last_vs) else "-",
                    "提示": f"[收盘:{eod_reason}]",
                    "合成": False,
                })
                eod_cnt += 1
                continue

            # ---- 盘中合成 ----
            close_syn = last_close * (1.0 + pct / 100.0)
            amount_syn = amt_yi * 1e8          # 亿元 → 元
            volume_syn = vol_wan * 1e6 if np.isfinite(vol_wan) else np.nan  # 万手 → 股
            vol_carry = False
            if vol_est and state == "in" and prog is not None and prog >= 0.05:
                # 盘中半日量按日内分布曲线折算成全日估计（否则 vol_surge 低估 2~4 倍）
                amount_syn /= prog
                if np.isfinite(volume_syn):
                    volume_syn /= prog
            if np.isfinite(ratio) and abs(ratio - 1.0) > UNIT_RATIO_TOL:
                # 量纲校验不过 → 量能维度沿用缓存（价格维度不受影响）
                amount_syn, volume_syn = np.nan, np.nan
                vol_carry = True

            df2 = df.copy()
            df2.loc[today, ["open", "high", "low", "close"]] = close_syn
            df2.loc[today, "amount"] = amount_syn
            df2.loc[today, "volume"] = volume_syn

            factors.add_ma(df2)
            factors.add_momentum(df2)
            factors.add_price_position(df2, window=config.TEMPERATURE["price_window"])
            factors.add_volume_surge(df2, window=config.TEMPERATURE["vol_window"])
            # 估值与广度盘中无法计算, 沿用最后缓存值
            # (个别板块缺 breadth/new_high 列: 富化时个股样本不足未生成,
            #  compute_temperature 对缺失维度自动权重重归一化, 只 ffill 存在的列)
            carry = [c for c in ("pb", "pb_pct", "breadth", "new_high") if c in df2.columns]
            df2[carry] = df2[carry].ffill()

            temp = float(compute_temperature(df2).iloc[-1])
            if not (0.0 <= temp <= 100.0):
                failures.append((name, f"温度越界: {temp:.2f}"))
                continue

            vs = _num(df2["vol_surge"].iloc[-1])
            if vol_carry or np.isnan(vs):
                vs = last_vs  # 展示沿用值; 温度计算时该维度已自动缺失重归一化
            elif vs < VOL_SURGE_FATAL[0] or vs > VOL_SURGE_FATAL[1]:
                fatal_unit.append((name, vs))
                continue
            elif vs < VOL_SURGE_NORMAL[0] or vs > VOL_SURGE_NORMAL[1]:
                warnings.append((name, vs))
            pbp = _num(df2["pb_pct"].iloc[-1]) if "pb_pct" in df2.columns else last_pbp

            delta = temp - last_temp
            flags = []
            if temp < 12:
                flags.append("深寒买入区")
            if temp > 75:
                flags.append("过热预警")
            if np.isfinite(delta):
                if delta <= -5:
                    flags.append("骤冷")
                elif delta >= 5:
                    flags.append("骤热")
            rows.append({
                "板块": name, "类别": m.get("cat", ""),
                "温度": temp, "昨日": last_temp, "涨跌幅%": round(pct, 2),
                "阶段": last_stage, "PB分位": _fmt_pct(pbp),
                "vol_surge": round(vs, 3) if np.isfinite(vs) else "-",
                "提示": "/".join(flags),
                "合成": True,
            })
        except Exception as e:  # noqa: BLE001
            failures.append((name, f"{type(e).__name__}: {e}"))
            print(f"[警告] {name} 计算失败: {type(e).__name__}: {e}")

    # ---- 量纲校验 ----
    if ratios:
        rs = np.array([r[-1] for r in ratios])
        devs = np.abs(rs - 1.0)
        worst = ratios[int(np.argmax(devs))]
        bad = [r for r in ratios if abs(r[-1] - 1.0) > UNIT_RATIO_TOL]
        print(f"[量能] 万手/亿元→股/元 换算校验: 比值中位数 {np.median(rs):.4f}, "
              f"最差偏离 {devs.max() * 100:.2f}%（{worst[0]}）"
              + (f", {len(bad)} 个越限已回退沿用缓存量能" if bad else ", 全部通过"))
        if vol_table:
            print(f"{'板块':<8}{'均价':>10}{'总成交量(万手)':>16}{'总成交额(亿元)':>16}{'比值':>10}")
            for nm, apx, vw, ay, rt in ratios:
                print(f"{nm:<8}{apx:>10.2f}{vw:>16.2f}{ay:>16.2f}{rt:>10.4f}")
    else:
        print("[量能] 无可校验样本")

    if fatal_unit:
        print("\n[错误] vol_surge 呈 1e-3/1e3 量级, 单位换算必错, 停下排查:")
        for nm, vs in fatal_unit:
            print(f"       {nm}: vol_surge = {vs}")
        return 1

    # ---- 主表 ----
    now_str = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 100)
    print(f"盘中周期温度 | {now_str} | {hint} | 缓存数据截至 "
          f"{max(df.index[-1] for df in dfs.values()).date()}")
    print("=" * 100)
    if not rows:
        print("（无任何板块成功计算温度）")
        return 1

    t = pd.DataFrame(rows).sort_values("温度", ascending=False)
    disp = pd.DataFrame({
        "板块": t["板块"],
        "温度": t["温度"].map(_fmt_int),
        "昨日": t["昨日"].map(_fmt_int),
        "Δ": (t["温度"] - t["昨日"]).map(lambda d: f"{d:+.1f}" if np.isfinite(d) else "-"),
        "涨跌幅%": t["涨跌幅%"],
        "阶段": t["阶段"],
        "PB分位": t["PB分位"],
        "vol": t["vol_surge"],
        "提示": t["提示"],
    })
    print(disp.to_string(index=False))
    print(f"\n温度范围: {t['温度'].min():.1f} ~ {t['温度'].max():.1f}"
          + (f" | 收盘态 {eod_cnt} 个（休市/盘前/已刷新, 未合成）" if eod_cnt else ""))

    # ---- 名单（含收盘态——休市时即最新收盘状态的观察名单） ----
    cold = t[t["温度"] < 12]
    lukewarm = t[(t["温度"] >= 12) & (t["温度"] < 20)]
    hot = t[t["温度"] > 75]
    shock = t[t["提示"].str.contains("骤")]
    if len(cold):
        print("\n>>> 深寒买入区（温度<12, 实证120日胜率67-84%）:")
        for _, r in cold.iterrows():
            print(f"    {r['板块']:<8} 温度{r['温度']:>5.1f}  PB分位{r['PB分位']:>5}  {r['阶段']}")
    if len(lukewarm):
        print(">>> 冰点观察区（12-20）: " + "、".join(lukewarm["板块"]))
    if len(hot):
        print(">>> 过热预警（>75, 逢高分批兑现）:")
        for _, r in hot.iterrows():
            print(f"    {r['板块']:<8} 温度{r['温度']:>5.1f}  PB分位{r['PB分位']:>5}  {r['阶段']}")
    if len(shock):
        print(">>> 盘中骤变（|Δ|≥5）:")
        for _, r in shock.iterrows():
            print(f"    {r['板块']:<8} Δ{(r['温度'] - r['昨日']):+.1f}  {r['提示']}")
    if not (len(cold) or len(hot) or len(shock)):
        print("\n>>> 无深寒/过热/骤变触发")

    if unmatched:
        print("\n[警告] 以下板块未在实时接口匹配到:")
        for nm, ths in unmatched:
            print(f"       {nm} -> '{ths}'")
    if failures:
        print("[警告] 以下板块计算失败:")
        for nm, why in failures:
            print(f"       {nm}: {why}")
    if warnings:
        print(f"[提示] {len(warnings)} 个板块 vol_surge 超出 0.05-5 区间（非致命）")

    ok = (not unmatched) and (not failures)
    print(f"\n[结果] 成功 {len(rows)}/{len(config.CYCLE_SECTORS)}"
          + ("，自检通过" if ok else "，存在告警"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="盘中实时周期温度监控")
    ap.add_argument("--watch", action="store_true", help="循环监控（Ctrl+C 退出）")
    ap.add_argument("--interval", type=float, default=300, help="循环间隔秒数（默认300）")
    ap.add_argument("--no-vol-est", action="store_true",
                    help="量能不做日内折算（调试; 盘中会把 vol_surge 低估2~4倍）")
    ap.add_argument("--vol-table", action="store_true", help="打印量纲校验明细表")
    args = ap.parse_args()

    if not os.path.exists(PKL):
        die(f"富化缓存不存在: {PKL} — 请先运行 python main.py 生成")
    obj = pd.read_pickle(PKL)
    dfs, meta = obj["dfs"], obj["meta"]
    today = pd.Timestamp.now().normalize()

    if not args.watch:
        sys.exit(scan_once(dfs, meta, today, vol_est=not args.no_vol_est,
                           vol_table=args.vol_table))
    try:
        while True:
            code = scan_once(dfs, meta, today, vol_est=not args.no_vol_est,
                             vol_table=args.vol_table)
            nxt = pd.Timestamp.now() + pd.Timedelta(seconds=args.interval)
            print(f"\n[watch] 下一轮 {nxt.strftime('%H:%M:%S')}（Ctrl+C 退出）| 上轮退出码 {code}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[watch] 已停止")
        sys.exit(0)


if __name__ == "__main__":
    main()
