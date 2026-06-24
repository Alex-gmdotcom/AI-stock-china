"""
可视化 Web 界面 — AI Hedge Fund 中国版

无需终端，浏览器操作：
  - 一键生成早报/晚报
  - 个股 AI 分析（选择分析师组合）
  - 三分法观察池管理

启动方式：
    poetry run python src/web_app.py
    然后浏览器打开 http://localhost:8000

默认使用 DeepSeek（.env 中配置 DEEPSEEK_API_KEY）。
"""

from __future__ import annotations

import os
import traceback
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="AI Hedge Fund China — Web UI")

# ── 默认模型（按 .env 中配置的 key 自动选择）──
def default_model() -> tuple[str, str]:
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek-chat", "DeepSeek"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude-sonnet-4-20250514", "Anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "gpt-4.1", "OpenAI"
    return "deepseek-chat", "DeepSeek"


# ════════════════════════════════════════════
# API 端点
# ════════════════════════════════════════════

class AnalyzeRequest(BaseModel):
    ticker: str
    analysts: list[str] | None = None
    model_name: str | None = None
    model_provider: str | None = None


@app.get("/api/pool")
def get_pool():
    """获取三分法观察池"""
    try:
        from src.strategy.three_categories import ThreeCategoryPool
        pool = ThreeCategoryPool()
        return pool.to_json_for_frontend()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
            "cash": 1000000.0,
            "margin_requirement": 0.5,
            "margin_used": 0.0,
            "positions": {info.full_ticker: {"long": 0, "short": 0, "long_cost_basis": 0.0, "short_cost_basis": 0.0, "short_margin_used": 0.0}},
            "realized_gains": {info.full_ticker: {"long": 0.0, "short": 0.0}},
        }

        result = run_china_hedge_fund(
            tickers=[info.full_ticker],
            start_date=start_date,
            end_date=end_date,
            portfolio=portfolio,
            show_reasoning=False,
            selected_analysts=req.analysts,
            model_name=model_name,
            model_provider=model_provider,
        )

        # v3.4: 强制结论层 —— 区分"数据不足型中性"与真实观点，
        # 杜绝裸 hold/0/100% 输出
        try:
            from src.utils.decision_summary import build_conclusions
            result["conclusions"] = build_conclusions(result, [info.full_ticker])
        except Exception:
            result["conclusions"] = {}
        return result
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


# ════════════════════════════════════════════
# 内嵌前端页面
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
input,select{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;
  color:var(--text);font-size:14px;font-family:inherit;outline:none;width:100%}
