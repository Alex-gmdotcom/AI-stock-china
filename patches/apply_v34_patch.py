#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v3.4 补丁应用器 — 修复个股分析"全员中性 + No valid trade available"。

根因（对照原版 virattt/ai-hedge-fund 源码逐行复盘的结论）:
  原版 risk_manager 用 get_prices 取价 → 取不到则 current_price=0、
  remaining_position_limit=0 → 原版 portfolio_manager 的
  compute_allowed_actions 只剩 hold → 预填
  hold/0/100%/"No valid trade available"，根本不进 LLM。
  而 api_china 的 _fetch_hk_prices 只用东财 stock_hk_hist（push2his 子域，
  正是被系统代理劫持/海外不稳的那一类），单源失败 = 整条决策链短路。

本补丁:
  src/tools/api_china.py
    E1  港股价格: stock_hk_hist(东财) → stock_hk_daily(新浪) 回退
    E2  A股价格: stock_zh_a_hist(东财) → stock_zh_a_daily(新浪) 回退
  src/web_app.py
    E3  /api/analyze 响应注入 conclusions（强制结论层，服务端）
    E4  前端渲染结论段（交易决策之后）

配套（整文件覆盖，不在本脚本内）:
  src/tools/line_items_china.py  新增 —— buffett/taleb 中性的另一根因:
      bridge 对 CN/HK 的 search_line_items 一律返回空
  src/tools/api_bridge.py        v3.4 —— search_line_items 路由到中国实现
  src/utils/decision_summary.py  v2 —— 纯文本版 + "No valid trade"绊线诊断

每文件原子化；备份 .bak4；精确匹配否则不动文件；幂等。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# ════════════ api_china: 价格多源回退 ════════════

CN_PRICES_OLD = '''def _fetch_cn_prices(ak, info: TickerInfo, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch A-share daily prices via AKShare."""
    # ak.stock_zh_a_hist returns: 日期,开盘,收盘,最高,最低,成交量,成交额,...
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    df = ak.stock_zh_a_hist(
        symbol=info.code,
        period="daily",
        start_date=start_fmt,
        end_date=end_fmt,
        adjust="qfq",  # 前复权 (forward-adjusted)
    )
    return df'''

CN_PRICES_NEW = '''def _filter_price_range(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """v3.4: 对返回全量历史的备用源（新浪）按日期区间过滤；date 可能在列或索引。"""
    if df is None or df.empty:
        return df
    if "date" not in df.columns and "日期" not in df.columns:
        df = df.reset_index()
    col = "date" if "date" in df.columns else ("日期" if "日期" in df.columns else None)
    if col is None:
        return df
    ds = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
    return df.loc[(ds >= start_date) & (ds <= end_date)]


def _fetch_cn_prices(ak, info: TickerInfo, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch A-share daily prices via AKShare.

    v3.4: 东财 stock_zh_a_hist（push2his 子域，代理/海外环境易失败）→
    新浪 stock_zh_a_daily 回退。价格链断裂会让 risk_manager 的
    current_price=0，进而把 portfolio_manager 确定性短路成
    hold/0/100%/"No valid trade available"。
    """
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    try:
        df = ak.stock_zh_a_hist(
            symbol=info.code,
            period="daily",
            start_date=start_fmt,
            end_date=end_fmt,
            adjust="qfq",  # 前复权 (forward-adjusted)
        )
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.debug("stock_zh_a_hist failed for %s: %s", info.code, e)

    # 新浪回退（symbol 需 sh/sz 前缀；英文列 date/open/high/low/close/volume）
    prefix = "sh" if info.code[0] in ("6", "9", "5") else "sz"
    sina_symbol = f"{prefix}{info.code}"
    for kwargs in (
        {"symbol": sina_symbol, "start_date": start_fmt, "end_date": end_fmt, "adjust": "qfq"},
        {"symbol": sina_symbol, "adjust": "qfq"},
        {"symbol": sina_symbol},
    ):
        try:
            df = ak.stock_zh_a_daily(**kwargs)
            if df is not None and not df.empty:
                logger.info("A股价格回退新浪源: %s", info.code)
                return _filter_price_range(df, start_date, end_date)
        except Exception as e:
            logger.debug("stock_zh_a_daily %s failed: %s", list(kwargs), e)
    return pd.DataFrame()'''

