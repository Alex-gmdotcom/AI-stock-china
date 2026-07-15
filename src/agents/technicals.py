import math

from langchain_core.messages import HumanMessage

from src.graph.state import AgentState, show_agent_reasoning
from src.utils.api_key import get_api_key_from_state
import json
import pandas as pd
import numpy as np

from src.tools.api import get_prices, prices_to_df
from src.utils.progress import progress

import logging
_logger = logging.getLogger(__name__)

# ── marker: TECH_FIX3_V1 — 修③ A股动量捕捉(2026-07-15 Alex 批) ──────────
# 诊断(修②草案 §5/§10 靶子: 中际旭创+10倍读中性16%):
#   C1 批跑3月窗口喂不饱 mom_6m(126根)→动量策略结构性中性(I1.1守卫诚实但瞎)
#   C2 均值回归对延伸趋势股恒 bearish 高置信, 反向抵消 trend(美股均衡残留)
#   C3 中性策略 w×conf 稀释分母 → |score| 永远卡在 0.2 阈值下
#   C4 trend conf=ADX/100, 实战上限~0.5, 主升浪永远半信半疑
#   C6 量能确认=单日点值, 噪声开关
# 修法: FixA 扩窗(只向过去,PIT I10.4不破) FixB 相对强度(vs沪深300,原版stub落地)
#       FixC regime路由(强趋势掐MR)+中性不入分母 FixD conf=min(ADX/25,1)
TECH_LOOKBACK_CAL_DAYS = 460     # Fix A: mom_6m(126交易日)+暖机 对应自然日; end_date 锚定不动
TECH_BENCHMARK = "000300.SH"     # Fix B: 相对强度基准=沪深300(行业指数版留二期); .HK 票不适用→绝对口径
MR_ADX_GATE = 25.0               # Fix C: ADX>25 判强趋势态
MR_TREND_RELIABILITY = 0.3       # Fix C: 强趋势态下 mean_reversion 权重折扣
TREND_CONF_ADX_SCALE = 25.0      # Fix D: trend conf = min(ADX/25, 1)
MOM_VOL_SMOOTH_DAYS = 5          # C6: 量能确认 5日均量/21日均量(单日点值→噪声开关)


def safe_float(value, default=0.0):
    """
    Safely convert a value to float, handling NaN cases
    
    Args:
        value: The value to convert (can be pandas scalar, numpy value, etc.)
        default: Default value to return if the input is NaN or invalid
    
    Returns:
        float: The converted value or default if NaN/invalid
    """
    try:
        if pd.isna(value) or np.isnan(value):
            return default
        return float(value)
    except (ValueError, TypeError, OverflowError):
        return default