input:focus,select:focus{border-color:var(--accent)}
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
.pool-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:8px;margin-top:10px}
.pcard{border:1px solid var(--border);border-radius:8px;padding:9px 11px;font-size:12.5px}
.pcard .nm{font-weight:700;font-size:13.5px;margin:2px 0}
.pcard .tk{color:var(--dim);font-family:var(--mono);font-size:10.5px}
.stars{color:#e8b339;font-size:11px;letter-spacing:1px}
.footer{color:var(--dim);font-size:11.5px;margin-top:28px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI Hedge Fund 中国版</h1>
  <div class="sub">三分法投资研究控制台 · 红涨绿跌 · 无需终端操作</div>

  <div class="row">
    <!-- 个股分析 -->
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
      <div style="margin-top:14px">
        <button class="btn" id="abtn" onclick="analyze()">▶ 开始分析</button>
      </div>
      <div class="loading" id="aload"><div class="spin"></div><span>多 Agent 并行分析中，约 2-5 分钟，请勿关闭页面...</span></div>
      <div class="out" id="aout"></div>
    </div>
  </div>

  <div class="row">
    <!-- 观察池 -->
    <div class="card">
      <h2>📊 三分法观察池 <button class="btn sm alt" style="float:right" onclick="loadPool()">⟳ 刷新</button></h2>
      <div id="pool" class="pool-grid"><span style="color:var(--dim)">加载中...</span></div>
    </div>
  </div>

  <div class="footer">本工具为 AI 辅助分析，不构成投资建议 · AI Hedge Fund China Edition</div>
</div>

<script>
const $ = id => document.getElementById(id);

function fmt(text){
  return text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>');
}

async function analyze(){
  const ticker = $('ticker').value.trim();
  if(!ticker){ alert('请输入股票代码'); return; }
  const analysts = [...document.querySelectorAll('.checks input:checked')].map(c=>c.value);
  if(!analysts.length){ alert('至少选择一个分析师'); return; }
  $('abtn').disabled = true;
  $('aload').classList.add('show');
  $('aout').classList.remove('show');
  try{
    const r = await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ticker, analysts})});
    const d = await r.json();
    if(d.error){ $('aout').innerHTML='❌ '+fmt(d.error); }
    else{
      let out = '═══ 交易决策 ═══\\n\\n';
      const dec = d.decisions || {};
      for(const [tk,v] of Object.entries(dec)){
        out += `【${tk}】 动作: ${v.action || '-'} | 数量: ${v.quantity ?? '-'} | 置信度: ${v.confidence ?? '-'}%\\n理由: ${v.reasoning || '-'}\\n\\n`;
      }
      if(d.conclusions){
        for(const txt of Object.values(d.conclusions)){
          out += '\\n' + txt + '\\n';
        }
      }
      out += '\\n═══ 各分析师信号 ═══\\n\\n';
      const sig = d.analyst_signals || {};
      for(const [agent,tickers] of Object.entries(sig)){
        if(agent.includes('risk_management')) continue;  // 风控输出非信号格式，跳过
        for(const [tk,s] of Object.entries(tickers)){
          if(!s || !s.signal) continue;
          const emoji = s.signal==='bullish'?'🔴看多':s.signal==='bearish'?'🟢看空':'⚪中性';
          const conf = (s.confidence ?? '-');
          out += `${agent.replace(/_agent$/,'').replace(/_/g,' ')}: ${emoji} (置信度 ${conf}%)\\n`;
        }
      }
      const isHK = /\\.HK$/i.test(ticker) || /^0\\d{4}$/.test(ticker);
      if(isHK){
        out += '\\n💡 提示: 港股的北向资金/A股板块/东方财富个股新闻数据覆盖有限，\\n' +
               '中国特色Agent对港股可能给出低置信度中性信号，属正常现象。\\n' +
               '港股分析建议侧重: 舆情+政策+技术面+巴菲特/塔勒布视角。';
      }
      $('aout').innerHTML = fmt(out);
    }
    $('aout').classList.add('show');
  }catch(e){ $('aout').innerHTML='❌ '+e.message; $('aout').classList.add('show'); }
  $('abtn').disabled = false;
  $('aload').classList.remove('show');
}

async function loadPool(){
  $('pool').innerHTML = '<span style="color:var(--dim)">加载中...</span>';
  try{
    const r = await fetch('/api/pool');
    const d = await r.json();
    if(d.error){ $('pool').innerHTML = '<div style="color:#e2453e;grid-column:1/-1">❌ 加载失败: '+fmt(d.error)+'<br><span style="color:var(--dim);font-size:11px">请检查后端日志，或重启 web_app.py</span></div>'; return; }
    if(!d.entries || !d.entries.length){ $('pool').innerHTML = '<span style="color:var(--dim)">观察池为空</span>'; return; }
    $('pool').innerHTML = d.entries.map(e=>{
      const cls = e.category==='V'?'v':e.category==='T'?'t':'n';
      return `<div class="pcard">
        <span class="tag ${cls}">${e.slot}</span><span class="stars">${'★'.repeat(e.rating)}</span>
        <div class="nm">${e.name}</div>
        <div class="tk">${e.ticker}</div>
      </div>`;
    }).join('');
  }catch(e){ $('pool').innerHTML = '<div style="color:#e2453e;grid-column:1/-1">❌ '+e.message+'</div>'; }
}

loadPool();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


if __name__ == "__main__":
    print("\n  ✦ AI Hedge Fund 中国版 — Web 控制台")
    print("  ✦ 浏览器打开: http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
