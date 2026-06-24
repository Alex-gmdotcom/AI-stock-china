#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
baostock_probe.py  —  Baostock 数据源一致性探测（2026-06-21）

目的：在重建数据层之前，先确认 Baostock 在你这台机器上：
  1. 能否登录连通
  2. 日线 K线 + 估值字段(PE/PB/PS) 能否取到、返回什么列
  3. 季度财报六张表(利润/资产负债/现金流/成长/运营/杜邦) 能否取到、字段名是什么
  4. 次新股(605788)会不会有问题
  5. 港股的缺口确认(Baostock 仅 A 股)

这个脚本【完全独立】，不导入项目任何代码，不改动任何文件。
只读探测，跑完写一份 baostock_probe_report.json，把它贴回来即可。

────────────────────────────────────────────────────────
【从零运行步骤】（在项目根目录 E:\\AI-tool\\Stock\\ai-hedge-fund 下）

  1) 安装 baostock：
       poetry run pip install baostock

  2) 把本文件放到项目根目录，运行：
       poetry run python baostock_probe.py

  3) 运行完会在同目录生成 baostock_probe_report.json
     —— 把这个文件（或终端输出）整个贴回新会话给我。
────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timedelta

REPORT = {
    "probe_version": "2026-06-21",
    "time": datetime.now().isoformat(),
    "python": sys.version.split()[0],
    "baostock_login": None,
    "prices": {},
    "financials": {},
    "notes": [],
}

# 测试标的：大盘主流 / 深市 / 次新(之前 'date' 崩的那只)
PRICE_CODES = [
    ("sh.600519", "贵州茅台-沪市主流"),
    ("sz.000333", "美的集团-深市"),
    ("sh.605788", "次新股(之前出问题的)"),
]
FIN_CODE = ("sh.600519", "贵州茅台")
# 从近到远找最近一个有数据的季度
FIN_QUARTERS = [(2026, 1), (2025, 4), (2025, 3), (2025, 2), (2025, 1)]

PRICE_FIELDS = ("date,open,high,low,close,preclose,volume,amount,turn,"
                "pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM")


def _collect(rs, limit=None):
    """把 baostock ResultData 收成 (fields, rows)。"""
    fields = list(getattr(rs, "fields", []) or [])
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
        if limit and len(rows) >= limit:
            # 仍要把游标走完？这里只取前 limit 行作样本，够看格式
            pass
    return fields, rows


def probe_prices(bs):
    end = datetime.now()
    start = end - timedelta(days=45)
    s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    for code, label in PRICE_CODES:
        entry = {"label": label, "ok": False}
        try:
            rs = bs.query_history_k_data_plus(
                code, PRICE_FIELDS, start_date=s, end_date=e,
                frequency="d", adjustflag="2")  # 2=前复权
            if rs.error_code != "0":
                entry["error"] = f"error_code={rs.error_code} {rs.error_msg}"
            else:
                fields, rows = _collect(rs)
                entry["ok"] = len(rows) > 0
                entry["fields"] = fields
                entry["row_count"] = len(rows)
                entry["first_row"] = rows[0] if rows else None
                entry["last_row"] = rows[-1] if rows else None
                # 估值字段是否有非空值
                if rows and fields:
                    last = dict(zip(fields, rows[-1]))
                    entry["valuation_sample"] = {
                        k: last.get(k, "") for k in ("peTTM", "pbMRQ", "psTTM")}
        except Exception as ex:
            entry["error"] = f"{type(ex).__name__}: {ex}"
            entry["trace"] = traceback.format_exc()[-600:]
        REPORT["prices"][code] = entry
        flag = "OK " if entry["ok"] else "FAIL"
        print(f"  [{flag}] 日线 {code} ({label}): "
              f"{entry.get('row_count','-')} 行  "
              f"{('估值='+str(entry.get('valuation_sample'))) if entry.get('ok') else entry.get('error','')}")