##### Technical Analyst #####
def technical_analyst_agent(state: AgentState, agent_id: str = "technical_analyst_agent"):
    """
    Sophisticated technical analysis system that combines multiple trading strategies for multiple tickers:
    1. Trend Following
    2. Mean Reversion
    3. Momentum
    4. Volatility Analysis
    5. Statistical Arbitrage Signals
    """
    data = state["data"]
    start_date = data["start_date"]
    end_date = data["end_date"]
    tickers = data["tickers"]
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    # Initialize analysis for each ticker
    technical_analysis = {}

    # Fix A: 指标回看窗口自给自足 —— 只向过去扩(end_date 锚定不动, PIT I10.4 不破)
    lookback_start = (pd.to_datetime(end_date)
                      - pd.Timedelta(days=TECH_LOOKBACK_CAL_DAYS)).strftime("%Y-%m-%d")
    fetch_start = min(str(start_date), lookback_start)
    # Fix B: 基准序列每 run 取一次, 全 ticker 复用; 取不到 → 绝对动量兜底(WARNING)
    bench_mom = _bench_momentum(_fetch_benchmark_closes(fetch_start, end_date))

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Analyzing price data")

        # Get the historical price data
        prices = get_prices(
            ticker=ticker,
            start_date=fetch_start,
            end_date=end_date,
            api_key=api_key,
        )

        if not prices:
            progress.update_status(agent_id, ticker, "Failed: No price data found")
            continue

        # Convert prices to a DataFrame
        prices_df = prices_to_df(prices)

        progress.update_status(agent_id, ticker, "Calculating trend signals")
        trend_signals = calculate_trend_signals(prices_df)

        progress.update_status(agent_id, ticker, "Calculating mean reversion")
        mean_reversion_signals = calculate_mean_reversion_signals(prices_df)

        progress.update_status(agent_id, ticker, "Calculating momentum")
        _bm = None if ticker.upper().endswith(".HK") else bench_mom   # HK 不对沪深300
        momentum_signals = calculate_momentum_signals(prices_df, bench_mom=_bm)

        progress.update_status(agent_id, ticker, "Analyzing volatility")
        volatility_signals = calculate_volatility_signals(prices_df)

        progress.update_status(agent_id, ticker, "Statistical analysis")
        stat_arb_signals = calculate_stat_arb_signals(prices_df)

        # Combine all signals using a weighted ensemble approach
        strategy_weights = {
            "trend": 0.25,
            "mean_reversion": 0.20,
            "momentum": 0.25,
            "volatility": 0.15,
            "stat_arb": 0.15,
        }

        progress.update_status(agent_id, ticker, "Combining signals")
        # Fix C: regime 路由 —— 强趋势态(ADX>25)掐 mean_reversion 权重
        effective_weights = apply_regime_gate(
            strategy_weights, trend_signals["metrics"].get("adx"))
        combined_signal = weighted_signal_combination(
            {
                "trend": trend_signals,
                "mean_reversion": mean_reversion_signals,
                "momentum": momentum_signals,
                "volatility": volatility_signals,
                "stat_arb": stat_arb_signals,
            },
            effective_weights,
        )

        # Generate detailed analysis report for this ticker
        technical_analysis[ticker] = {
            "signal": combined_signal["signal"],
            "confidence": round(combined_signal["confidence"] * 100),
            "regime": {"adx": safe_metric(trend_signals["metrics"].get("adx")),
                       "mr_weight": round(effective_weights["mean_reversion"], 3),
                       "score_raw": round(combined_signal.get("raw_score", 0.0), 3),
                       "breadth": round(combined_signal.get("breadth", 0.0), 3),
                       "momentum_basis": momentum_signals["metrics"].get("momentum_basis")},
            "reasoning": {
                "trend_following": {
                    "signal": trend_signals["signal"],
                    "confidence": round(trend_signals["confidence"] * 100),
                    "metrics": normalize_pandas(trend_signals["metrics"]),
                },
                "mean_reversion": {
                    "signal": mean_reversion_signals["signal"],
                    "confidence": round(mean_reversion_signals["confidence"] * 100),
                    "metrics": normalize_pandas(mean_reversion_signals["metrics"]),
                },
                "momentum": {
                    "signal": momentum_signals["signal"],
                    "confidence": round(momentum_signals["confidence"] * 100),
                    "metrics": normalize_pandas(momentum_signals["metrics"]),
                },
                "volatility": {
                    "signal": volatility_signals["signal"],
                    "confidence": round(volatility_signals["confidence"] * 100),
                    "metrics": normalize_pandas(volatility_signals["metrics"]),
                },
                "statistical_arbitrage": {
                    "signal": stat_arb_signals["signal"],
                    "confidence": round(stat_arb_signals["confidence"] * 100),
                    "metrics": normalize_pandas(stat_arb_signals["metrics"]),
                },
            },
        }
        progress.update_status(agent_id, ticker, "Done", analysis=json.dumps(technical_analysis, indent=4))

    # Create the technical analyst message
    message = HumanMessage(
        content=json.dumps(technical_analysis),
        name=agent_id,
    )

    if state["metadata"]["show_reasoning"]:
        show_agent_reasoning(technical_analysis, "Technical Analyst")

    # Add the signal to the analyst_signals list
    state["data"]["analyst_signals"][agent_id] = technical_analysis

    progress.update_status(agent_id, None, "Done")

    return {
        "messages": state["messages"] + [message],
        "data": data,
    }


def calculate_trend_signals(prices_df):
    """
    Advanced trend following strategy using multiple timeframes and indicators
    """
    # Calculate EMAs for multiple timeframes
    ema_8 = calculate_ema(prices_df, 8)
    ema_21 = calculate_ema(prices_df, 21)
    ema_55 = calculate_ema(prices_df, 55)

    # Calculate ADX for trend strength
    adx = calculate_adx(prices_df, 14)

    # Determine trend direction and strength
    short_trend = ema_8 > ema_21
    medium_trend = ema_21 > ema_55

    # Fix D(TECH_FIX3_V1): conf=min(ADX/25,1) —— 旧 ADX/100 实战上限~0.5,
    # 主升浪永远半信半疑; ADX>25 即成趋势, 以 25 为满刻度
    adx_last = adx["adx"].iloc[-1]
    trend_strength = min(safe_float(adx_last, 0.0) / TREND_CONF_ADX_SCALE, 1.0)

    if short_trend.iloc[-1] and medium_trend.iloc[-1]:
        signal = "bullish"
        confidence = trend_strength
    elif not short_trend.iloc[-1] and not medium_trend.iloc[-1]:
        signal = "bearish"
        confidence = trend_strength
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "adx": safe_float(adx["adx"].iloc[-1]),
            "trend_strength": safe_float(trend_strength),
        },
    }


