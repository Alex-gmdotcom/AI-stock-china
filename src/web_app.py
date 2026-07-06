"""
可视化 Web 界面 — AI Hedge Fund 中国版（Part B UI 版）

浏览器操作：
  - 个股 AI 分析（选择分析师组合）+ K线图（红涨绿跌 / MA5·20·60）
  - 三分法观察池查看
  - 股市早晚报入库（粘贴/文本）

启动：poetry run python src/web_app.py  →  http://localhost:8000
默认 DeepSeek（.env 中 DEEPSEEK_API_KEY）。
"""
from __future__ import annotations

# 进程级 NO_PROXY 注入必须在任何 AKShare 调用之前（海外/TUN 代理隔离）
try:
    from src.markets.proxy import inject_no_proxy
    inject_no_proxy()
except Exception:
    try:
        from markets.proxy import inject_no_proxy  # type: ignore
        inject_no_proxy()
    except Exception:
        pass

import os
import traceback
from datetime import datetime, timedelta

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="AI Hedge Fund China — Web UI")


def default_model() -> tuple[str, str]:
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek-v4-flash", "DeepSeek"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude-sonnet-4-20250514", "Anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "gpt-4.1", "OpenAI"
    return "deepseek-v4-flash", "DeepSeek"


# ════════════════════════════════════════════
# API 端点
# ════════════════════════════════════════════

class AnalyzeRequest(BaseModel):
    ticker: str
    analysts: list[str] | None = None
    model_name: str | None = None
    model_provider: str | None = None


class BriefingIngestRequest(BaseModel):
    text: str


@app.get("/api/pool")
def get_pool():
    """获取三分法观察池"""
    try:
        from src.strategy.three_categories import ThreeCategoryPool
        return ThreeCategoryPool().to_json_for_frontend()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/kline")
def get_kline(ticker: str, days: int = 120):
    """个股 K 线 + MA5/20/60（复用已修好的 get_prices，A股东财失败自动走新浪）。"""
    try:
        from src.tools.api_china import get_prices
        from src.markets.ticker import parse_ticker
        info = parse_ticker(ticker)
        end = datetime.now()
        start = end - timedelta(days=int((days + 75) * 1.5))
        prices = get_prices(info.full_ticker, start.strftime("%Y-%m-%d"),
                            end.strftime("%Y-%m-%d"))
        if not prices:
            return {"error": "无K线数据（东财+新浪均未返回，检查网络/是否交易日）",
                    "ticker": info.full_ticker}
        closes = [float(p.close) for p in prices]

        def ma(n, i):
            if i + 1 < n:
                return None
            return round(sum(closes[i + 1 - n:i + 1]) / n, 3)

        rows = []
        for i, p in enumerate(prices):
            rows.append({
                "t": p.time, "o": float(p.open), "h": float(p.high),
                "l": float(p.low), "c": float(p.close), "v": int(p.volume),
                "ma5": ma(5, i), "ma20": ma(20, i), "ma60": ma(60, i),
            })
        rows = rows[-int(days):]
        return {"ticker": info.full_ticker, "rows": rows}
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()},
                            status_code=500)


@app.post("/api/briefing/ingest")
def ingest_briefing_route(req: BriefingIngestRequest):
    """早晚报入库 + LLM 标的抽取(F3/I2.2)。归档成功与抽取成败解耦(I2.1)。"""
    try:
        if not (req.text or "").strip():
            return JSONResponse({"error": "正文为空"}, status_code=400)
        from src.briefings_archive.ingest import ingest_briefing_text
        from src.briefings_archive.storage import BriefingStorage
        b = ingest_briefing_text(req.text, source="web_paste")
        BriefingStorage().save(b, overwrite=True)
        bt = b.briefing_type.value if hasattr(b.briefing_type, "value") else str(b.briefing_type)
        resp = {"id": b.briefing_id, "type": bt, "date": str(b.briefing_date),
                "tickers": list(getattr(b.metadata, "tickers_mentioned", []) or [])}
        # ── LLM 标的抽取(归档已成功,抽取失败只降级不回滚) ──
        try:
            from src.analysis.ticker_extractor import extract_tickers, TickerExtractionFailed
            try:
                ext = extract_tickers(req.text, bt)
                _save_extraction(b.briefing_id, "llm",
                                 [t.model_dump() for t in ext], None)
                resp["extraction"] = {"method": "llm", "failed": False,
                                      "tickers": [t.model_dump() for t in ext]}
            except TickerExtractionFailed as ee:
                _save_extraction(b.briefing_id, "failed", [], str(ee))
                resp["extraction"] = {"method": "manual_fallback", "failed": True,
                                      "error": str(ee),
                                      "pool_tickers": _pool_tickers()}   # I2.2 手动多选票单
        except ImportError as ie:
            resp["extraction"] = {"method": "unavailable", "failed": True,
                                  "error": f"ticker_extractor 不可用: {ie}"}
        return resp
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()},
                            status_code=500)


