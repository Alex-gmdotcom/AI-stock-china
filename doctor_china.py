#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-hedge-fund 中国版 · 数据链路自检 (doctor)
用法: poetry run python doctor_china.py
独立运行，不依赖项目其他模块，只需 akshare + requests。
逐项测试早晚报所需的全部数据接口，输出诊断报告 doctor_report.json
"""
import os
import sys
import time
import json
import datetime as dt

RESULTS = []
TODAY = dt.date.today()
END = TODAY.strftime("%Y%m%d")
START = (TODAY - dt.timedelta(days=30)).strftime("%Y%m%d")


def record(name, ok, elapsed, detail, error=""):
    RESULTS.append({
        "check": name, "ok": ok, "elapsed_s": round(elapsed, 1),
        "detail": detail, "error": error,
    })


def check(name, fn, hint=""):
    """执行单项检查，打印结果，绝不静默吞异常"""
    t0 = time.time()
    try:
        out = fn()
        elapsed = time.time() - t0
        n = len(out) if hasattr(out, "__len__") else 1
        detail = f"{n} 行/项"
        print(f"  [OK]   {name}  ({elapsed:.1f}s, {detail})")
        record(name, True, elapsed, detail)
        return out
    except Exception as e:
        elapsed = time.time() - t0
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"  [FAIL] {name}  ({elapsed:.1f}s)")
        print(f"         -> {msg}")
        if hint:
            print(f"         建议: {hint}")
        record(name, False, elapsed, "", msg)
        return None


def try_chain(names, kwargs_list=None):
    """按顺序尝试多个 akshare 接口名（应对版本间改名），全部失败才抛异常"""
    import akshare as ak
    errors = []
    for i, name in enumerate(names):
        fn = getattr(ak, name, None)
        if fn is None:
            errors.append(f"{name}: 接口不存在(版本过旧或已移除)")
            continue
        kw = {}
        if kwargs_list and i < len(kwargs_list) and kwargs_list[i]:
            kw = kwargs_list[i]
        try:
            out = fn(**kw)
            print(f"         (命中接口: {name})")
            return out
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__} {str(e)[:100]}")
    raise RuntimeError(" | ".join(errors))


def main():
    print("=" * 62)
    print("ai-hedge-fund 中国版 数据链路自检")
    print(f"时间: {dt.datetime.now().isoformat(timespec='seconds')}")
    print("=" * 62)

    # ---------- 0. 环境 ----------
    print("\n[0] 运行环境")
    print(f"  Python: {sys.version.split()[0]}")
    try:
        import akshare as ak
        print(f"  AKShare: {ak.__version__}")
        record("akshare导入", True, 0, ak.__version__)
    except Exception as e:
        print(f"  [FAIL] akshare 导入失败: {e}")
        print("         建议: poetry run pip install -U akshare")
        record("akshare导入", False, 0, "", str(e)[:200])
        finish()
        return

    proxy_vars = {k: v for k, v in os.environ.items()
                  if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
    if proxy_vars:
        print(f"  [警告] 检测到代理环境变量: {list(proxy_vars.keys())}")
        print("         AKShare 走代理访问东财国内源极易超时/被拒，这是数据全缺的头号嫌疑")
        record("代理检测", False, 0, json.dumps(proxy_vars, ensure_ascii=False))
    else:
        print("  代理环境变量: 无 (注意: 系统级/TUN模式代理不体现在环境变量中)")
        record("代理检测", True, 0, "无环境变量代理")

    # ---------- 1. 东财直连 ----------
    print("\n[1] 东方财富直连测试 (push2.eastmoney.com)")

    def _net():
        import requests
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={"secids": "1.000001", "fields": "f2,f3,f12,f14"},
            timeout=8,
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("diff", [])

    net_ok = check("东财行情服务器直连", _net,
                   hint="若失败: 关闭VPN/代理, 或将 *.eastmoney.com 加入代理白名单; 海外网络直连东财本身不稳")

    # ---------- 2. 行情数据 ----------
    print("\n[2] 行情数据")
    check("A股日线 (600519 贵州茅台)",
          lambda: try_chain(["stock_zh_a_hist"],
                            [{"symbol": "600519", "period": "daily",
                              "start_date": START, "end_date": END, "adjust": "qfq"}]))

    check("A股实时盘口 (600519)",
          lambda: try_chain(["stock_bid_ask", "stock_zh_a_spot_em"],
                            [{"symbol": "600519"}, {}]),
          hint="spot_em 拉全市场较慢, 海外网络下建议改用逐只 stock_bid_ask")

    check("港股日线 (00148 建滔集团)",
          lambda: try_chain(["stock_hk_hist", "stock_hk_daily"],
                            [{"symbol": "00148", "period": "daily",
                              "start_date": START, "end_date": END, "adjust": ""},
                             {"symbol": "00148"}]))

    # ---------- 3. 资金面 ----------
    print("\n[3] 资金面数据")
    check("北向资金",
          lambda: try_chain(["stock_hsgt_fund_flow_summary_em",
                             "stock_hsgt_north_net_flow_in_em",
                             "stock_hsgt_fund_min_em"],
                            [{}, {"symbol": "北上"}, {"symbol": "北向资金"}]),
          hint="该接口历史上多次改名, 全部失败则 pip install -U akshare 后重试")

    check("个股主力资金流 (600519)",
          lambda: try_chain(["stock_individual_fund_flow"],
                            [{"stock": "600519", "market": "sh"}]),
          hint="注意 market 参数必须小写 (踩坑#12)")

    check("融资融券",
          lambda: try_chain(["stock_margin_account_info", "stock_margin_sse"],
                            [{}, {"start_date": START, "end_date": END}]))

    # ---------- 3.5 备用数据源（新浪/腾讯，海外友好） ----------
    print("\n[3.5] 备用数据源（系统在东财失败时自动回退到这些源）")
    check("新浪A股日线 (sh600519)",
          lambda: try_chain(["stock_zh_a_daily"],
                            [{"symbol": "sh600519", "adjust": "qfq"}]),
          hint="价格链备源, 决策引擎短路与否取决于此")
    check("新浪港股日线 (00148)",
          lambda: try_chain(["stock_hk_daily"], [{"symbol": "00148"}]))

    def _tencent():
        import requests
        r = requests.get("https://qt.gtimg.cn/q=sh600519,hk00148,usDJI",
                         timeout=8, headers={"Referer": "https://gu.qq.com/"})
        r.raise_for_status()
        r.encoding = "gbk"
        n = r.text.count("v_")
        if n < 2:
            raise RuntimeError("返回条目不足: " + r.text[:80])
        return list(range(n))
    check("腾讯实时行情+指数 (qt.gtimg.cn)", _tencent,
          hint="快照兜底与全球指数温度计依赖此源")

    # ---------- 4. 板块与新闻 ----------
    print("\n[4] 板块与新闻")
    check("行业板块行情", lambda: try_chain(["stock_board_industry_name_em"], [{}]))
    check("个股新闻 (600519)", lambda: try_chain(["stock_news_em"], [{"symbol": "600519"}]),
          hint="东财新闻接口对海外IP限流较严, 失败不影响行情类数据")

    finish(net_ok is not None)


def finish(net_ok=False):
    print("\n" + "=" * 62)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    rate = passed / total * 100 if total else 0
    print(f"自检结果: {passed}/{total} 通过 ({rate:.0f}%)")

    fails = [r for r in RESULTS if not r["ok"]]
    if fails:
        print("\n失败项汇总:")
        for r in fails:
            print(f"  - {r['check']}: {r['error'][:120]}")

        # v3.4.1 智能结论: 区分"东财拒连海外IP"与"代理劫持"两种场景
        em_fails = [r for r in RESULTS if not r["ok"] and
                    ("ProxyError" in r.get("error", "") or
                     "RemoteDisconnected" in r.get("error", "") or
                     "502" in r.get("error", ""))]
        proxy_type = any("ProxyError" in r.get("error", "") for r in em_fails)
        backup_ok = any(r["ok"] and ("新浪" in r["check"] or "腾讯" in r["check"])
                        for r in RESULTS)
        if em_fails and backup_ok and not proxy_type:
            print("\n【诊断结论】东财服务器拒绝当前网络的直连（海外IP限制），")
            print("  但新浪/腾讯备源可用 —— 系统核心链路(价格/快照/决策)不受影响，")
            print("  受影响仅: 行业板块排名、个股主力资金流（东财独有，报告中标【数据缺口】）。")
            print("  彻底解决三选一:")
            print("   ① 代理客户端把 *.eastmoney.com 单独走中国大陆方向节点,")
            print("      并设环境变量 AHF_DISABLE_NO_PROXY=1（详见 proxy_guard.py 说明）")
            print("   ② 采集端部署到国内 VPS 定时运行")
            print("   ③ 接受缺口（不影响个股分析与早晚报核心内容）")
        elif em_fails and proxy_type:
            print("\n【诊断结论】检测到 ProxyError —— 系统代理劫持了东财请求,")
            print("  按下方建议配置代理直连白名单。")

        print("\n修复建议(按优先级):")
        if any(r["check"] == "代理检测" and not r["ok"] for r in RESULTS):
            print("  1. 关闭代理/VPN后重跑, 或为 *.eastmoney.com / *.sinajs.cn /")
            print("     qt.gtimg.cn 配置代理直连规则(白名单)")
        if not net_ok:
            print("  2. 东财直连失败: 若无代理仍失败, 说明当前网络(海外)到东财链路不通,")
            print("     建议: a)挂中国大陆方向代理 b)部署到国内VPS/云函数定时跑采集")
        if any("接口不存在" in r.get("error", "") or "改名" in r.get("error", "")
               for r in fails):
            print("  3. 接口改名: poetry run pip install -U akshare 升级后重跑")
        print("  4. 把 doctor_report.json 内容贴回会话, 可针对性出补丁")
    else:
        print("\n数据链路全通。若早晚报仍出现【数据缺口】, 问题在")
        print("briefing_generator 的采集封装层而非 AKShare 本身,")
        print("请贴出 _collect_market_snapshot() 源码定位。")

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "doctor_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"time": dt.datetime.now().isoformat(),
                   "python": sys.version.split()[0],
                   "pass_rate": f"{passed}/{total}",
                   "results": RESULTS}, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {report_path}")


if __name__ == "__main__":
    main()