def calculate_mean_reversion_signals(prices_df):
    """
    Mean reversion strategy using statistical measures and Bollinger Bands
    """
    # Calculate z-score of price relative to moving average
    ma_50 = prices_df["close"].rolling(window=50).mean()
    std_50 = prices_df["close"].rolling(window=50).std()
    z_score = (prices_df["close"] - ma_50) / std_50

    # Calculate Bollinger Bands
    bb_upper, bb_lower = calculate_bollinger_bands(prices_df)

    # Calculate RSI with multiple timeframes
    rsi_14 = calculate_rsi(prices_df, 14)
    rsi_28 = calculate_rsi(prices_df, 28)

    # Mean reversion signals
    price_vs_bb = (prices_df["close"].iloc[-1] - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1])

    # Combine signals
    if z_score.iloc[-1] < -2 and price_vs_bb < 0.2:
        signal = "bullish"
        confidence = min(abs(z_score.iloc[-1]) / 4, 1.0)
    elif z_score.iloc[-1] > 2 and price_vs_bb > 0.8:
        signal = "bearish"
        confidence = min(abs(z_score.iloc[-1]) / 4, 1.0)
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "z_score": safe_float(z_score.iloc[-1]),
            "price_vs_bb": safe_float(price_vs_bb),
            "rsi_14": safe_float(rsi_14.iloc[-1]),
            "rsi_28": safe_float(rsi_28.iloc[-1]),
        },
    }


def safe_metric(value):
    """marker: REDLINE_FIX_TECH_METRIC_V1 (I1.1)
    退化/NaN 指标透传 None(JSON null → 下游渲染【数据缺口】),
    禁止以 0.0 冒充真值(旧 safe_float 把 NaN 洗成 0.0,
    momentum_6m 在 3 个月批跑窗口下结构性 NaN → 全池长期入册假 0)。"""
    try:
        if value is None:
            return None
        if pd.isna(value):
            return None
        f = float(value)
        if not math.isfinite(f):
            return None
        return f
    except (ValueError, TypeError, OverflowError):
        return None


def calculate_momentum_signals(prices_df, bench_mom: dict | None = None):
    """Multi-factor momentum strategy — TECH_FIX3_V1
    Fix B: bench_mom={21:r,63:r,126:r}(沪深300 同窗累计收益)时用超额动量;
           None(基准不可得/HK票) → 绝对动量兜底, metrics 标 momentum_basis。
    C6:    量能确认 = 5日均量/21日均量(单日点值是噪声开关)。
    I1.1 守卫保留: 任一窗口不可算 → 低置信中性 + data_gaps。"""
    # Price momentum
    returns = prices_df["close"].pct_change()
    mom_1m = returns.rolling(21).sum()
    mom_3m = returns.rolling(63).sum()
    mom_6m = returns.rolling(126).sum()

    # Volume momentum(5日均量平滑, C6)
    volume_ma = prices_df["volume"].rolling(21).mean()
    volume_5d = prices_df["volume"].rolling(MOM_VOL_SMOOTH_DAYS).mean()
    volume_momentum = volume_5d / volume_ma

    # I1.1 守卫: 任一动量窗口退化(NaN)→ 数据不足, 低置信中性 + data_gaps。
    _gaps = [name for name, series in (
        ("momentum_1m", mom_1m), ("momentum_3m", mom_3m), ("momentum_6m", mom_6m),
    ) if pd.isna(series.iloc[-1])]
    if pd.isna(volume_momentum.iloc[-1]):
        _gaps.append("volume_momentum")
    if _gaps:
        return {
            "signal": "neutral",
            "confidence": 0.2,
            "metrics": {
                "momentum_1m": safe_metric(mom_1m.iloc[-1]),
                "momentum_3m": safe_metric(mom_3m.iloc[-1]),
                "momentum_6m": safe_metric(mom_6m.iloc[-1]),
                "volume_momentum": safe_metric(volume_momentum.iloc[-1]),
                "momentum_basis": None,
                "data_gaps": _gaps,
            },
        }

    # Fix B: 相对强度落地(原版 stub "would compare to market/sector")
    if bench_mom:
        ex_1m = mom_1m.iloc[-1] - bench_mom[21]
        ex_3m = mom_3m.iloc[-1] - bench_mom[63]
        ex_6m = mom_6m.iloc[-1] - bench_mom[126]
        momentum_score = 0.4 * ex_1m + 0.3 * ex_3m + 0.3 * ex_6m
        basis = "excess_vs_" + TECH_BENCHMARK
    else:
        momentum_score = (0.4 * mom_1m + 0.3 * mom_3m + 0.3 * mom_6m).iloc[-1]
        basis = "absolute"

    # Volume confirmation
    volume_confirmation = volume_momentum.iloc[-1] > 1.0

    if momentum_score > 0.05 and volume_confirmation:
        signal = "bullish"
        confidence = min(abs(momentum_score) * 5, 1.0)
    elif momentum_score < -0.05 and volume_confirmation:
        signal = "bearish"
        confidence = min(abs(momentum_score) * 5, 1.0)
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "momentum_1m": safe_metric(mom_1m.iloc[-1]),
            "momentum_3m": safe_metric(mom_3m.iloc[-1]),
            "momentum_6m": safe_metric(mom_6m.iloc[-1]),
            "momentum_score": safe_metric(momentum_score),
            "momentum_basis": basis,
            "volume_momentum": safe_metric(volume_momentum.iloc[-1]),
        },
    }


