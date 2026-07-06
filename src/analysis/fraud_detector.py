# -*- coding: utf-8 -*-
"""
analysis/fraud_detector.py — 财务舞弊检测 (Phase 3 Step 13, v1.0.0)
========================================================================
规格: TECH v1.1 §8.2 / PRODUCT F10 / D3(五指标固化) / I1.5(警示档必列具体项)
     / I1.1(缺数据显式标注) / I6.1(LLM fail-loud) / I10.4(asof PIT)

设计裁决(两处,待 Alex 认可即入 v1.2 增量):
  1. 档位与 findings 为**确定性计算**,LLM 仅生成中文 summary 文案、不改档位
     (§8.2 原文"不改变档位结论");LLM 失败 → summary 显式标注失败 +
     llm_summary_failed=True(fail-loud 语义 = 可见,不静默冒充成功),
     确定性结果不陪葬。
  2. level 增加 "unknown" 档:数据不足(如港股 tushare 无三大报表)时
     healthy/watch/alert 三档任何一档都是编造 → 违 I1.1。规格 3.6 的
     三档 Literal 与 I1.1 冲突,取 I1.1(不变量优先于数据模型枚举)。

D3 五指标(阈值 config 可调,env AIHF_FRAUD_* 覆盖):
  1. 5年累计经营现金流/累计净利 <0.7 watch,<0.4 alert
  2. 应收增速 − 营收增速(最新年报 YoY)>20pp watch,>50pp alert
  3. 存货增速 − 营收增速 >30pp watch,>60pp alert
  4. 商誉/净资产 >30% watch,>50% alert
  5. 关联交易占比 — 2000积分档无稳定接口 → 永远显式【数据缺口】,禁硬凑

图外 call_llm 姿势 = Step 11 定谳模式:state 桩 + agent_name **双非空**,
哨兵 default_factory 检测重试耗尽的静默默认对象。
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)
__version__ = "1.0.0"

# 阈值(D3 固化值为默认;env 可调但调整属 config 语义非拍脑袋改规格)
_T = lambda k, v: float(os.environ.get(k, v))
TH_OCF_NI_WATCH = _T("AIHF_FRAUD_OCFNI_WATCH", "0.7")
TH_OCF_NI_ALERT = _T("AIHF_FRAUD_OCFNI_ALERT", "0.4")
TH_AR_WATCH = _T("AIHF_FRAUD_AR_WATCH", "20")      # pp
TH_AR_ALERT = _T("AIHF_FRAUD_AR_ALERT", "50")
TH_INV_WATCH = _T("AIHF_FRAUD_INV_WATCH", "30")
TH_INV_ALERT = _T("AIHF_FRAUD_INV_ALERT", "60")
TH_GW_WATCH = _T("AIHF_FRAUD_GW_WATCH", "30")      # %
TH_GW_ALERT = _T("AIHF_FRAUD_GW_ALERT", "50")
N_YEARS = 5

_BAL_F = ("ts_code,end_date,f_ann_date,accounts_receiv,inventories,"
          "goodwill,total_hldr_eqy_exc_min_int")
_INC_F = "ts_code,end_date,f_ann_date,revenue,n_income"
_CF_F = "ts_code,end_date,f_ann_date,n_cashflow_act"


class FraudFinding(BaseModel):
    indicator: str
    observed: str
    threshold: str
    severity: Literal["info", "watch", "alert"]


class FraudCheckResult(BaseModel):
    ticker: str
    asof: str
    level: Literal["healthy", "watch", "alert", "unknown"]   # unknown=数据不足(裁决2)
    findings: list[FraudFinding] = []
    summary: str = ""
    llm_summary_failed: bool = False
    periods_used: list[str] = []          # 参与计算的年报期
    data_gaps: list[str] = []
    checked_at: str = ""


class _LLMSummary(BaseModel):
    summary: str = ""
    llm_failed: bool = False              # 哨兵位


# ----------------------------------------------------------------------
# 纯计算核心(沙箱可确定性验证)
# ----------------------------------------------------------------------
def _fnum(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None      # NaN 过滤(禁 0.0 冒充,R1-b 同款纪律)
    except (TypeError, ValueError):
        return None


def _yoy(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None or prev <= 0:
        return None
    return (cur / prev - 1.0) * 100.0


def compute_findings(annuals: list[dict]) -> tuple[list[FraudFinding], list[str]]:
    """annuals: 年报行降序(最新在前),键 = revenue/n_income/n_cashflow_act/
    accounts_receiv/inventories/goodwill/equity。→ (findings, data_gaps)。
    每个指标要么产出 finding(含 info 档=通过),要么进 data_gaps,绝不静默。"""
    fs: list[FraudFinding] = []
    gaps: list[str] = []

    def add(ind, obs, th, sev):
        fs.append(FraudFinding(indicator=ind, observed=obs, threshold=th, severity=sev))

    # ① 5年累计 OCF / 累计净利
    ocfs = [_fnum(a.get("n_cashflow_act")) for a in annuals[:N_YEARS]]
    nis = [_fnum(a.get("n_income")) for a in annuals[:N_YEARS]]
    pairs = [(o, n) for o, n in zip(ocfs, nis) if o is not None and n is not None]
    if len(pairs) < 3:
        gaps.append(f"OCF/净利匹配度不可算(完整年报仅 {len(pairs)} 期 <3)")
    else:
        s_ocf = sum(o for o, _ in pairs); s_ni = sum(n for _, n in pairs)
        if s_ni <= 0:
            gaps.append(f"OCF/净利匹配度不可算({len(pairs)}年累计净利 ≤0,比率无意义;"
                        "累计亏损本身请结合基本面判断)")
        else:
            r = s_ocf / s_ni
            sev = "alert" if r < TH_OCF_NI_ALERT else ("watch" if r < TH_OCF_NI_WATCH else "info")
            add("经营现金流/净利润", f"{len(pairs)}年累计比率 {r:.2f}",
                f"<{TH_OCF_NI_WATCH} 关注 / <{TH_OCF_NI_ALERT} 警示", sev)

    # ②③ 应收/存货增速 vs 营收增速(最新年报 YoY)
    if len(annuals) >= 2:
        rev_g = _yoy(_fnum(annuals[0].get("revenue")), _fnum(annuals[1].get("revenue")))
        for key, name, w, a in (("accounts_receiv", "应收账款增速 vs 营收", TH_AR_WATCH, TH_AR_ALERT),
                                ("inventories", "存货增速 vs 营收", TH_INV_WATCH, TH_INV_ALERT)):
            g = _yoy(_fnum(annuals[0].get(key)), _fnum(annuals[1].get(key)))
            if rev_g is None or g is None:
                gaps.append(f"{name} 不可算(最新两期年报字段缺失)")
                continue
            diff = g - rev_g
            sev = "alert" if diff > a else ("watch" if diff > w else "info")
            add(name, f"增速 {g:.1f}% − 营收增速 {rev_g:.1f}% = {diff:+.1f}pp",
                f">{w:.0f}pp 关注 / >{a:.0f}pp 警示", sev)
    else:
        gaps.append("应收/存货增速不可算(年报不足两期)")

    # ④ 商誉/净资产(最新年报)
    if annuals:
        gw = _fnum(annuals[0].get("goodwill"))
        eq = _fnum(annuals[0].get("equity"))
        if eq is None or eq <= 0:
            gaps.append("商誉/净资产不可算(净资产缺失或 ≤0)")
        else:
            gw = gw or 0.0                 # 商誉科目空行 = 无商誉,语义为 0 非缺口
            pct = gw / eq * 100.0
            sev = "alert" if pct > TH_GW_ALERT else ("watch" if pct > TH_GW_WATCH else "info")
            add("商誉占净资产", f"{pct:.1f}%",
                f">{TH_GW_WATCH:.0f}% 关注 / >{TH_GW_ALERT:.0f}% 警示", sev)

    # ⑤ 关联交易占比 — 无稳定数据源(D3 已知缺口,禁硬凑)
    gaps.append("关联交易占比【数据缺口】(2000积分档无稳定接口,v1 不硬凑)")
    return fs, gaps


def decide_level(findings: list[FraudFinding], had_data: bool) -> str:
    if not had_data:
        return "unknown"
    sevs = {f.severity for f in findings}
    if "alert" in sevs:
        return "alert"
    if "watch" in sevs:
        return "watch"
    return "healthy" if findings else "unknown"


# ----------------------------------------------------------------------
# 数据适配层
# ----------------------------------------------------------------------
def _tsd():
    try:
        from src.tools import tushare_data as t
    except ImportError:
        from tools import tushare_data as t   # type: ignore
    return t


def _annual_rows(norm: str, asof: str) -> tuple[list[dict], list[str]]:
    """三大报表年报行(end_date=1231),f_ann_date≤asof(PIT),同期取最早公告。"""
    t = _tsd()
    if not t.available():
        return [], ["tushare 不可用(缺 token 或未装包),三大报表不可取"]
    asof_c = asof.replace("-", "")
    start = (datetime.strptime(asof, "%Y-%m-%d") - timedelta(days=365 * (N_YEARS + 2))
             ).strftime("%Y%m%d")

    def pull(method: str, fields: str) -> dict[str, dict]:
        df = t._query(method, ts_code=norm, start_date=start, end_date=asof_c, fields=fields)
        out: dict[str, dict] = {}
        if df is None or getattr(df, "empty", True):
            return out
        for _, row in df.iterrows():
            ed = str(row.get("end_date") or "")
            fann = str(row.get("f_ann_date") or "")
            if not ed.endswith("1231") or (fann and fann > asof_c):
                continue
            if ed in out and str(out[ed].get("f_ann_date")) <= fann:
                continue                    # 同期保留最早公告(防更正稿前视)
            out[ed] = {c: row.get(c) for c in row.index}
        return out

    try:
        bal = pull("balancesheet", _BAL_F)
        inc = pull("income", _INC_F)
        cf = pull("cashflow", _CF_F)
    except Exception as exc:
        logger.warning("fraud_detector %s: 报表拉取失败: %s", norm, exc)
        return [], [f"三大报表拉取失败: {str(exc)[:80]}"]

    periods = sorted(set(bal) | set(inc) | set(cf), reverse=True)[:N_YEARS + 1]
    rows = []
    for p in periods:
        b, i, c = bal.get(p, {}), inc.get(p, {}), cf.get(p, {})
        rows.append({
            "end_date": p,
            "revenue": i.get("revenue"), "n_income": i.get("n_income"),
            "n_cashflow_act": c.get("n_cashflow_act"),
            "accounts_receiv": b.get("accounts_receiv"),
            "inventories": b.get("inventories"),
            "goodwill": b.get("goodwill"),
            "equity": b.get("total_hldr_eqy_exc_min_int"),
        })
    return rows, []


# ----------------------------------------------------------------------
# LLM summary(仅文案,Step 11 双非空姿势)
# ----------------------------------------------------------------------
def _model_state() -> Optional[dict]:
    model = os.environ.get("AIHF_FRAUD_MODEL", "").strip()
    provider = os.environ.get("AIHF_FRAUD_PROVIDER", "").strip()
    if not (model and provider):
        if os.environ.get("DEEPSEEK_API_KEY", "").strip():
            model, provider = "deepseek-v4-flash", "DeepSeek"
        else:
            return None                    # 无 LLM 可用 → summary 显式跳过
    return {"metadata": {"model_name": model, "model_provider": provider}}


def _llm_summary(ticker: str, level: str, findings: list[FraudFinding],
                 gaps: list[str]) -> tuple[str, bool]:
    """→ (summary, failed)。失败显式标注,绝不改档位、绝不静默冒充成功。"""
    st = _model_state()
    if st is None:
        return "[LLM summary 跳过: 未配置 LLM(缺 DEEPSEEK_API_KEY);档位为确定性计算不受影响]", True
    lines = [f"- [{f.severity}] {f.indicator}: {f.observed}(阈值 {f.threshold})" for f in findings]
    lines += [f"- [缺口] {g}" for g in gaps]
    prompt = (f"你是财务分析助手。以下是 {ticker} 财务舞弊检测的确定性指标结果,"
              f"综合档位={level}。请用 2-3 句中文概括(不要复述全部数字,点出最关键的 1-2 项;"
              "档位结论不得与给定档位矛盾)。只输出 JSON: {\"summary\": \"...\"}\n\n"
              + "\n".join(lines))
    try:
        try:
            from src.utils.llm import call_llm
        except ImportError:
            from utils.llm import call_llm  # type: ignore
        r = call_llm(prompt=prompt, pydantic_model=_LLMSummary,
                     agent_name="fraud_detector", state=st,      # 双非空(Step 11 定谳)
                     default_factory=lambda: _LLMSummary(llm_failed=True))
        if r is None or getattr(r, "llm_failed", False) or not r.summary.strip():
            return "[LLM summary 生成失败(重试耗尽);档位与指标为确定性计算,不受影响]", True
        return r.summary.strip()[:300], False
    except Exception as exc:
        logger.warning("fraud_detector %s: LLM summary 异常: %s", ticker, exc)
        return f"[LLM summary 异常: {str(exc)[:60]};档位为确定性计算,不受影响]", True


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def check(norm: str, asof: Optional[str] = None, with_llm: bool = True) -> FraudCheckResult:
    """F10 主入口。港股/数据不足 → level=unknown + 显式缺口,fail-soft。"""
    asof = asof or date.today().isoformat()
    res = FraudCheckResult(ticker=norm, asof=asof, level="unknown",
                           checked_at=datetime.now().isoformat(timespec="seconds"))
    if norm.upper().endswith(".HK"):
        res.data_gaps.append("港股三大报表 tushare 不覆盖(v1),舞弊检测不可用")
        res.summary = "港股数据不足,无法评估"
        logger.info("fraud_detector %s: 港股短路 unknown", norm)
        return res

    annuals, gaps0 = _annual_rows(norm, asof)
    res.data_gaps.extend(gaps0)
    if not annuals:
        if not gaps0:
            res.data_gaps.append("年报零记录(次新股或接口不覆盖)")
        res.summary = "年报数据不足,无法评估"
        logger.warning("fraud_detector %s: 无年报数据 → unknown", norm)
        return res

    res.periods_used = [a["end_date"] for a in annuals]
    findings, gaps = compute_findings(annuals)
    res.findings = findings
    res.data_gaps.extend(gaps)
    res.level = decide_level(findings, had_data=True)

    if with_llm:
        res.summary, res.llm_summary_failed = _llm_summary(norm, res.level, findings, gaps)
    else:
        res.summary = f"档位 {res.level}(确定性计算,未生成 LLM 文案)"
    logger.info("fraud_detector %s: level=%s findings=%d gaps=%d periods=%s",
                norm, res.level, len(findings), len(res.data_gaps),
                ",".join(res.periods_used[:3]))
    return res