@app.post("/api/analyze")
def run_analysis(req: AnalyzeRequest):
    """运行个股多 Agent 分析"""
    try:
        from src.main_china import run_china_hedge_fund
        from src.markets.ticker import parse_ticker
        from dateutil.relativedelta import relativedelta

        info = parse_ticker(req.ticker)
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - relativedelta(months=3)).strftime("%Y-%m-%d")

        model_name, model_provider = default_model()
        if req.model_name:
            model_name = req.model_name
        if req.model_provider:
            model_provider = req.model_provider

        portfolio = {
            "cash": 1000000.0, "margin_requirement": 0.5, "margin_used": 0.0,
            "positions": {info.full_ticker: {"long": 0, "short": 0, "long_cost_basis": 0.0,
                          "short_cost_basis": 0.0, "short_margin_used": 0.0}},
            "realized_gains": {info.full_ticker: {"long": 0.0, "short": 0.0}},
        }

        result = run_china_hedge_fund(
            tickers=[info.full_ticker], start_date=start_date, end_date=end_date,
            portfolio=portfolio, show_reasoning=False, selected_analysts=req.analysts,
            model_name=model_name, model_provider=model_provider,
        )
        try:
            from src.utils.decision_summary import build_conclusions
            result["conclusions"] = build_conclusions(result, [info.full_ticker])
        except Exception:
            result["conclusions"] = {}
        return result
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()},
                            status_code=500)