def calculate_volatility_signals(prices_df):
    """
    Volatility-based trading strategy
    """
    # Calculate various volatility metrics
    returns = prices_df["close"].pct_change()

    # Historical volatility
    hist_vol = returns.rolling(21).std() * math.sqrt(252)

    # Volatility regime detection
    vol_ma = hist_vol.rolling(63).mean()
    vol_regime = hist_vol / vol_ma

    # Volatility mean reversion
    vol_z_score = (hist_vol - vol_ma) / hist_vol.rolling(63).std()

    # ATR ratio
    atr = calculate_atr(prices_df)
    atr_ratio = atr / prices_df["close"]

    # Generate signal based on volatility regime
    current_vol_regime = vol_regime.iloc[-1]
    vol_z = vol_z_score.iloc[-1]

    if current_vol_regime < 0.8 and vol_z < -1:
        signal = "bullish"  # Low vol regime, potential for expansion
        confidence = min(abs(vol_z) / 3, 1.0)
    elif current_vol_regime > 1.2 and vol_z > 1:
        signal = "bearish"  # High vol regime, potential for contraction
        confidence = min(abs(vol_z) / 3, 1.0)
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "historical_volatility": safe_float(hist_vol.iloc[-1]),
            "volatility_regime": safe_float(current_vol_regime),
            "volatility_z_score": safe_float(vol_z),
            "atr_ratio": safe_float(atr_ratio.iloc[-1]),
        },
    }


def calculate_stat_arb_signals(prices_df):
    """
    Statistical arbitrage signals based on price action analysis
    """
    # Calculate price distribution statistics
    returns = prices_df["close"].pct_change()

    # Skewness and kurtosis
    skew = returns.rolling(63).skew()
    kurt = returns.rolling(63).kurt()

    # Test for mean reversion using Hurst exponent
    hurst = calculate_hurst_exponent(prices_df["close"])

    # Correlation analysis
    # (would include correlation with related securities in real implementation)

    # Generate signal based on statistical properties
    # I1.1 守卫:hurst 计算失败(None)或偏度退化(NaN)→ 数据不足。
    # 旧路径 NaN hurst 被 safe_float 洗成 0.0,而 0.0<0.4 恒真,
    # 可能配合偏度产出 (0.5-0)*2=1.0 的满置信假方向信号。
    if hurst is None or pd.isna(skew.iloc[-1]):
        return {
            "signal": "neutral",
            "confidence": 0.2,
            "metrics": {
                "hurst_exponent": safe_metric(hurst),
                "skewness": safe_metric(skew.iloc[-1]),
                "kurtosis": safe_metric(kurt.iloc[-1]),
                "data_gaps": [n for n, v in (
                    ("hurst_exponent", hurst), ("skewness", skew.iloc[-1]),
                ) if v is None or pd.isna(v)],
            },
        }
    if hurst < 0.4 and skew.iloc[-1] > 1:
        signal = "bullish"
        confidence = (0.5 - hurst) * 2
    elif hurst < 0.4 and skew.iloc[-1] < -1:
        signal = "bearish"
        confidence = (0.5 - hurst) * 2
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "hurst_exponent": safe_metric(hurst),
            "skewness": safe_metric(skew.iloc[-1]),
            "kurtosis": safe_metric(kurt.iloc[-1]),
        },
    }


