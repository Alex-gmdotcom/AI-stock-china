# -*- coding: utf-8 -*-
"""
probe_moneyflow.py — 个股主力资金冗余路径探针(只读, 不改任何生产代码)
================================================================
背景: 2026-07-15 A股批 stock_individual_fund_flow(东财) 13/13 全灭
     (RemoteDisconnected, 分端点反爬软掐; 该腿占裁决⑦后 capflow 55%)。
目的: 为定向冗余定谳两条候选路径, 数据说话后再动生产代码:
  路径一: tushare `moneyflow`(口径同源级别, 有积分门槛, 真机实测定谳)
  路径二: akshare 原链 + 请求间隔(验证"连发触发反爬"假设, 零新依赖)
运行: cd E:\\AI-tool\\Stock\\ai-hedge-fund
      poetry run python probe_moneyflow.py
预计耗时 ~1 分钟(路径二含刻意间隔)。
"""
import sys
import time

sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv
    load_dotenv()                      # 入口点纪律(ENTRYPOINT_DOTENV_V1 同族)
except Exception:
    pass

PROBE_TICKERS = ["600519", "300308", "000333"]   # 沪/创/深各一
RESULTS = []


def log(path, ok, detail):
    RESULTS.append((path, ok, detail))
    print(f"[{'OK' if ok else 'XX'}] {path}: {detail}")


# ── 路径一: tushare moneyflow ──────────────────────────────────
def probe_tushare():
    import os
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("AIHF_TUSHARE_TOKEN")
    if not token:
        log("tushare.moneyflow", False, "token 未注入(.env 键名?)")
        return
    try:
        import tushare as ts
        pro = ts.pro_api(token)
    except Exception as exc:
        log("tushare.moneyflow", False, f"初始化失败: {str(exc)[:100]}")
        return
    try:
        df = pro.moneyflow(ts_code="600519.SH",
                           start_date="20260701", end_date="20260715")
        if df is None or df.empty:
            log("tushare.moneyflow", False, "返回空表(可能积分不足或参数不符)")
        else:
            cols = [c for c in df.columns if "lg" in c or "elg" in c or "net" in c]
            log("tushare.moneyflow", True,
                f"{len(df)} 行; 大单/特大单字段: {cols[:6]}")
            print(df.head(3).to_string())
    except Exception as exc:
        msg = str(exc)[:160]
        hint = "(积分门槛)" if "积分" in msg or "权限" in msg or "point" in msg.lower() else ""
        log("tushare.moneyflow", False, f"调用异常{hint}: {msg}")


# ── 路径二: akshare fund_flow + 间隔(反爬连发假设) ─────────────
def probe_akshare_spaced(gap_s=4.0):
    try:
        import akshare as ak
    except Exception as exc:
        log("akshare.spaced", False, f"akshare 导入失败: {str(exc)[:100]}")
        return
    ok_n = 0
    for i, t in enumerate(PROBE_TICKERS):
        if i:
            time.sleep(gap_s)          # 刻意间隔: 验证连发才是死因
        market = "sh" if t.startswith(("6", "9")) else "sz"
        try:
            df = ak.stock_individual_fund_flow(stock=t, market=market)
            ok = df is not None and not df.empty
            ok_n += ok
            print(f"    {t}: {'OK ' + str(len(df)) + ' 行' if ok else '空表'}")
        except Exception as exc:
            print(f"    {t}: 失败 {str(exc)[:90]}")
    log("akshare.spaced", ok_n == len(PROBE_TICKERS),
        f"{ok_n}/{len(PROBE_TICKERS)} 成功 @ 间隔 {gap_s}s"
        + (" -> 连发假设成立, 生产侧加间隔即可" if ok_n == len(PROBE_TICKERS) else
           " -> 非单纯连发问题" if ok_n == 0 else " -> 部分通过, 需更长间隔或重试"))


if __name__ == "__main__":
    print("== 路径一: tushare moneyflow ==")
    probe_tushare()
    print("\n== 路径二: akshare 间隔请求(约 %d 秒) ==" % (4 * (len(PROBE_TICKERS) - 1)))
    probe_akshare_spaced()
    print("\n== 探针结论 ==")
    for p, ok, d in RESULTS:
        print(f"  {'✓' if ok else '✗'} {p} — {d}")
    print("把整段输出贴回来定谳冗余方案; 本探针未改动任何生产文件。")
