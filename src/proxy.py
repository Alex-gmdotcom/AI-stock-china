"""
markets/proxy.py — NO_PROXY 进程级注入

对应不变量 I6.4：系统级代理（Clash / v2rayN TUN 模式）必须通过 NO_PROXY 进程级注入隔离 AKShare 数据源。

为什么不依赖 trust_env=True：
    Windows 上 TUN 模式 = 系统级流量劫持，不经过环境变量。
    trust_env=True 只读环境变量里的代理设置，对 TUN 模式无效。
    必须主动用 NO_PROXY 告诉 requests："这些 hostname 不走任何代理"。

使用方式：
    在 web_app.py / main_china.py 文件的第 1-2 行调用：
        from markets.proxy import inject_no_proxy
        inject_no_proxy()
    然后才能 import 任何调用 AKShare 的模块。

自测：
    python -m markets.proxy
"""

import os
from typing import Tuple

__version__ = "v1.0.0"


AKSHARE_NO_PROXY_HOSTS = [
    # === 东方财富（核心数据源，必须最前） ===
    "*.eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "data.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "fund.eastmoney.com",
    "quote.eastmoney.com",
    "emhsmarketwg.eastmoneysecurities.com",

    # === 腾讯财经（第一备用 fallback） ===
    "qt.gtimg.cn",
    "web.ifzq.gtimg.cn",
    "stock.gtimg.cn",

    # === 新浪财经（第二备用 fallback） ===
    "hq.sinajs.cn",
    "finance.sina.com.cn",
    "vip.stock.finance.sina.com.cn",
    "money.finance.sina.com.cn",

    # === AKShare 自身可能用到 ===
    "akshare.akfamily.xyz",

    # === v2 预留：财新 / 巨潮 / 交易所 ===
    "*.caixin.com",
    "*.cninfo.com.cn",
    "static.sse.com.cn",
    "query.sse.com.cn",
    "www.szse.cn",
]


def inject_no_proxy() -> Tuple[int, str]:
    """
    向当前进程的环境变量注入 NO_PROXY 白名单。

    必须在任何 AKShare / requests 调用之前调用一次。重复调用幂等（合并去重）。

    Returns:
        (host_count, merged_value): 注入后的 host 总数 + 最终 NO_PROXY 字符串
    """
    existing_upper = os.environ.get("NO_PROXY", "")
    existing_lower = os.environ.get("no_proxy", "")

    # 合并已有 + 新增 + 去重
    current = set()
    for src in (existing_upper, existing_lower):
        if src:
            current.update(h.strip() for h in src.split(",") if h.strip())
    current.update(AKSHARE_NO_PROXY_HOSTS)

    merged = ",".join(sorted(current))

    # 大小写都写（不同库读不同 key）
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged

    print(
        f"[boot] NO_PROXY injected: {len(current)} hosts total "
        f"({len(AKSHARE_NO_PROXY_HOSTS)} from AKShare whitelist)"
    )
    return len(current), merged


def _self_test() -> None:
    """自测块。
    1. 已存在 entry 必须被保留
    2. AKShare 白名单全部注入
    3. 重复调用必须幂等
    """
    print("=" * 60)
    print("markets.proxy self-test")
    print("=" * 60)

    # 模拟已存在的 NO_PROXY
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    print(f"  pre-existing NO_PROXY: {os.environ['NO_PROXY']}")

    n1, merged1 = inject_no_proxy()
    assert "localhost" in merged1, "existing entries must be preserved"
    assert "127.0.0.1" in merged1
    assert "push2.eastmoney.com" in merged1, "AKShare whitelist must be injected"
    assert "qt.gtimg.cn" in merged1
    print(f"  test 1 PASS: existing entries preserved ({n1} hosts after merge)")
    print(f"  test 2 PASS: AKShare whitelist injected")

    n2, merged2 = inject_no_proxy()
    assert n1 == n2, f"idempotent: expected {n1}, got {n2}"
    assert merged1 == merged2
    print(f"  test 3 PASS: idempotent on repeat call")

    # 连通性检查（可选，不强依赖）
    try:
        import requests
        r = requests.get("https://www.baidu.com", timeout=3)
        print(f"  test 4 INFO: baidu reachable (status {r.status_code})")
    except Exception as e:
        print(f"  test 4 SKIP: network unavailable ({type(e).__name__})")

    print("=" * 60)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    _self_test()