# ════════════════════════════════════════════
# 内嵌前端
# ════════════════════════════════════════════

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Hedge Fund 中国版 — 控制台</title>
<style>
:root{--bg:#0b0d13;--card:#14171f;--border:#232838;--text:#e6e8ef;--dim:#6e7389;
  --accent:#5b7cfa;--up:#e2453e;--down:#1ba784;--mono:ui-monospace,Consolas,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;font-size:14px;min-height:100vh}
.wrap{max-width:1100px;margin:0 auto;padding:28px 20px}
h1{font-size:22px;font-weight:800;letter-spacing:.5px}
.sub{color:var(--dim);font-size:13px;margin:4px 0 24px}
.row{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;flex:1;min-width:300px}
.card h2{font-size:15px;font-weight:700;margin-bottom:12px}
.btn{background:var(--accent);border:none;border-radius:8px;color:#fff;padding:10px 20px;
  cursor:pointer;font-size:14px;font-weight:600;font-family:inherit;transition:opacity .15s}
.btn:hover{opacity:.88}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn.alt{background:none;border:1px solid var(--border);color:var(--text)}
.btn.sm{padding:6px 14px;font-size:13px}
input,select,textarea{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;
  color:var(--text);font-size:14px;font-family:inherit;outline:none;width:100%}
textarea{resize:vertical;min-height:120px;line-height:1.6}
input:focus,select:focus,textarea:focus{border-color:var(--accent)}
label{display:block;color:var(--dim);font-size:12px;margin:10px 0 4px}
.out{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:16px 20px;
  margin-top:14px;white-space:pre-wrap;line-height:1.85;font-size:13.5px;max-height:560px;overflow-y:auto;display:none}
.out.show{display:block}
.out b{color:#9db4ff}
.loading{display:none;align-items:center;gap:10px;color:var(--dim);font-size:13px;margin-top:14px}
.loading.show{display:flex}
.spin{width:18px;height:18px;border:2.5px solid var(--border);border-top:2.5px solid var(--accent);
  border-radius:50%;animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.checks{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px}
.checks label{display:flex;align-items:center;gap:6px;margin:0;font-size:13px;color:var(--text);cursor:pointer}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:5px;font-weight:700;margin-right:6px}
.tag.v{background:rgba(79,143,247,.15);color:#4f8ff7}
.tag.t{background:rgba(247,99,79,.15);color:#f7634f}
.tag.n{background:rgba(232,179,57,.15);color:#e8b339}
.pool-cat{margin-top:12px}
.pool-cat .h{font-size:12px;color:var(--dim);margin-bottom:6px;font-weight:700}
.pool-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:8px}
.pcard{border:1px solid var(--border);border-radius:8px;padding:9px 11px;font-size:12.5px}
.pcard .nm{font-weight:700;font-size:13.5px;margin:2px 0}
.pcard .tk{color:var(--dim);font-family:var(--mono);font-size:10.5px}
.kline-box{margin-top:14px;display:none}
.kline-box.show{display:block}
.kline-legend{font-size:11.5px;color:var(--dim);margin:8px 0 4px}
.kline-legend span{margin-right:14px}
canvas{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:8px}
.note{font-size:11.5px;color:var(--dim);margin-top:8px}
.footer{color:var(--dim);font-size:11.5px;margin-top:28px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI Hedge Fund 中国版</h1>
  <div class="sub">三分法投资研究控制台 · 红涨绿跌 · 无需终端操作</div>

  <div class="row">
    <div class="card">
      <h2>🔍 个股 AI 分析</h2>
      <label>股票代码（如 600519 / 300308 / 00700.HK）</label>
      <input id="ticker" placeholder="600519" value="600519">
      <label>分析师组合</label>
      <div class="checks">
        <label><input type="checkbox" value="china_public_opinion" checked> 舆情+黑天鹅</label>
        <label><input type="checkbox" value="china_policy" checked> 政策解读</label>
        <label><input type="checkbox" value="china_capital_flow" checked> 资金流向</label>
        <label><input type="checkbox" value="china_sector_rotation"> 板块轮动</label>
        <label><input type="checkbox" value="technical_analyst" checked> 技术分析</label>
        <label><input type="checkbox" value="fundamentals_analyst"> 基本面</label>
        <label><input type="checkbox" value="valuation_analyst"> 估值分析</label>
        <label><input type="checkbox" value="warren_buffett"> 巴菲特视角</label>
        <label><input type="checkbox" value="nassim_taleb"> 塔勒布风险</label>
      </div>
      <div style="margin-top:14px;display:flex;gap:10px">
        <button class="btn" id="abtn" onclick="analyze()">▶ 开始分析</button>
        <button class="btn alt" id="kbtn" onclick="loadKline()">📈 看K线</button>
      </div>
      <div class="loading" id="aload"><div class="spin"></div><span>多 Agent 并行分析中，约 2-5 分钟，请勿关闭页面...</span></div>
      <div class="out" id="aout"></div>
      <div class="kline-box" id="kbox">
        <div class="kline-legend">
          <span style="color:var(--up)">█ 阳线(涨)</span><span style="color:var(--down)">█ 阴线(跌)</span>
          <span style="color:#e8b339">— MA5</span><span style="color:#5b7cfa">— MA20</span><span style="color:#c45bff">— MA60</span>
        </div>
        <canvas id="kcanvas" width="1040" height="360"></canvas>
        <div class="note" id="knote"></div>
      </div>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>📊 三分法观察池 <button class="btn sm alt" style="float:right" onclick="loadPool()">⟳ 刷新</button></h2>
      <div id="pool"><span style="color:var(--dim)">加载中...</span></div>
    </div>
  </div>

  <div class="row">
    <div class="card">
      <h2>📰 股市早晚报入库</h2>
      <label>粘贴早报/晚报原文（自动识别日期·类型·标的）</label>
      <textarea id="brief" placeholder="📅 2026年6月20日 早报 ..."></textarea>
      <div style="margin-top:12px"><button class="btn" id="bbtn" onclick="ingestBrief()">📥 入库</button></div>
      <div class="out" id="bout"></div>
    </div>
  </div>

  <div class="footer">本工具为 AI 辅助分析，不构成投资建议 · AI Hedge Fund China Edition</div>
</div>

<script>
const $ = id => document.getElementById(id);
const AGENT_ZH = __AGENT_ZH__;

function fmt(text){
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>');
}
function azh(agent){
  const k = (agent||'').replace(/_agent$/,'');
  return AGENT_ZH[k] || k.replace(/_/g,' ');
}

async function analyze(){
  const ticker = $('ticker').value.trim();
  if(!ticker){ alert('请输入股票代码'); return; }
  const analysts = [...document.querySelectorAll('.checks input:checked')].map(c=>c.value);
  if(!analysts.length){ alert('至少选择一个分析师'); return; }
  $('abtn').disabled = true; $('aload').classList.add('show'); $('aout').classList.remove('show');
  try{
    const r = await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ticker, analysts})});
    const d = await r.json();
    if(d.error){ $('aout').innerHTML='❌ '+fmt(d.error); }
    else{
      let out = '═══ 交易决策 ═══\\n\\n';
      for(const [tk,v] of Object.entries(d.decisions||{})){
        out += `【${tk}】 动作: ${v.action||'-'} | 数量: ${v.quantity??'-'} | 置信度: ${v.confidence??'-'}%\\n理由: ${v.reasoning||'-'}\\n\\n`;
      }
      if(d.conclusions){ for(const txt of Object.values(d.conclusions)){ out += '\\n'+txt+'\\n'; } }
      out += '\\n═══ 各分析师信号 ═══\\n\\n';
      const sig = d.analyst_signals || {};
      for(const [agent,tickers] of Object.entries(sig)){
        if(agent.includes('risk_management')) continue;
        for(const [tk,s] of Object.entries(tickers)){
          if(!s || !s.signal) continue;
          const emoji = s.signal==='bullish'?'🔴看多':s.signal==='bearish'?'🟢看空':'⚪中性';
          out += `${azh(agent)}: ${emoji} (置信度 ${s.confidence??'-'}%)\\n`;
        }
      }
      $('aout').innerHTML = fmt(out);
    }
    $('aout').classList.add('show');
  }catch(e){ $('aout').innerHTML='❌ '+e.message; $('aout').classList.add('show'); }
  $('abtn').disabled = false; $('aload').classList.remove('show');
}

async function loadKline(){
  const ticker = $('ticker').value.trim();
  if(!ticker){ alert('请输入股票代码'); return; }
  $('kbtn').disabled = true; $('knote').textContent = '加载K线中...';
  $('kbox').classList.add('show');
  try{
    const r = await fetch('/api/kline?ticker='+encodeURIComponent(ticker)+'&days=120');
    const d = await r.json();
    if(d.error){ $('knote').textContent = '❌ '+d.error; clearCanvas(); }
    else{ drawKline(d.rows); $('knote').textContent = d.ticker+' · 近'+d.rows.length+'个交易日 · 红涨绿跌'; }
  }catch(e){ $('knote').textContent = '❌ '+e.message; }
  $('kbtn').disabled = false;
}

function clearCanvas(){ const c=$('kcanvas'); c.getContext('2d').clearRect(0,0,c.width,c.height); }

function drawKline(rows){
  const c = $('kcanvas'), ctx = c.getContext('2d');
  const W = c.width, H = c.height, padL=54, padR=12, padT=12, padB=22;
  ctx.clearRect(0,0,W,H);
  if(!rows || !rows.length) return;
  const priceH = H - padT - padB;
  let hi=-1e9, lo=1e9;
  rows.forEach(r=>{ hi=Math.max(hi,r.h); lo=Math.min(lo,r.l); });
  rows.forEach(r=>{ ['ma5','ma20','ma60'].forEach(k=>{ if(r[k]!=null){hi=Math.max(hi,r[k]);lo=Math.min(lo,r[k]);} }); });
  const pad=(hi-lo)*0.06||1; hi+=pad; lo-=pad;
  const x = i => padL + (W-padL-padR) * (i/(rows.length-1||1));
  const y = p => padT + priceH * (1-(p-lo)/(hi-lo||1));
  ctx.strokeStyle='#232838'; ctx.fillStyle='#6e7389'; ctx.font='10px monospace'; ctx.lineWidth=1;
  for(let g=0; g<=4; g++){
    const py=padT+priceH*g/4, pv=hi-(hi-lo)*g/4;
    ctx.beginPath(); ctx.moveTo(padL,py); ctx.lineTo(W-padR,py); ctx.stroke();
    ctx.fillText(pv.toFixed(2), 6, py+3);
  }
  const cw = Math.max(1.5, (W-padL-padR)/rows.length*0.62);
  rows.forEach((r,i)=>{
    const up = r.c>=r.o, col = up?'#e2453e':'#1ba784';
    const xc=x(i);
    ctx.strokeStyle=col; ctx.fillStyle=col; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(xc,y(r.h)); ctx.lineTo(xc,y(r.l)); ctx.stroke();
    const yo=y(r.o), yc=y(r.c), top=Math.min(yo,yc), bh=Math.max(1,Math.abs(yc-yo));
    ctx.fillRect(xc-cw/2, top, cw, bh);
  });
  const maLine=(key,color)=>{
    ctx.strokeStyle=color; ctx.lineWidth=1.3; ctx.beginPath(); let started=false;
    rows.forEach((r,i)=>{ if(r[key]==null) return; const px=x(i),py=y(r[key]);
      if(!started){ctx.moveTo(px,py);started=true;} else ctx.lineTo(px,py); });
    ctx.stroke();
  };
  maLine('ma5','#e8b339'); maLine('ma20','#5b7cfa'); maLine('ma60','#c45bff');
}

async function loadPool(){
  $('pool').innerHTML = '<span style="color:var(--dim)">加载中...</span>';
  try{
    const r = await fetch('/api/pool'); const d = await r.json();
    if(d.error){ $('pool').innerHTML = '<div style="color:#e2453e">❌ 加载失败: '+fmt(d.error)+'</div>'; return; }
    const cats = [['v_pool','V','估值'],['t_pool','T','趋势'],['n_pool','N','叙事']];
    const total = (d.v_pool||[]).length+(d.t_pool||[]).length+(d.n_pool||[]).length;
    if(!total){ $('pool').innerHTML = '<span style="color:var(--dim)">观察池为空 — 运行 seed_pool.py 播种</span>'; return; }
    let html='';
    for(const [key,cls,label] of cats){
      const arr = d[key]||[];
      html += '<div class="pool-cat"><div class="h">'+label+' ('+cls+') · '+arr.length+'/5</div><div class="pool-grid">';
      html += arr.map(e=>'<div class="pcard"><span class="tag '+cls.toLowerCase()+'">'+(e.sub_id||cls)+'</span>'+
        '<div class="nm">'+(e.name||'')+'</div><div class="tk">'+(e.ticker||'')+'</div></div>').join('');
      html += '</div></div>';
    }
    $('pool').innerHTML = html;
  }catch(e){ $('pool').innerHTML = '<div style="color:#e2453e">❌ '+e.message+'</div>'; }
}

async function ingestBrief(){
  const text = $('brief').value.trim();
  if(!text){ alert('请粘贴日报正文'); return; }
  $('bbtn').disabled = true;
  try{
    const r = await fetch('/api/briefing/ingest',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})});
    const d = await r.json();
    if(d.error){ $('bout').innerHTML = '❌ '+fmt(d.error); }
    else{ $('bout').innerHTML = '✅ 已入库\\nID: '+d.id+'\\n类型: '+d.type+'  日期: '+d.date+'\\n抽取标的: '+((d.tickers||[]).join(', ')||'(无)'); }
    $('bout').classList.add('show');
  }catch(e){ $('bout').innerHTML='❌ '+e.message; $('bout').classList.add('show'); }
  $('bbtn').disabled = false;
}