def apply_regime_gate(weights: dict, adx) -> dict:
    """Fix C(TECH_FIX3_V1): ADX>25 强趋势态 → mean_reversion 权重 ×0.3。
    延伸趋势里 MR 的 bearish 是"涨得越猛越看空"的反向器, 降权不禁言(旁证保留)。"""
    w = dict(weights)
    try:
        if adx is not None and not pd.isna(adx) and float(adx) > MR_ADX_GATE:
            w["mean_reversion"] = w["mean_reversion"] * MR_TREND_RELIABILITY
    except (TypeError, ValueError):
        pass
    return w


def weighted_signal_combination(signals, weights):
    """v2(TECH_FIX3_V1): 中性不入分母(C3) + breadth 防单策略满置信。
    raw = Σ dir·w·conf / Σ w·conf(仅方向策略);
    breadth = 方向策略权重占比; effective = raw × breadth;
    signal 阈值 ±0.2 作用于 effective, confidence = |effective|。
    全中性 → raw=0 → 中性@0(诚实: 无方向证据, 而非"半信半疑")。"""
    signal_values = {"bullish": 1, "neutral": 0, "bearish": -1}

    num = 0.0
    den = 0.0
    w_dir = 0.0
    w_all = 0.0
    for strategy, signal in signals.items():
        numeric_signal = signal_values[signal["signal"]]
        weight = weights[strategy]
        confidence = signal["confidence"]
        w_all += weight
        if numeric_signal != 0:
            num += numeric_signal * weight * confidence
            den += weight * confidence
            w_dir += weight

    raw = (num / den) if den > 0 else 0.0
    breadth = (w_dir / w_all) if w_all > 0 else 0.0
    effective = raw * breadth

    if effective > 0.2:
        signal = "bullish"
    elif effective < -0.2:
        signal = "bearish"
    else:
        signal = "neutral"

    return {"signal": signal, "confidence": abs(effective),
            "raw_score": raw, "breadth": breadth}


def _fetch_benchmark_closes(start_iso: str, end_iso: str):
    """Fix B 基准链: tushare index_daily(复用 peer_compare) → baostock sh.000300(共享会话)。
    全失败 → None(动量降级绝对口径)。每条退出路径必留 WARNING 面包屑。"""
    try:
        try:
            from src.analysis import peer_compare as pc
        except ImportError:
            import peer_compare as pc  # type: ignore
        rows = pc._index_series("index_daily", TECH_BENCHMARK,
                                start_iso.replace("-", ""), end_iso.replace("-", ""))
        if rows:
            s = pd.Series({d: float(c) for d, c in rows if c}).sort_index()
            s = s[s > 0]
            if len(s) >= 130:
                return s
            _logger.warning("technicals: tushare 基准序列过短(%d 根), 试 baostock", len(s))
        else:
            _logger.warning("technicals: tushare 基准序列为空, 试 baostock")
    except Exception as exc:
        _logger.warning("technicals: tushare 基准链失败: %s", str(exc)[:120])
    try:
        import sys as _sys
        _bsd = (_sys.modules.get("tools.baostock_data")
                or _sys.modules.get("src.tools.baostock_data"))
        if _bsd is None:
            try:
                from tools import baostock_data as _bsd  # type: ignore
            except ImportError:
                from src.tools import baostock_data as _bsd  # type: ignore
        import baostock as bs
        with _bsd._BS_LOCK:
            if not _bsd._ensure_login():
                raise RuntimeError("baostock 登录失败(共享会话)")
            rs = bs.query_history_k_data_plus(
                "sh.000300", "date,close", start_date=start_iso, end_date=end_iso,
                frequency="d", adjustflag="1")
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
        if rows:
            s = pd.Series({r[0]: float(r[1]) for r in rows if r[1]}).sort_index()
            s = s[s > 0]
            if len(s):
                return s
        _logger.warning("technicals: baostock 基准序列为空")
    except Exception as exc:
        _logger.warning("technicals: baostock 基准链失败: %s", str(exc)[:120])
    _logger.warning("technicals: 基准 %s 不可得, 动量降级为绝对口径", TECH_BENCHMARK)
    return None


