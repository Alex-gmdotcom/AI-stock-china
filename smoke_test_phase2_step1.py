"""
smoke_test_phase2_step1.py — tools/api_china.py 真网络 smoke test
=================================================================

用法 (仓库根目录):

    python smoke_test_phase2_step1.py

行为:
  - 用 5 个真实 ticker (A 股 4 + 港股 1) 各跑一次 quote()
  - 报告每个 ticker 实际使用了哪个 source (审计 fallback chain)
  - 报告报价是否在合理范围 (sanity check)
  - 最后给汇总表

预期结果 (基于 2026-06-17 §3 探测):
  - 600519 / 000001 / 300750 / 688008 → 大概率 source = tencent_qt
  - 00700.HK → 可能 source = tencent_qt 或 sina_hq

任何 source = "<error>..." 都标 [FAIL] 但继续往下跑,
因为单只挂不应阻塞其他 ticker 的诊断.
"""

import sys
import time
from pathlib import Path

# 兼容两种调用: 从仓库根运行 / 从 src/ 运行
HERE = Path(__file__).resolve().parent
for candidate in (HERE / "src", HERE):
    if (candidate / "tools" / "api_china.py").exists():
        sys.path.insert(0, str(candidate))
        break

try:
    # 优先用 boot 初始化 (保证 NO_PROXY + 横幅)
    try:
        import boot  # noqa: F401
        boot.boot_print_versions()
    except ImportError:
        try:
            from src import boot  # type: ignore
            boot.boot_print_versions()
        except ImportError:
            print("[WARN] boot.py 未找到,直接 import,NO_PROXY 可能未注入")
            from markets.proxy import ensure_no_proxy
            ensure_no_proxy()

    from tools.api_china import quote, batch_quote, NoDataSourceAvailable, __version__
except ImportError as exc:
    print(f"[FATAL] import 失败: {exc}")
    print("提示: 请在仓库根目录运行 (能看到 src/ 的位置)")
    sys.exit(1)


# 测试用例: (ticker, 期望市场, 报价合理上限/下限)
TEST_CASES = [
    ("600519",    "SH", 200,  3500),     # 贵州茅台
    ("000001.SZ", "SZ", 5,    50),       # 平安银行
    ("300750",    "SZ", 100,  500),      # 宁德时代
    ("688008",    "SH", 20,   500),      # 澜起科技
    ("00700.HK",  "HK", 100,  1000),     # 腾讯控股
]


def _fmt(q) -> str:
    return (
        f"{q.ticker:<12} {q.name[:8]:<10} "
        f"price={q.price:>10.3f}  chg={q.change_pct:>+6.2f}%  "
        f"src={q.source}"
    )


def main() -> int:
    print()
    print("=" * 76)
    print(f"smoke_test phase2-step1 — tools/api_china v{__version__}")
    print("=" * 76)

    results = []
    sources_used: dict[str, int] = {}
    t_total_start = time.time()

    for ticker, expected_market, lo, hi in TEST_CASES:
        t0 = time.time()
        try:
            q = quote(ticker)
            dt = time.time() - t0

            # Sanity check 1: market 字段
            market_ok = q.market == expected_market
            # Sanity check 2: 价格在合理范围
            price_ok = lo <= q.price <= hi

            tag = "[OK]  " if (market_ok and price_ok) else "[WARN]"
            results.append((tag, ticker, q, dt, None))
            sources_used[q.source] = sources_used.get(q.source, 0) + 1

            print(f"{tag} {_fmt(q)}  ({dt*1000:.0f}ms)")
            if not market_ok:
                print(f"       └─ market 字段 {q.market!r} ≠ 期望 {expected_market!r}")
            if not price_ok:
                print(f"       └─ 价格 {q.price} 不在合理区间 [{lo}, {hi}] — 可能数据异常")

        except NoDataSourceAvailable as exc:
            dt = time.time() - t0
            results.append(("[FAIL]", ticker, None, dt, str(exc)))
            print(f"[FAIL] {ticker:<12}  ({dt*1000:.0f}ms)")
            print(f"       └─ {exc}")
        except Exception as exc:  # noqa: BLE001
            dt = time.time() - t0
            results.append(("[FAIL]", ticker, None, dt, f"{type(exc).__name__}: {exc}"))
            print(f"[FAIL] {ticker:<12}  ({dt*1000:.0f}ms)")
            print(f"       └─ {type(exc).__name__}: {exc}")

    total_dt = time.time() - t_total_start
    print("-" * 76)
    print()

    # 汇总
    ok_count = sum(1 for r in results if r[0] == "[OK]  ")
    warn_count = sum(1 for r in results if r[0] == "[WARN]")
    fail_count = sum(1 for r in results if r[0] == "[FAIL]")

    print("Source 使用分布 (审计 fallback chain 真实行为):")
    for src, count in sorted(sources_used.items(), key=lambda x: -x[1]):
        print(f"  {src:<20}  {count} / {len(TEST_CASES)}")
    print()
    print(f"汇总: {ok_count} OK, {warn_count} WARN, {fail_count} FAIL")
    print(f"总耗时: {total_dt:.2f}s")
    print()

    # 诊断提示
    if fail_count == len(TEST_CASES):
        print("[DIAG] 全部失败 — 网络层问题可能:")
        print("  1. 检查是否能直接访问 http://qt.gtimg.cn/q=sh600519 (浏览器)")
        print("  2. 检查 NO_PROXY 注入: print(os.environ.get('NO_PROXY'))")
        print("  3. 检查代理设置: Clash/v2rayN 是否在 TUN 模式拦截了 reqs")
    elif fail_count > 0:
        print("[DIAG] 部分失败:")
        for tag, ticker, _, _, err in results:
            if tag == "[FAIL]":
                print(f"  - {ticker}: {err[:120] if err else ''}")
    elif sources_used.get("eastmoney_spot", 0) >= 3:
        print("[DIAG] 多数请求落到 eastmoney_spot — 腾讯/新浪可能被网络层干扰,建议:")
        print("  1. 浏览器测试 http://qt.gtimg.cn/q=sh600519 是否可达")
        print("  2. 浏览器测试 http://hq.sinajs.cn/list=sh600519 是否可达")
    else:
        print("[DIAG] fallback chain 工作正常,建议作为 Phase 2 后续模块的数据底座")

    # 退出码: 至少一只成功就算 0
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