HK_PRICES_OLD = '''def _fetch_hk_prices(ak, info: TickerInfo, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch HK stock daily prices via AKShare."""
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    df = ak.stock_hk_hist(
        symbol=info.code,
        period="daily",
        start_date=start_fmt,
        end_date=end_fmt,
        adjust="qfq",
    )
    return df'''

HK_PRICES_NEW = '''def _fetch_hk_prices(ak, info: TickerInfo, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch HK stock daily prices via AKShare.

    v3.4: 东财 stock_hk_hist → 新浪 stock_hk_daily 回退（全量历史，区间过滤）。
    00148.HK "No valid trade available" 案例的直接根因即此处单源失败。
    """
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    try:
        df = ak.stock_hk_hist(
            symbol=info.code,
            period="daily",
            start_date=start_fmt,
            end_date=end_fmt,
            adjust="qfq",
        )
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.debug("stock_hk_hist failed for %s: %s", info.code, e)

    # 新浪回退（英文列 date/open/high/low/close/volume，返回全量历史）
    for kwargs in ({"symbol": info.code, "adjust": "qfq"}, {"symbol": info.code}):
        try:
            df = ak.stock_hk_daily(**kwargs)
            if df is not None and not df.empty:
                logger.info("港股价格回退新浪源: %s", info.code)
                return _filter_price_range(df, start_date, end_date)
        except Exception as e:
            logger.debug("stock_hk_daily %s failed: %s", list(kwargs), e)
    return pd.DataFrame()'''

# ════════════ web_app: 结论层 ════════════

WEB_SERVER_OLD = '''            model_name=model_name,
            model_provider=model_provider,
        )
        return result'''

WEB_SERVER_NEW = '''            model_name=model_name,
            model_provider=model_provider,
        )

        # v3.4: 强制结论层 —— 区分"数据不足型中性"与真实观点，
        # 杜绝裸 hold/0/100% 输出
        try:
            from src.utils.decision_summary import build_conclusions
            result["conclusions"] = build_conclusions(result, [info.full_ticker])
        except Exception:
            result["conclusions"] = {}
        return result'''

WEB_JS_OLD = r"      out += '\\n═══ 各分析师信号 ═══\\n\\n';"

WEB_JS_NEW = r"""      if(d.conclusions){
        for(const txt of Object.values(d.conclusions)){
          out += '\\n' + txt + '\\n';
        }
      }
      out += '\\n═══ 各分析师信号 ═══\\n\\n';"""

FILE_PATCHES = {
    "src/tools/api_china.py": [
        ("E1", "A股价格新浪回退", "_filter_price_range", CN_PRICES_OLD, CN_PRICES_NEW),
        ("E2", "港股价格新浪回退", "stock_hk_daily", HK_PRICES_OLD, HK_PRICES_NEW),
    ],
    "src/web_app.py": [
        ("E3", "服务端注入结论层", "build_conclusions", WEB_SERVER_OLD, WEB_SERVER_NEW),
        ("E4", "前端渲染结论段", "d.conclusions", WEB_JS_OLD, WEB_JS_NEW),
    ],
}


def apply_file(path: Path, patches) -> bool:
    if not path.exists():
        print(f"[跳过] {path} 不存在")
        return False
    text = path.read_text(encoding="utf-8")
    original = text
    applied, skipped, failed = [], [], []
    for pid, desc, marker, old, new in patches:
        if marker in text:
            skipped.append(pid)
            continue
        n = text.count(old)
        if n == 1:
            text = text.replace(old, new)
            applied.append(pid)
        else:
            failed.append((pid, f"原文匹配到 {n} 处（期望1处）: {desc}"))
    if failed:
        print(f"[中止] {path} 以下补丁无法应用，该文件未修改：")
        for pid, why in failed:
            print(f"  ✗ {pid}: {why}")
        return False
    if text != original:
        bak = path.with_suffix(".py.bak4")
        shutil.copy2(path, bak)
        path.write_text(text, encoding="utf-8")
        print(f"[备份] {bak}")
    print(f"[完成] {path.name}: 已应用 {', '.join(applied) or '无'}"
          f" | 跳过 {', '.join(skipped) or '无'}")
    return True


def main():
    ok = True
    for rel, patches in FILE_PATCHES.items():
        ok = apply_file(Path(rel), patches) and ok
    if not ok:
        sys.exit(1)
    print("\n完成。验证: poetry run python src/main_china.py --ticker 00148.HK")


if __name__ == "__main__":
    main()