loadPool();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    import json
    try:
        from src.utils.i18n import agent_name_map_json
        amap = agent_name_map_json()
    except Exception:
        amap = {}
    return HTML.replace("__AGENT_ZH__", json.dumps(amap, ensure_ascii=False))



# ════════════════════════════════════════════
# marker: STEP14_BRIEFING_TICKERS_V1 — 入口A 抽取端点 + healthz
# ════════════════════════════════════════════
def _extraction_dir():
    from src.briefings_archive.storage import default_storage_path
    d = default_storage_path() / "extracted_tickers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_extraction(briefing_id: str, method: str, tickers: list, error):
    import json as _json
    from datetime import datetime as _dt
    p = _extraction_dir() / f"{briefing_id}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps({"briefing_id": briefing_id, "method": method,
                                "tickers": tickers, "error": error,
                                "at": _dt.now().isoformat(timespec="seconds")},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)   # 原子写(I4.4 同款纪律)


def _pool_tickers() -> list[str]:
    try:
        from src.strategy.three_categories import load_pool_state
        s = load_pool_state()
        out = []
        for pool in (s.v_pool, s.t_pool, s.n_pool):
            out.extend(e.ticker for e in pool)
        return out
    except Exception:
        return []   # 池不可读不阻塞降级路径,UI 允许自由输入


