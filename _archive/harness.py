"""
src/eval/harness.py — outcome 评估编排 + CLI
======================================================================
把 signals + 价格 → 前瞻收益面板 → metrics 汇总。

数据流：
  load_signals(path)
    → 对每条信号，取 entry_close(信号日) 和 exit_close(信号日+h交易日)
    → forward_return，减基准 → excess_return
    → 拼成 panel(date,ticker,score,fwd_ret,excess_ret,horizon,source,class)
    → metrics.summary（分 horizon / 可分 class / 分 source）

build_panel 与 IO 解耦：传入任意 PriceSource（真 baostock 或 mock），
所以核心编排逻辑也能在沙箱里验证。

CLI（国内机器）：
  poetry run python -m src.eval.harness --signals output/eval/signals_20260701.json \
      --horizons 5,10,20 --out output/eval/
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from . import metrics
from .data import (BaostockPriceSource, baostock_session,
                   nth_trading_day_on_or_after)
from .signals import Signal, load_signals


# ----------------------------------------------------------------------
# 核心：信号 + 价格 → 面板（与数据源解耦，可 mock）
# ----------------------------------------------------------------------
def build_panel(
    signals: list[Signal],
    closes_by_ticker: dict[str, pd.Series],
    bench_closes: pd.Series,
    trade_dates: list[str],
    horizons: list[int],
) -> pd.DataFrame:
    """closes_by_ticker: {ticker: Series(index=YYYY-MM-DD, close)}。
    bench_closes: 沪深300 收盘 Series。trade_dates: 排序好的交易日列表。"""
    def close_on(series: pd.Series, date: str):
        return float(series[date]) if date in series.index else None

    recs = []
    for sig in signals:
        px = closes_by_ticker.get(sig.ticker)
        if px is None or sig.date not in px.index:
            continue  # 缺价（如港股/停牌），跳过，不臆造
        entry = close_on(px, sig.date)
        bench_entry = close_on(bench_closes, sig.date)
        for h in horizons:
            exit_date = nth_trading_day_on_or_after(sig.date, h, trade_dates)
            if exit_date is None:
                fwd = float("nan")     # 日历不够长：向前累积后重跑
                exc = float("nan")
            else:
                fwd = metrics.forward_return(entry, close_on(px, exit_date))
                bench_fwd = metrics.forward_return(bench_entry, close_on(bench_closes, exit_date))
                exc = metrics.excess_return(fwd, bench_fwd)
            recs.append({
                "date": sig.date, "ticker": sig.ticker, "score": sig.score,
                "horizon": h, "fwd_ret": fwd, "excess_ret": exc,
                "source": sig.source, "stock_class": sig.stock_class,
                "direction": sig.direction, "confidence": sig.confidence,
            })
    return pd.DataFrame(recs)


def evaluate(panel: pd.DataFrame, horizons: list[int]) -> dict:
    """按 horizon（并可分 class）汇总指标。"""
    report = {"by_horizon": {}}
    for h in horizons:
        sub = panel[panel["horizon"] == h]
        if sub.empty:
            continue
        report["by_horizon"][h] = {
            "overall": metrics.summary(sub),
            "by_class": {
                cls: metrics.summary(g)
                for cls, g in sub.groupby("stock_class") if cls
            },
        }
    return report


# ----------------------------------------------------------------------
# IO 编排（国内机器：真 baostock）
# ----------------------------------------------------------------------
def _date_span(signals: list[Signal], max_h: int) -> tuple[str, str]:
    dates = sorted(s.date for s in signals)
    start = dates[0]
    # 结束日多留 max_h 个交易日的自然日缓冲（*2 冗余覆盖周末/假期）
    end = (pd.to_datetime(dates[-1]) + pd.Timedelta(days=max_h * 2 + 15)).strftime("%Y-%m-%d")
    return start, end


def run(signals_path: str, horizons: list[int], out_dir: str) -> dict:
    # 支持逗号分隔多个文件（如 run_Ashare.log,run_HK.log）
    paths = [p.strip() for p in str(signals_path).split(",") if p.strip()]
    signals = []
    for p in paths:
        signals.extend(load_signals(p))
    a_shares = [s for s in signals if not s.ticker.upper().endswith(".HK")]
    hk = [s for s in signals if s.ticker.upper().endswith(".HK")]
    if hk:
        print(f"⚠️ 跳过 {len(hk)} 只港股（baostock 取不到，需 Sina/腾讯 fallback）: "
              f"{sorted({s.ticker for s in hk})}")

    start, end = _date_span(a_shares, max(horizons))
    tickers = sorted({s.ticker for s in a_shares})

    with baostock_session() as bs:
        src = BaostockPriceSource(bs)
        closes = {t: src.get_closes(t, start, end) for t in tickers}
        bench = src.get_benchmark_closes(start, end)
        trade_dates = src.get_trade_dates(start, end)

    panel = build_panel(a_shares, closes, bench, trade_dates, horizons)
    report = evaluate(panel, horizons)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out / "panel.csv", index=False, encoding="utf-8-sig")
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    _print_report(report, len(a_shares), len(trade_dates), horizons)
    print(f"\n📄 明细: {out/'panel.csv'}  报告: {out/'report.json'}")
    return report


def _print_report(report, n_sig, n_dates, horizons):
    print("\n" + "=" * 60)
    print(f"outcome 评估  |  A股信号 {n_sig} 条  |  日历覆盖 {n_dates} 交易日")
    print("=" * 60)
    n_signal_dates = report["by_horizon"].get(horizons[0], {}).get("overall", {}).get("n_dates", "?")
    if n_signal_dates == 1:
        print("⚠️ 只有单个信号日期截面 → IC/ICIR 统计上无意义（n≈15、ICIR 需≥2日）。")
        print("   现阶段只有前瞻收益/命中率可初步参考；IC 要向前累积 2-4 周多次批跑。\n")
    for h, blk in report["by_horizon"].items():
        o = blk["overall"]
        print(f"[H={h}交易日] RankIC均 {o['rank_ic_mean']:.3f} | ICIR {o['rank_icir']} | "
              f"命中率 {o['hit_rate']} (方向样本 {o['directional_n']}) | "
              f"超额命中 {o.get('excess_hit_rate','-')}")
        for cls, s in blk["by_class"].items():
            print(f"    {cls}类: 命中 {s['hit_rate']} (n={s['directional_n']}) "
                  f"RankIC {s['rank_ic_mean']:.3f}")


def main():
    ap = argparse.ArgumentParser(description="AI Hedge Fund 中国版 — outcome 评估 harness")
    ap.add_argument("--signals", required=True,
                    help="信号文件，支持 .json/.csv/.log；逗号分隔多个（如 run_Ashare.log,run_HK.log）")
    ap.add_argument("--horizons", default="5,10,20", help="前瞻交易日，逗号分隔（默认1/2/4周）")
    ap.add_argument("--out", default="output/eval/", help="输出目录")
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]
    run(args.signals, horizons, args.out)


if __name__ == "__main__":
    main()
