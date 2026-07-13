# -*- coding: utf-8 -*-
"""
probe_northbound.py — 北向数据"有壳无肉"三假设分诊探针
================================================================
背景: capfix 后 capital 维度 northbound 元素 date 在、total_net_buy/sh/sz 全 None。
待区分假设:
  H1: tushare 权限/积分不足(接口报错,错误文本含"积分/权限")
  H2: 数据在源头已停更 —— 2024-08-19 起陆股通披露机制调整,
      日度北向净买入不再披露(moneyflow_hsgt 数据应终止在 2024-08-16 附近);
      个股北向持股量(CCASS, hk_hold)仍每日披露
  H3: 源有数但本项目 fallback 链/解析拿不到(源直连 OK 而项目输出 None)
运行: poetry run python probe_northbound.py   (在 E:\\AI-tool\\Stock\\ai-hedge-fund 下)
输出: northbound_probe_result.txt (utf-8, 控制台只打 ASCII, 防 GBK 炸)
只读探针: 不改任何项目文件、不写缓存。
"""
import os
import sys
import io
import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path

RESULT_PATH = Path("northbound_probe_result.txt")
_lines = []


def log(s=""):
    _lines.append(str(s))


def flush():
    RESULT_PATH.write_text("\n".join(_lines), encoding="utf-8")


def head(title):
    log()
    log("=" * 64)
    log(title)
    log("=" * 64)


def df_report(name, df, value_cols=None):
    """统一报告: 区分 OK / EMPTY / 有壳无肉(NULL-VALUES) / 列缺失"""
    try:
        import pandas as pd  # noqa
    except Exception:
        pass
    if df is None:
        log(f"[{name}] -> None 返回")
        return "EMPTY"
    try:
        n = len(df)
    except Exception:
        log(f"[{name}] -> 非表格返回: {type(df)} {str(df)[:200]}")
        return "ERROR"
    if n == 0:
        log(f"[{name}] -> EMPTY (0 行)")
        return "EMPTY"
    cols = list(df.columns)
    log(f"[{name}] -> {n} 行, 列: {cols}")
    # 日期范围
    for dc in ("trade_date", "date", "日期"):
        if dc in cols:
            try:
                log(f"    日期范围: {df[dc].min()} .. {df[dc].max()}")
            except Exception:
                pass
            break
    # 值层判空(有壳无肉检测, I1.1)
    status = "OK"
    if value_cols:
        present = [c for c in value_cols if c in cols]
        missing = [c for c in value_cols if c not in cols]
        if missing:
            log(f"    [列缺失] {missing}")
        all_null_cols = []
        for c in present:
            try:
                if df[c].isna().all():
                    all_null_cols.append(c)
            except Exception:
                pass
        if all_null_cols:
            log(f"    [有壳无肉] 值列全 None/NaN: {all_null_cols}")
            status = "NULL-VALUES"
    # 样本(首尾各1行)
    try:
        log("    首行: " + df.head(1).to_string(index=False).replace("\n", " | "))
        log("    末行: " + df.tail(1).to_string(index=False).replace("\n", " | "))
    except Exception:
        pass
    return status


def err_report(name, exc):
    txt = f"{type(exc).__name__}: {exc}"
    log(f"[{name}] -> ERROR: {txt[:300]}")
    lower = txt.lower()
    if ("积分" in txt) or ("权限" in txt) or ("permission" in lower) or ("points" in lower):
        log("    ** 错误文本含 积分/权限 关键词 -> 支持 H1 **")
        return "PERM"
    return "ERROR"


results = {}

# ----------------------------------------------------------------
head("0. 环境准备 (NO_PROXY 注入 + .env token)")
# 尽量复用项目的 NO_PROXY 注入; 失败则手动兜底
try:
    sys.path.insert(0, "src")
    from markets.proxy import inject_no_proxy  # type: ignore
    inject_no_proxy()
    log("NO_PROXY: 已经项目 inject_no_proxy() 注入")
except Exception as e:
    extra = ("eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,"
             "datacenter-web.eastmoney.com,api.tushare.pro,tushare.pro,"
             "sina.com.cn,hq.sinajs.cn,qt.gtimg.cn,akshare.xyz")
    os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + "," + extra
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    log(f"NO_PROXY: 项目注入失败({type(e).__name__}), 已手动兜底追加东财/tushare/新浪域")

TS_TOKEN = None
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if "TUSHARE" in k.upper():
            TS_TOKEN = v.strip().strip('"').strip("'")
            log(f".env: 找到 token 键 {k.strip()} (长度 {len(TS_TOKEN)})")
            break
if not TS_TOKEN:
    TS_TOKEN = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_API_KEY")
if not TS_TOKEN:
    log(".env / 环境变量均未找到 TUSHARE token -> tushare 段将全部跳过(本身即线索: 项目怎么配的 token?)")

today = datetime.now()
d_end = today.strftime("%Y%m%d")
d_start_30 = (today - timedelta(days=30)).strftime("%Y%m%d")
d_start_10 = (today - timedelta(days=14)).strftime("%Y%m%d")

# ----------------------------------------------------------------
head("1. tushare 直连 — moneyflow_hsgt (北向日度净买入)")
log("判据: 若 2024-08 窗口有数而近 30 日窗口空/全 None,且最大日期停在 2024-08-16 附近 -> H2 铁证(披露停更)")
pro = None
if TS_TOKEN:
    try:
        import tushare as ts
        pro = ts.pro_api(TS_TOKEN)
    except Exception as e:
        results["ts_init"] = err_report("tushare 初始化", e)