@app.get("/api/briefing/{briefing_id}/tickers")
def get_briefing_tickers(briefing_id: str):
    """历史抽取结果(故事D: 用缓存,不重抽)。无缓存 → status=none。"""
    import json as _json
    p = _extraction_dir() / f"{briefing_id}.json"
    if not p.exists():
        return {"briefing_id": briefing_id, "status": "none",
                "pool_tickers": _pool_tickers()}
    try:
        return {"status": "ok", **_json.loads(p.read_text(encoding="utf-8"))}
    except Exception as e:
        return JSONResponse({"error": f"抽取缓存损坏: {e}"}, status_code=500)


class ManualTickersRequest(BaseModel):
    tickers: list[str]


@app.post("/api/briefing/{briefing_id}/tickers/manual")
def set_briefing_tickers_manual(briefing_id: str, req: ManualTickersRequest):
    """I2.2 手动兜底提交: 归一化校验后持久化(method=manual)。"""
    try:
        from src.markets.ticker import normalize_ticker
        items, bad = [], []
        for t in req.tickers:
            try:
                items.append({"ticker": normalize_ticker(t.strip()), "name": "",
                              "role": "focus", "raw_mention": "(手动选择)"})
            except Exception:
                bad.append(t)
        if bad:
            return JSONResponse({"error": f"无法识别的标的: {bad}"}, status_code=400)
        _save_extraction(briefing_id, "manual", items, None)
        return {"status": "ok", "briefing_id": briefing_id, "count": len(items)}
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()},
                            status_code=500)


@app.get("/healthz")
def healthz():
    """I5.1: 核心模块实际路径 + 版本(Ghost Version 排查)。"""
    mods = {}
    for name in ("src.analysis.ticker_extractor", "src.analysis.unlock_radar",
                 "src.analysis.fraud_detector", "src.analysis.dcf",
                 "src.analysis.peer_compare", "src.tools.api_china",
                 "src.tools.baostock_data", "src.tools.tushare_data"):
        try:
            import importlib
            m = importlib.import_module(name)
            mods[name] = {"version": getattr(m, "__version__", "?"),
                          "path": getattr(m, "__file__", "?")}
        except Exception as e:
            mods[name] = {"error": str(e)[:80]}
    return {"status": "ok", "modules": mods}


if __name__ == "__main__":
    print("\n  ✦ AI Hedge Fund 中国版 — Web 控制台")
    print("  ✦ 浏览器打开: http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