def _bench_momentum(bench_closes) -> dict | None:
    """基准 21/63/126 交易日累计收益; 任一窗口不可算 → None(整体绝对兜底)。"""
    if bench_closes is None or len(bench_closes) == 0:
        return None
    r = bench_closes.pct_change()
    out = {}
    for w in (21, 63, 126):
        v = r.rolling(w).sum().iloc[-1] if len(r) > w else float("nan")
        if pd.isna(v):
            _logger.warning("technicals: 基准 %d 日窗口不可算(序列 %d 根), 动量降级为绝对口径",
                            w, len(bench_closes))
            return None
        out[w] = float(v)
    return out


def normalize_pandas(obj):
    """Convert pandas Series/DataFrames to primitive Python types"""
    if isinstance(obj, pd.Series):
        return obj.tolist()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict("records")
    elif isinstance(obj, dict):
        return {k: normalize_pandas(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [normalize_pandas(item) for item in obj]
    return obj


def calculate_rsi(prices_df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = prices_df["close"].diff()
    gain = (delta.where(delta > 0, 0)).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_bollinger_bands(prices_df: pd.DataFrame, window: int = 20) -> tuple[pd.Series, pd.Series]:
    sma = prices_df["close"].rolling(window).mean()
    std_dev = prices_df["close"].rolling(window).std()
    upper_band = sma + (std_dev * 2)
    lower_band = sma - (std_dev * 2)
    return upper_band, lower_band


def calculate_ema(df: pd.DataFrame, window: int) -> pd.Series:
    """
    Calculate Exponential Moving Average

    Args:
        df: DataFrame with price data
        window: EMA period

    Returns:
        pd.Series: EMA values
    """
    return df["close"].ewm(span=window, adjust=False).mean()


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Calculate Average Directional Index (ADX)

    Args:
        df: DataFrame with OHLC data
        period: Period for calculations

    Returns:
        DataFrame with ADX values
    """
    # Calculate True Range
    df["high_low"] = df["high"] - df["low"]
    df["high_close"] = abs(df["high"] - df["close"].shift())
    df["low_close"] = abs(df["low"] - df["close"].shift())
    df["tr"] = df[["high_low", "high_close", "low_close"]].max(axis=1)

    # Calculate Directional Movement
    df["up_move"] = df["high"] - df["high"].shift()
    df["down_move"] = df["low"].shift() - df["low"]

    df["plus_dm"] = np.where((df["up_move"] > df["down_move"]) & (df["up_move"] > 0), df["up_move"], 0)
    df["minus_dm"] = np.where((df["down_move"] > df["up_move"]) & (df["down_move"] > 0), df["down_move"], 0)

    # Calculate ADX
    df["+di"] = 100 * (df["plus_dm"].ewm(span=period).mean() / df["tr"].ewm(span=period).mean())
    df["-di"] = 100 * (df["minus_dm"].ewm(span=period).mean() / df["tr"].ewm(span=period).mean())
    df["dx"] = 100 * abs(df["+di"] - df["-di"]) / (df["+di"] + df["-di"])
    df["adx"] = df["dx"].ewm(span=period).mean()

    return df[["adx", "+di", "-di"]]


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range

    Args:
        df: DataFrame with OHLC data
        period: Period for ATR calculation

    Returns:
        pd.Series: ATR values
    """
    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)

    return true_range.rolling(period).mean()


def calculate_hurst_exponent(price_series: pd.Series, max_lag: int = 20) -> float:
    """
    Calculate Hurst Exponent to determine long-term memory of time series
    H < 0.5: Mean reverting series
    H = 0.5: Random walk
    H > 0.5: Trending series

    Args:
        price_series: Array-like price data
        max_lag: Maximum lag for R/S calculation

    Returns:
        float: Hurst exponent
    """
    # I1.1:数据不足以支撑 R/S 估计 → None(禁止假装 0.5 随机游走)
    if price_series is None or len(price_series) < max_lag + 2:
        return None
    lags = range(2, max_lag)
    # Add small epsilon to avoid log(0)
    tau = [max(1e-8, np.sqrt(np.std(np.subtract(price_series[lag:], price_series[:-lag])))) for lag in lags]

    # Return the Hurst exponent from linear fit
    try:
        reg = np.polyfit(np.log(lags), np.log(tau), 1)
        h = float(reg[0])  # Hurst exponent is the slope
        return h if math.isfinite(h) else None
    except (ValueError, RuntimeWarning, TypeError):
        # 计算失败 → None(旧版返回 0.5 假装随机游走,同属退化值冒充)
        return None