def probe_financials(bs):
    code, label = FIN_CODE
    queries = [
        ("profit", bs.query_profit_data, "利润(ROE/净利率/毛利率/净利润)"),
        ("balance", bs.query_balance_data, "资产负债(流动比/资产负债率)"),
        ("cash_flow", bs.query_cash_flow_data, "现金流"),
        ("growth", bs.query_growth_data, "成长(净利同比等)"),
        ("operation", bs.query_operation_data, "运营(周转率)"),
        ("dupont", bs.query_dupont_data, "杜邦(ROE分解)"),
    ]
    # 先确定一个有数据的季度
    chosen = None
    for name, fn, _desc in queries:
        for (yr, q) in FIN_QUARTERS:
            try:
                rs = fn(code=code, year=yr, quarter=q)
                if rs.error_code == "0":
                    fields, rows = _collect(rs)
                    if rows:
                        chosen = (yr, q)
                        break
            except Exception:
                continue
        if chosen:
            break
    REPORT["financials"]["chosen_quarter"] = chosen
    print(f"\n  财报探测标的: {code} ({label})  采用季度: {chosen}")
    if not chosen:
        REPORT["financials"]["error"] = "六张表在所有候选季度都无数据"
        print("  [FAIL] 所有候选季度都取不到财报")
        return

    yr, q = chosen
    for name, fn, desc in queries:
        entry = {"desc": desc, "ok": False, "year": yr, "quarter": q}
        try:
            rs = fn(code=code, year=yr, quarter=q)
            if rs.error_code != "0":
                entry["error"] = f"error_code={rs.error_code} {rs.error_msg}"
            else:
                fields, rows = _collect(rs)
                entry["ok"] = len(rows) > 0
                entry["fields"] = fields
                entry["first_row"] = rows[0] if rows else None
        except Exception as ex:
            entry["error"] = f"{type(ex).__name__}: {ex}"
        REPORT["financials"][name] = entry
        flag = "OK " if entry["ok"] else "FAIL"
        nf = len(entry.get("fields", []) or [])
        print(f"  [{flag}] {name:10s} {desc}: {nf} 字段  {entry.get('error','')}")


def main():
    print("=" * 62)
    print("Baostock 数据源一致性探测  (2026-06-21)")
    print("=" * 62)
    try:
        import baostock as bs
    except ImportError:
        print("\n[FAIL] 未安装 baostock。请先运行:")
        print("    poetry run pip install baostock\n然后重跑本脚本。")
        sys.exit(1)

    print("\n[1] 登录 Baostock ...")
    lg = bs.login()
    REPORT["baostock_login"] = {"error_code": lg.error_code, "error_msg": lg.error_msg}
    if lg.error_code != "0":
        print(f"  [FAIL] 登录失败: {lg.error_code} {lg.error_msg}")
        print("  → 说明你这台机器连不上 baostock 服务器，请把这条结果告诉我。")
        _write()
        sys.exit(1)
    print(f"  [OK] 登录成功 ({lg.error_msg})")

    print("\n[2] 日线 + 估值字段探测 (含次新股 605788):")
    probe_prices(bs)

    print("\n[3] 季度财报六张表探测:")
    probe_financials(bs)

    REPORT["notes"].append("Baostock 仅支持 A 股；港股(00700/09880等)需继续用新浪/腾讯。")
    REPORT["notes"].append("实时报价继续用腾讯 qt.gtimg.cn；新闻继续用 stock_news_em。")

    bs.logout()
    _write()
    print("\n" + "=" * 62)
    ok_p = sum(1 for v in REPORT["prices"].values() if v.get("ok"))
    ok_f = sum(1 for k, v in REPORT["financials"].items()
               if isinstance(v, dict) and v.get("ok"))
    print(f"探测完成: 日线 {ok_p}/{len(PRICE_CODES)} 通  |  财报 {ok_f}/6 张表通")
    print("报告已写入: baostock_probe_report.json")
    print("→ 把 baostock_probe_report.json 整个贴回新会话给我。")
    print("=" * 62)


def _write():
    try:
        with open("baostock_probe_report.json", "w", encoding="utf-8") as f:
            json.dump(REPORT, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        print(f"[WARN] 写报告失败: {ex}")


if __name__ == "__main__":
    main()
