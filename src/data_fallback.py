"""
tools/data_fallback.py — 数据源 fallback chain 原语

对应不变量 I1.2：数据源 fallback chain 必须可追溯，实际使用的链路在页面 footer 标注。

设计：
    每种数据类型都有一个 chain（按优先级排序的 source 列表）。
    调用 chain 时按序尝试，第一个成功且通过 validator 的返回 + 记录 source_name。
    全部失败抛 DataSourceExhaustedError 含每个 source 的失败原因。

为什么必须 chain：
    v3.5 教训：东方财富对海外 IP 502、对某些代理拦截，单一数据源导致整页空白。
    腾讯 qt / 新浪是稳定备用，覆盖度足够日常研究。

下游使用方式（由 api_china.py 调用）：
    from tools.data_fallback import call_with_fallback
    chain = [
        ("eastmoney",  lambda t: _quote_from_eastmoney(t)),
        ("tencent_qt", lambda t: _quote_from_tencent_qt(t)),
        ("sina",       lambda t: _quote_from_sina(t)),
    ]
    result = call_with_fallback(ticker, chain, validator=lambda d: d.get("price") is not None)
    quote = result.data
    snapshot.fallback_chain_used[f"quote_{ticker}"] = result.source_used

自测：
    python -m tools.data_fallback
"""

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

__version__ = "v1.0.0"


# =====================================================================
# 错误类型
# =====================================================================

class DataSourceExhaustedError(Exception):
    """所有 fallback source 都失败。"""

    def __init__(self, ticker: str, errors: List[str]):
        self.ticker = ticker
        self.errors = errors
        super().__init__(
            f"All data sources failed for {ticker}. "
            f"Tried {len(errors)} source(s): {errors}"
        )


# =====================================================================
# 结果对象
# =====================================================================

@dataclass
class FallbackResult:
    """fallback chain 调用的结果。

    Attributes:
        data: 实际返回的数据
        source_used: 实际生效的 source 名（如 "eastmoney" / "tencent_qt"）
        attempts: 所有尝试过的 source 名（按顺序，含失败的）
        errors: 失败 source 的错误描述
    """
    data: Any
    source_used: str
    attempts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# =====================================================================
# 主入口
# =====================================================================

def call_with_fallback(
    ticker: str,
    chain: List[Tuple[str, Callable[[str], Any]]],
    validator: Optional[Callable[[Any], bool]] = None,
) -> FallbackResult:
    """
    依次尝试 chain 中的每个 source，第一个成功且通过 validator 的返回。

    Args:
        ticker: 标的代码（用于错误信息）
        chain: [(source_name, fn), ...] 其中 fn(ticker) -> data
        validator: 可选，对 data 做有效性校验（如非空、字段完整）

    Returns:
        FallbackResult

    Raises:
        DataSourceExhaustedError: 全部失败
    """
    attempts: List[str] = []
    errors: List[str] = []

    for source_name, fn in chain:
        attempts.append(source_name)
        try:
            data = fn(ticker)
        except Exception as e:
            errors.append(f"{source_name}: {type(e).__name__}: {str(e)[:200]}")
            continue

        if validator is not None and not validator(data):
            errors.append(f"{source_name}: returned but failed validation")
            continue

        return FallbackResult(
            data=data,
            source_used=source_name,
            attempts=attempts,
            errors=errors,
        )

    raise DataSourceExhaustedError(ticker, errors)


# =====================================================================
# 占位 Chain 工厂（实际实现在 api_china.py）
# =====================================================================

def quote_chain():
    """实时报价 chain。Phase 2 接入 api_china.py 后回填。"""
    return []


def news_chain():
    """个股新闻 chain。"""
    return []


def kline_chain():
    """K 线 chain。"""
    return []


# =====================================================================
# 自测
# =====================================================================

def _self_test() -> None:
    print("=" * 60)
    print("tools.data_fallback self-test")
    print("=" * 60)

    # 测试 1：前两个失败，第三个成功
    def src_em(t):
        raise ConnectionError("eastmoney 502 from overseas IP")

    def src_qt(t):
        raise TimeoutError("tencent qt timeout")

    def src_sina(t):
        return {"price": 100.0, "volume": 5_000_000}

    chain = [("eastmoney", src_em), ("tencent_qt", src_qt), ("sina", src_sina)]
    result = call_with_fallback("000001.SZ", chain)
    assert result.data["price"] == 100.0
    assert result.source_used == "sina"
    assert result.attempts == ["eastmoney", "tencent_qt", "sina"]
    assert len(result.errors) == 2
    print(f"  Test 1 PASS: third source succeeded")
    print(f"    source_used = {result.source_used}")
    print(f"    errors      = {result.errors}")

    # 测试 2：全部失败
    chain_all_fail = [("eastmoney", src_em), ("tencent_qt", src_qt)]
    try:
        call_with_fallback("000001.SZ", chain_all_fail)
        raise AssertionError("Expected DataSourceExhaustedError")
    except DataSourceExhaustedError as e:
        print(f"  Test 2 PASS: {str(e)[:120]}...")

    # 测试 3：validator 拒绝
    def src_empty(t):
        return {"price": None}

    chain_empty = [("eastmoney", src_empty), ("tencent_qt", src_sina)]
    result = call_with_fallback(
        "000001.SZ",
        chain_empty,
        validator=lambda d: d.get("price") is not None,
    )
    assert result.source_used == "tencent_qt"
    assert "eastmoney: returned but failed validation" in result.errors
    print(f"  Test 3 PASS: validator rejected empty, fell through to tencent_qt")

    # 测试 4：空 chain
    try:
        call_with_fallback("000001.SZ", [])
        raise AssertionError("Expected DataSourceExhaustedError on empty chain")
    except DataSourceExhaustedError as e:
        print(f"  Test 4 PASS: empty chain raises ({e.errors=})")

    # 测试 5：第一个就成功（fast path）
    chain_first_ok = [("eastmoney", src_sina), ("tencent_qt", src_qt)]
    result = call_with_fallback("000001.SZ", chain_first_ok)
    assert result.source_used == "eastmoney"
    assert result.attempts == ["eastmoney"]
    assert result.errors == []
    print(f"  Test 5 PASS: fast path, only attempted eastmoney")

    print("=" * 60)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    _self_test()