if pro:
    # 1a. 停更边界窗口: 2024-08-01 .. 2024-08-31
    try:
        df = pro.moneyflow_hsgt(start_date="20240801", end_date="20240831")
        results["ts_mf_202408"] = df_report(
            "moneyflow_hsgt 2024-08 边界窗", df,
            value_cols=["north_money", "hgt", "sgt"])
    except Exception as e:
        results["ts_mf_202408"] = err_report("moneyflow_hsgt 2024-08 边界窗", e)
    # 1b. 近 30 日窗口
    try:
        df = pro.moneyflow_hsgt(start_date=d_start_30, end_date=d_end)
        results["ts_mf_recent"] = df_report(
            f"moneyflow_hsgt 近30日 {d_start_30}..{d_end}", df,
            value_cols=["north_money", "hgt", "sgt"])
    except Exception as e:
        results["ts_mf_recent"] = err_report("moneyflow_hsgt 近30日", e)

    head("2. tushare 直连 — hk_hold (北向个股持股量 CCASS, 替代路线探测)")
    log("判据: 若近 14 日有数且 vol/ratio 非空 -> ΔCCASS 持股量变化路线可行(capflow 修类方向)")
    for code in ("600519.SH", "300308.SZ"):
        try:
            df = pro.hk_hold(ts_code=code, start_date=d_start_10, end_date=d_end)
            results[f"ts_hkhold_{code}"] = df_report(
                f"hk_hold {code} 近14日", df, value_cols=["vol", "ratio"])
        except Exception as e:
            results[f"ts_hkhold_{code}"] = err_report(f"hk_hold {code}", e)
else:
    log("(无 pro 客户端, 本段跳过)")

# ----------------------------------------------------------------
head("3. akshare 直连 — 东财北向系接口逐一探测")
log("判据: 源直连若 OK 而项目输出 None -> H3; 源直连也空/停更 -> H2; 连接被掐 -> 东财反爬(已知特性)")
try:
    import akshare as ak
    ak_ok = True
    log(f"akshare 版本: {getattr(ak, '__version__', '?')}")
except Exception as e:
    ak_ok = False
    results["ak_import"] = err_report("akshare import", e)

AK_TESTS = [
    # (接口名, kwargs, 值列)
    ("stock_hsgt_fund_flow_summary_em", {}, None),
    ("stock_hsgt_hist_em", {"symbol": "北向资金"}, ["当日成交净买额", "当日资金流入"]),
    ("stock_hsgt_hold_stock_em", {"market": "北向", "indicator": "今日排行"}, None),
    ("stock_hsgt_individual_em", {"stock": "600519"}, None),
]
if ak_ok:
    for fname, kwargs, vcols in AK_TESTS:
        fn = getattr(ak, fname, None)
        if fn is None:
            log(f"[{fname}] -> 接口不存在于本版 akshare (接口漂移, 坑9)")
            results[f"ak_{fname}"] = "MISSING"
            continue
        try:
            df = fn(**kwargs)
            results[f"ak_{fname}"] = df_report(f"{fname}{kwargs}", df, value_cols=vcols)
        except TypeError as e:
            # 参数签名漂移: 再裸调一次
            try:
                df = fn()
                results[f"ak_{fname}"] = df_report(f"{fname}() 裸调", df, value_cols=vcols)
            except Exception as e2:
                results[f"ak_{fname}"] = err_report(fname, e2)
        except Exception as e:
            results[f"ak_{fname}"] = err_report(fname, e)

# ----------------------------------------------------------------
head("4. 本项目代码内 northbound 消费点定位 (只读 grep)")
hits = 0
for root in ("src",):
    for p in Path(root).rglob("*.py"):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            low = line.lower()
            if ("northbound" in low or "hsgt" in low or "north_money" in low
                    or "total_net_buy" in low):
                log(f"{p}:{i}: {line.strip()[:150]}")
                hits += 1
                if hits >= 60:
                    break
        if hits >= 60:
            break
if hits == 0:
    log("(未找到消费点 —— 关键词可能不同, 需人工看 capflow agent 源)")

# ----------------------------------------------------------------
head("5. 自动裁定 (规则式, 最终以人读上文原始输出为准)")
mf_recent = results.get("ts_mf_recent", "SKIP")
mf_2408 = results.get("ts_mf_202408", "SKIP")
hkhold_any_ok = any(
    v == "OK" for k, v in results.items() if k.startswith("ts_hkhold"))
perm_any = any(v == "PERM" for v in results.values())
ak_ok_any = any(
    v == "OK" for k, v in results.items() if k.startswith("ak_"))

if perm_any:
    log("-> 存在 积分/权限 报错: H1 成立(至少部分接口), 对照哪些接口 PERM 定积分缺口。")
if mf_2408 == "OK" and mf_recent in ("EMPTY", "NULL-VALUES"):
    log("-> moneyflow_hsgt: 2024-08 有数 + 近期空/全None = H2 铁证:")
    log("   日度北向净买入自 2024-08-19 披露机制调整后源头停更, 非权限、非 bug。")
    log("   修类方向: capflow 弃 net_buy 流量, 改用 hk_hold(CCASS) 持股量日度变化 ΔCCASS。")
if hkhold_any_ok:
    log("-> hk_hold 个股持股量可用: ΔCCASS 替代路线数据面通 -> 可上会当裁决候选。")
if ak_ok_any and mf_recent in ("EMPTY", "NULL-VALUES", "SKIP"):
    log("-> akshare 侧有接口出数而项目输出 None: 叠加检查 H3(项目链路/解析), 对照第4节消费点。")
log()
log("results 摘要: " + json.dumps(results, ensure_ascii=False))

flush()
print("probe done ->", RESULT_PATH.resolve())
