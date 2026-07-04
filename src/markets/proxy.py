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

    # === 裸二级域兜底（requests 用 endswith 匹配，覆盖任意编号子域，
    #     如 82.push2 / 17.push2 等负载均衡 host，防未来新增子域漏网） ===
    "eastmoney.com",
    "gtimg.cn",
    "sinajs.cn",
    "sina.com.cn",
    "akfamily.xyz",

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

    # 环境开关：用国内代理/VPS 时设 AIHF_NO_PROXY=0，让东财请求走你的代理
    # （不再被 NO_PROXY 强制直连 → 直连从海外出口仍被东财拒）。默认开启（破本地坏代理）。
    if os.environ.get("AIHF_NO_PROXY", "1").strip().lower() in ("0", "false", "off", "no"):
        print("[boot] NO_PROXY injection DISABLED (AIHF_NO_PROXY=0) — 东财等请求将走系统/代理")
        return 0, existing_upper or existing_lower

    existing_upper = os.environ.get("NO_PROXY", "")
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


_ENSURED = False


def ensure_no_proxy() -> None:
    """按需保证 NO_PROXY 已注入（数据层每次 AKShare 调用前调用）。

    历史命名事故修复：api_china.py / line_items_china.py 一直 import 的是
    ``ensure_no_proxy``，但本模块此前只导出 ``inject_no_proxy``，导致 import
    静默失败、落到 NO-OP 兜底 —— NO_PROXY 从未真正注入，AKShare 全程走系统
    TUN 代理，东财全线 ProxyError，current_price=0 短路整条决策链。

    本函数首次调用时真正注入白名单，之后幂等空转（零开销）。
    """
    global _ENSURED
    if _ENSURED:
        return
    inject_no_proxy()
    _ENSURED = True


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


# ── marker: V8_IMPORT_GUARD_V1 (I10.6) ──
def _poison_py_mini_racer() -> None:
    """marker: V8_IMPORT_GUARD_V2 (I10.6)
    v1 教训: None 毒杀使 akshare 1.18.64 整包 ImportError(其 fund 子模块在
    包初始化链顶层 import py_mini_racer)→ _HAVE_AK=False → 全市场零价格
    (2026-07-05 实锤)。v2: 假模块桩——import 放行(akshare 正常加载),
    MiniRacer 实例化(akshare 全部在端点函数体内,惰性)抛可捕获 RuntimeError
    → 单端点 fail-soft 走缺口;V8 DLL 永不加载,0703 FATAL 类保持灭绝。"""
    import sys as _sys
    import types as _types
    _existing = _sys.modules.get("py_mini_racer")
    if _existing is not None and not getattr(_existing, "_AIHF_V8_STUB", False):
        return  # 已被真实加载(异常情况),不制造半初始化态

    class _V8Blocked:
        """任何实例化/属性用法 → 可捕获异常,绝不加载 V8 DLL。"""
        def __init__(self, *a, **kw):
            raise RuntimeError(
                "py_mini_racer 已被 V8 护栏禁用(I10.6): 该 akshare 端点依赖 V8,"
                "降级为【数据缺口】,fallback 链继续")
    _V8Blocked.MiniRacer = _V8Blocked  # 兼容 from py_mini_racer import py_mini_racer 旧式

    stub = _types.ModuleType("py_mini_racer")
    stub._AIHF_V8_STUB = True
    stub.MiniRacer = _V8Blocked
    def _module_getattr(name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _V8Blocked
    stub.__getattr__ = _module_getattr
    _sys.modules["py_mini_racer"] = stub


_poison_py_mini_racer()  # proxy 是 main_china/web_app 的最早公共 import,导入即生效
