import { useState, useEffect, useRef, useCallback } from "react";

// ════════════════════════════════════════════
// 三分法 AI 投资终端 V3
// 红涨绿跌(A股惯例) · 交互式K线 · 全池热力图
// ════════════════════════════════════════════

const CAT = {
  V: { label: "估值", en: "Value", color: "#4f8ff7", bg: "rgba(79,143,247,.13)", desc: "赚价格回归的钱", freq: "每周深查" },
  T: { label: "趋势", en: "Trend", color: "#f7634f", bg: "rgba(247,99,79,.13)", desc: "赚业绩双击的钱", freq: "每日必查" },
  N: { label: "叙事", en: "Narrative", color: "#e8b339", bg: "rgba(232,179,57,.13)", desc: "赚想象力的钱", freq: "每周两查" },
};

// A股惯例：红涨绿跌
const UP = "#e2453e", DOWN = "#1ba784", FLAT = "#8a8f9e";
const chgColor = (v) => (v > 0 ? UP : v < 0 ? DOWN : FLAT);
const chgSign = (v) => (v > 0 ? "+" : "");

const DEFAULT_POOL = [
  { slot:"V1",ticker:"002444.SZ",name:"巨星科技",cat:"V",rating:4,logic:"手工具龙头，绑定HD/沃尔玛",detail:"压制:关税55% | 催化:东南亚产能" },
  { slot:"V2",ticker:"600660.SH",name:"福耀玻璃",cat:"V",rating:5,logic:"全球汽车玻璃龙头>25%",detail:"压制:关税+车市周期 | 催化:智能玻璃" },
  { slot:"V3",ticker:"000333.SZ",name:"美的集团",cat:"V",rating:4,logic:"白电龙头，海外>42%",detail:"压制:国补退坡 | 催化:B端+海外OBM" },
  { slot:"V4",ticker:"000921.SZ",name:"海信家电",cat:"V",rating:4,logic:"白电估值洼地PE~10x",detail:"压制:关注度低 | 催化:海外+低基数" },
  { slot:"V5",ticker:"600887.SH",name:"伊利股份",cat:"V",rating:3,logic:"乳制品龙头，必选消费",detail:"压制:消费复苏弱 | 催化:股息率3.8%" },
  { slot:"T1",ticker:"300308.SZ",name:"中际旭创",cat:"T",rating:4,logic:"光模块龙头Q1+192%",detail:"景气:AI capex,800G/1.6T | 拐点:capex下调" },
  { slot:"T2",ticker:"002008.SZ",name:"大族激光",cat:"T",rating:4,logic:"PCB设备龙头",detail:"景气:PCB订单 | 拐点:AI开支放缓" },
  { slot:"T3",ticker:"603228.SH",name:"景旺电子",cat:"T",rating:3,logic:"PCB制造，算力+汽车",detail:"景气:产能利用率 | 拐点:产能过剩" },
  { slot:"T4",ticker:"300502.SZ",name:"新易盛",cat:"T",rating:3,logic:"光模块LPO方案",detail:"景气:800G市占率 | 拐点:CPO切换" },
  { slot:"T5",ticker:"688019.SH",name:"安集科技",cat:"T",rating:3,logic:"半导体材料国产替代",detail:"景气:替代率+晶圆扩产 | 拐点:进度不及预期" },
  { slot:"N1",ticker:"09880.HK",name:"优必选",cat:"N",rating:3,logic:"人形机器人量产先行者",detail:"验证:订单+良率 | 证伪:技术路线替代" },
  { slot:"N2",ticker:"09660.HK",name:"地平线",cat:"N",rating:3,logic:"自动驾驶芯片平台",detail:"验证:定点+征程6 | 证伪:英伟达抢份额" },
  { slot:"N3",ticker:"601985.SH",name:"中国核电",cat:"N",rating:4,logic:"核电重启+新堆型",detail:"验证:核准机组 | 证伪:政策转向/安全事件" },
  { slot:"N4",ticker:"002050.SZ",name:"三花智控",cat:"N",rating:3,logic:"热管理→机器人零部件",detail:"验证:机器人订单 | 证伪:产业化低于预期" },
  { slot:"N5",ticker:"002594.SZ",name:"比亚迪",cat:"N",rating:4,logic:"新能源全球化+智能化",detail:"验证:海外产能+智驾 | 证伪:价格战+政策壁垒" },
];

const SK = "stock-dashboard-v3";
async function loadState() { try { const r = await window.storage.get(SK); return r ? JSON.parse(r.value) : null; } catch { return null; } }
async function saveState(s) { try { await window.storage.set(SK, JSON.stringify(s)); } catch (e) { console.error(e); } }

async function callLLM(prompt, sys, maxTokens = 1200) {
  try {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514", max_tokens: maxTokens, system: sys || "",
        tools: [{ type: "web_search_20250305", name: "web_search" }],
        messages: [{ role: "user", content: prompt }],
      }),
    });
    const d = await r.json();
    return d.content?.filter((b) => b.type === "text").map((b) => b.text).join("\n") || "";
  } catch (e) { return "API调用失败: " + e.message; }
}

function parseJSON(text) {
  try { const m = text.match(/\[[\s\S]*\]|\{[\s\S]*\}/); if (m) return JSON.parse(m[0]); } catch (e) {}
  return null;
}

// ── 均线计算 ──
function calcMA(data, period) {
  return data.map((_, i) => {
    if (i < period - 1) return null;
    let s = 0;
    for (let j = i - period + 1; j <= i; j++) s += data[j].close;
    return s / period;
  });
}

// ════════════════════════════════════════════
// 交互式K线图：十字光标 + 悬停明细 + MA均线
// ════════════════════════════════════════════
function KlineChart({ data, period }) {
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);
  if (!data?.length) return null;

  const W = 760, H = 400, pad = { t: 18, r: 62, b: 30, l: 10 };
  const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const volH = ch * 0.16;
  const priceH = ch - volH - 8;

  const all = data.flatMap((d) => [d.high, d.low]);
  const mn = Math.min(...all), mx = Math.max(...all), rng = mx - mn || 1;
  const pMin = mn - rng * 0.04, pMax = mx + rng * 0.04;
  const gap = cw / data.length, bw = Math.max(2, Math.min(13, gap * 0.68));
  const y = (p) => pad.t + priceH - ((p - pMin) / (pMax - pMin)) * priceH;
  const maxV = Math.max(...data.map((d) => d.volume || 0));

  const ma5 = calcMA(data, 5), ma10 = calcMA(data, 10), ma20 = calcMA(data, 20);
  const maLine = (ma, color) => {
    const pts = ma.map((v, i) => (v == null ? null : `${pad.l + i * gap + gap / 2},${y(v)}`)).filter(Boolean);
    return pts.length > 1 ? <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.2" opacity="0.85" /> : null;
  };

  const onMove = (e) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const sx = ((e.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(data.length - 1, Math.floor((sx - pad.l) / gap)));
    setHover(idx);
  };

  const hd = hover != null ? data[hover] : null;
  const hPrev = hover > 0 ? data[hover - 1] : null;
  const hChg = hd && hPrev ? ((hd.close - hPrev.close) / hPrev.close) * 100 : 0;
  const hx = hover != null ? pad.l + hover * gap + gap / 2 : 0;

  return (
    <div style={{ position: "relative" }}>
      {/* 悬停信息条 */}
      <div style={{
        display: "flex", gap: 14, padding: "6px 10px", fontSize: 12, fontFamily: "var(--mono)",
        color: "var(--dim)", minHeight: 30, alignItems: "center", flexWrap: "wrap",
      }}>
        {hd ? (<>
          <span style={{ color: "var(--text)", fontWeight: 600 }}>{hd.date}</span>
          <span>开 <b style={{ color: chgColor(hd.open - (hPrev?.close ?? hd.open)) }}>{hd.open.toFixed(2)}</b></span>
          <span>高 <b style={{ color: UP }}>{hd.high.toFixed(2)}</b></span>
          <span>低 <b style={{ color: DOWN }}>{hd.low.toFixed(2)}</b></span>
          <span>收 <b style={{ color: chgColor(hd.close - hd.open) }}>{hd.close.toFixed(2)}</b></span>
          <span>涨跌 <b style={{ color: chgColor(hChg) }}>{chgSign(hChg)}{hChg.toFixed(2)}%</b></span>
          <span>量 <b style={{ color: "var(--text)" }}>{(hd.volume / 1e4).toFixed(0)}万</b></span>
        </>) : (
          <span>移动鼠标查看每日明细 · <span style={{color:"#e8b339"}}>—MA5</span> <span style={{color:"#4f8ff7"}}>—MA10</span> <span style={{color:"#b76ef0"}}>—MA20</span></span>
        )}
      </div>

      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", cursor: "crosshair" }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {/* 网格 + 价格刻度 */}
        {Array.from({ length: 6 }, (_, i) => {
          const p = pMin + ((pMax - pMin) / 5) * i, py = y(p);
          return (<g key={i}>
            <line x1={pad.l} y1={py} x2={W - pad.r} y2={py} stroke="var(--grid)" strokeWidth="0.5" />
            <text x={W - pad.r + 5} y={py + 3.5} fill="var(--dim)" fontSize="10" fontFamily="var(--mono)">{p.toFixed(2)}</text>
          </g>);
        })}

        {/* 成交量（底部，红涨绿跌） */}
        {data.map((d, i) => {
          const x = pad.l + i * gap + gap / 2;
          const vh = maxV > 0 ? (d.volume / maxV) * volH : 0;
          const bull = d.close >= d.open;
          return <rect key={`v${i}`} x={x - bw / 2} y={H - pad.b - vh} width={bw} height={vh}
            fill={bull ? "rgba(226,69,62,.28)" : "rgba(27,167,132,.28)"} />;
        })}

        {/* K线（红涨绿跌：阳线红色空心，阴线绿色实心） */}
        {data.map((d, i) => {
          const x = pad.l + i * gap + gap / 2;
          const bull = d.close >= d.open;
          const c = bull ? UP : DOWN;
          const bt = y(Math.max(d.open, d.close)), bb = y(Math.min(d.open, d.close));
          const bh = Math.max(1, bb - bt);
          return (<g key={i} opacity={hover != null && hover !== i ? 0.55 : 1}>
            <line x1={x} y1={y(d.high)} x2={x} y2={y(d.low)} stroke={c} strokeWidth="1" />
            <rect x={x - bw / 2} y={bt} width={bw} height={bh}
              fill={bull ? "var(--bg)" : c} stroke={c} strokeWidth="1.1" rx="0.5" />
          </g>);
        })}

        {/* MA均线 */}
        {maLine(ma5, "#e8b339")}
        {maLine(ma10, "#4f8ff7")}
        {maLine(ma20, "#b76ef0")}

        {/* 十字光标 */}
        {hd && (<g pointerEvents="none">
          <line x1={hx} y1={pad.t} x2={hx} y2={H - pad.b} stroke="var(--text)" strokeWidth="0.6" strokeDasharray="3,3" opacity="0.5" />
          <line x1={pad.l} y1={y(hd.close)} x2={W - pad.r} y2={y(hd.close)} stroke="var(--text)" strokeWidth="0.6" strokeDasharray="3,3" opacity="0.5" />
          <rect x={W - pad.r + 1} y={y(hd.close) - 9} width={58} height={18} fill="var(--text)" rx="3" />
          <text x={W - pad.r + 30} y={y(hd.close) + 4} fill="var(--bg)" fontSize="10.5" fontWeight="700" textAnchor="middle" fontFamily="var(--mono)">{hd.close.toFixed(2)}</text>
        </g>)}

        {/* 日期刻度 */}
        {data.filter((_, i) => i % Math.max(1, Math.floor(data.length / 7)) === 0).map((d) => {
          const idx = data.indexOf(d), x = pad.l + idx * gap + gap / 2;
          return <text key={`d${idx}`} x={x} y={H - 6} fill="var(--dim)" fontSize="9.5" textAnchor="middle" fontFamily="var(--mono)">{d.date?.slice(5) || ""}</text>;
        })}
      </svg>
    </div>
  );
}

// ════════════════════════════════════════════
// 简易 Markdown 渲染（标题/加粗/列表/分隔线）
// ════════════════════════════════════════════
function Md({ text }) {
  if (!text) return null;
  const lines = text.split("\n");
  return (
    <div style={{ lineHeight: 1.9, fontSize: 14 }}>
      {lines.map((ln, i) => {
        const t = ln.trim();
        if (!t) return <div key={i} style={{ height: 8 }} />;
        if (/^[━─=]{3,}$/.test(t)) return <hr key={i} style={{ border: "none", borderTop: "1px solid var(--border)", margin: "10px 0" }} />;
        if (/^#{1,3}\s/.test(t)) {
          const lvl = t.match(/^#+/)[0].length;
          return <div key={i} style={{ fontWeight: 700, fontSize: lvl === 1 ? 17 : lvl === 2 ? 15.5 : 14.5, margin: "12px 0 4px", color: "var(--accent2)" }}>{inline(t.replace(/^#+\s/, ""))}</div>;
        }
        if (/^[一二三四五六七八九十][、.]/.test(t) || /^\d+[\.、]\s*【/.test(t) || /^【.+】/.test(t.slice(0, 20)))
          return <div key={i} style={{ fontWeight: 700, margin: "12px 0 4px", color: "var(--accent2)" }}>{inline(t)}</div>;
        if (/^[·•\-*]\s/.test(t))
          return <div key={i} style={{ paddingLeft: 16, position: "relative" }}>
            <span style={{ position: "absolute", left: 4, color: "var(--dim)" }}>·</span>{inline(t.replace(/^[·•\-*]\s/, ""))}</div>;
        return <div key={i}>{inline(t)}</div>;
      })}
    </div>
  );
  function inline(s) {
    const parts = s.split(/(\*\*[^*]+\*\*)/g);
    return parts.map((p, j) => p.startsWith("**") && p.endsWith("**")
      ? <b key={j} style={{ color: "var(--text)" }}>{p.slice(2, -2)}</b> : p);
  }
}

// ════════════════════════════════════════════
// 星级（可点击编辑）
// ════════════════════════════════════════════
function Stars({ n, onChange, size = 13 }) {
  return (
    <span style={{ letterSpacing: 1, cursor: onChange ? "pointer" : "default", fontSize: size, userSelect: "none" }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} onClick={onChange ? (e) => { e.stopPropagation(); onChange(i); } : undefined}
          style={{ color: i <= n ? "#e8b339" : "#3a3f52", transition: "color .12s" }}>★</span>
      ))}
    </span>
  );
}

// ════════════════════════════════════════════
// 主应用
// ════════════════════════════════════════════
export default function App() {
  const [pool, setPool] = useState(DEFAULT_POOL);
  const [quotes, setQuotes] = useState({});     // {ticker: {price, chg}}
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("kline");
  const [catFilter, setCatFilter] = useState("all");
  const [klineCache, setKlineCache] = useState({});  // {ticker: data}
  const [period, setPeriod] = useState(30);
  const [reports, setReports] = useState({});  // {`${ticker}-${tab}`: text}
  const [loading, setLoading] = useState({});
  const [quotesLoading, setQuotesLoading] = useState(false);

  useEffect(() => { loadState().then((s) => {
    if (s?.pool) setPool(s.pool);
    if (s?.quotes) setQuotes(s.quotes);
  }); }, []);
  useEffect(() => { saveState({ pool, quotes }); }, [pool, quotes]);

  const stock = selected ? pool.find((s) => s.ticker === selected) : null;
  const filtered = catFilter === "all" ? pool : pool.filter((s) => s.cat === catFilter);
  const kline = selected ? klineCache[`${selected}-${period}`] : null;

  // ── 一键刷新全池行情（热力图数据）──
  const refreshQuotes = async () => {
    setQuotesLoading(true);
    const list = pool.map((s) => `${s.name}(${s.ticker})`).join("、");
    const raw = await callLLM(
      `搜索以下中国A股/港股的最新收盘价和当日涨跌幅：${list}。
只返回JSON对象，格式：{"002444.SZ":{"price":31.25,"chg":1.8},"600660.SH":{"price":58.3,"chg":-0.5},...}
chg为涨跌幅百分比数字。不要任何其他文字。`,
      "你是金融数据助手。只返回JSON。", 2000);
    const parsed = parseJSON(raw);
    if (parsed) setQuotes(parsed);
    setQuotesLoading(false);
  };

  // ── 获取K线 ──
  const fetchKline = async (ticker, days) => {
    const key = `${ticker}-${days}`;
    if (klineCache[key]) return;
    setLoading((p) => ({ ...p, kline: true }));
    const s = pool.find((x) => x.ticker === ticker);
    const raw = await callLLM(
      `搜索 ${s?.name || ticker} (${ticker}) 最近${days}个交易日的日K线数据。只返回JSON数组：
[{"date":"2026-06-01","open":100,"close":102,"high":103,"low":99,"volume":15000000},...]
基于真实价格区间，按日期升序。不要任何其他文字。`,
      "你是金融数据助手。只返回JSON。", days > 60 ? 4000 : 2500);
    const parsed = parseJSON(raw);
    if (parsed?.length) setKlineCache((p) => ({ ...p, [key]: parsed }));
    setLoading((p) => ({ ...p, kline: false }));
  };

  const selectStock = (ticker) => {
    setSelected(ticker); setTab("kline");
    fetchKline(ticker, period);
  };

  const changePeriod = (d) => { setPeriod(d); if (selected) fetchKline(selected, d); };

  // ── AI 报告 ──
  const fetchReport = async (type) => {
    if (!stock) return;
    const key = `${stock.ticker}-${type}`;
    setLoading((p) => ({ ...p, report: true }));
    const ci = CAT[stock.cat];
    const prompts = {
      morning: `你是中国市场首席策略师。搜索 ${stock.name}(${stock.ticker}) 最新信息，生成今日早盘策略。该股属【${ci.label}类】：${stock.logic}。${stock.detail}。
结构：## 宏观定调 / ## 盘前催化剂 / ## ${ci.label}类专项（${stock.cat === "V" ? "压制因素是否边际改善" : stock.cat === "T" ? "景气度延续性+拐点预警" : "叙事证实/证伪进展"}）/ ## 反向假说 / ## 今日剧本。重点用**加粗**。500字内。`,
      evening: `你是量化复盘员。搜索 ${stock.name}(${stock.ticker}) 今日收盘数据，生成盘后复盘。【${ci.label}类】评级${"★".repeat(stock.rating)}。${stock.detail}。
结构：## 收盘体温计 / ## 异动归因 / ## ${ci.label}类跟踪（${stock.cat === "V" ? "压制因素变化+估值水位" : stock.cat === "T" ? "景气度信号+拐点预警等级" : "叙事进展+置信度"}）/ ## 迁移检测 / ## 评级建议 / ## 明日校准。重点用**加粗**。500字内。`,
      deep: `你是资深研究员。搜索 ${stock.name}(${stock.ticker}) 全面深度分析。三分法定位：【${ci.label}的钱】${ci.desc}。
结构：## 基本面（PE/PB/ROE/营收 vs 同业）/ ## 技术面（支撑/压力/形态）/ ## 政策面 / ## ${stock.cat === "V" ? "压制因素评估：改善概率+时间线" : stock.cat === "T" ? "景气拐点分析" : "叙事验证进度"} / ## 综合评级 / ## 长线判断（这只股票值得长期持有吗？关键论据）。重点用**加粗**。700字内。`,
    };
    const text = await callLLM(prompts[type] || prompts.morning, "", 1500);
    setReports((p) => ({ ...p, [key]: text }));
    setLoading((p) => ({ ...p, report: false }));
  };

  const adjustRating = (ticker, n) => {
    setPool((p) => p.map((s) => (s.ticker === ticker ? { ...s, rating: n } : s)));
  };

  const last = kline?.[kline.length - 1], prev = kline?.[kline.length - 2];
  const chg = last && prev ? last.close - prev.close : 0;
  const chgPct = prev ? (chg / prev.close) * 100 : 0;
  const report = stock ? reports[`${stock.ticker}-${tab}`] : null;

  // ════════ 渲染 ════════
  return (
    <div style={S.root}>
      {/* ── 左侧观察池 ── */}
      <div style={S.side}>
        <div style={S.sideH}>
          <span style={S.sideT}>三分法观察池</span>
          <button onClick={refreshQuotes} disabled={quotesLoading} style={S.refreshSm} title="刷新全池行情">
            {quotesLoading ? "⏳" : "⟳"}
          </button>
        </div>
        <div style={S.catTabs}>
          {[{ k: "all", l: "全部" }, { k: "V", l: "估值" }, { k: "T", l: "趋势" }, { k: "N", l: "叙事" }].map((c) => (
            <button key={c.k} onClick={() => setCatFilter(c.k)}
              style={{ ...S.catTab, ...(catFilter === c.k ? { background: c.k === "all" ? "var(--accent)" : CAT[c.k]?.color, borderColor: "transparent", color: "#fff" } : {}) }}>
              {c.l}
            </button>
          ))}
        </div>
        <div style={S.list}>
          {filtered.map((s) => {
            const ci = CAT[s.cat], act = selected === s.ticker, q = quotes[s.ticker];
            return (
              <div key={s.ticker} onClick={() => selectStock(s.ticker)}
                style={{ ...S.item, borderLeft: `3px solid ${ci.color}`, ...(act ? { background: "var(--bg-act)" } : {}) }}>
                <div style={S.itemRow}>
                  <span style={{ ...S.slot, color: ci.color }}>{s.slot}</span>
                  <span style={S.name}>{s.name}</span>
                  {q && <span style={{ ...S.chgChip, color: chgColor(q.chg), background: q.chg > 0 ? "rgba(226,69,62,.12)" : q.chg < 0 ? "rgba(27,167,132,.12)" : "transparent" }}>
                    {chgSign(q.chg)}{q.chg?.toFixed(1)}%</span>}
                </div>
                <div style={S.itemRow2}>
                  <span style={S.tk}>{s.ticker}</span>
                  <Stars n={s.rating} size={11} />
                </div>
              </div>
            );
          })}
        </div>
        <div style={S.legend}>
          <span style={{ color: UP }}>■ 红涨</span>
          <span style={{ color: DOWN }}>■ 绿跌</span>
          <span style={{ color: "var(--dim)" }}>A股惯例</span>
        </div>
      </div>

      {/* ── 主区域 ── */}
      <div style={S.main}>
        {!selected ? (
          /* ════ 首页：全池热力图 ════ */
          <div style={S.home}>
            <div style={S.homeHead}>
              <div>
                <div style={S.homeT}>三分法 AI 投资终端</div>
                <div style={S.homeS}>15只观察池标的 · 点击卡片查看K线与AI分析</div>
              </div>
              <button onClick={refreshQuotes} disabled={quotesLoading} style={S.bigRefresh}>
                {quotesLoading ? "正在获取行情..." : "⟳ 刷新全池行情"}
              </button>
            </div>

            {Object.entries(CAT).map(([k, ci]) => (
              <div key={k} style={{ marginBottom: 22 }}>
                <div style={S.catHead}>
                  <span style={{ ...S.catBadgeBig, background: ci.bg, color: ci.color }}>{ci.label}的钱</span>
                  <span style={S.catDesc}>{ci.desc} · {ci.freq}</span>
                </div>
                <div style={S.grid}>
                  {pool.filter((s) => s.cat === k).map((s) => {
                    const q = quotes[s.ticker];
                    const bg = q ? (q.chg > 2 ? "rgba(226,69,62,.22)" : q.chg > 0 ? "rgba(226,69,62,.1)" : q.chg < -2 ? "rgba(27,167,132,.22)" : q.chg < 0 ? "rgba(27,167,132,.1)" : "var(--card)") : "var(--card)";
                    return (
                      <div key={s.ticker} onClick={() => selectStock(s.ticker)} style={{ ...S.card, background: bg }}>
                        <div style={S.cardTop}>
                          <span style={{ ...S.slot, color: ci.color }}>{s.slot}</span>
                          <Stars n={s.rating} size={11} />
                        </div>
                        <div style={S.cardName}>{s.name}</div>
                        <div style={S.cardTk}>{s.ticker}</div>
                        {q ? (
                          <div style={S.cardPrice}>
                            <span style={{ fontFamily: "var(--mono)", fontWeight: 700, fontSize: 16 }}>{q.price?.toFixed(2)}</span>
                            <span style={{ color: chgColor(q.chg), fontFamily: "var(--mono)", fontWeight: 600, fontSize: 13 }}>
                              {chgSign(q.chg)}{q.chg?.toFixed(2)}%</span>
                          </div>
                        ) : (
                          <div style={{ ...S.cardPrice, color: "var(--dim)", fontSize: 12 }}>点击⟳获取行情</div>
                        )}
                        <div style={S.cardLogic}>{s.logic}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        ) : (
          /* ════ 个股详情页 ════ */
          <>
            <div style={S.header}>
              <button onClick={() => setSelected(null)} style={S.backBtn}>← 返回总览</button>
              <div style={S.headMain}>
                <div style={S.headLeft}>
                  <span style={{ ...S.slotBig, color: CAT[stock.cat].color, background: CAT[stock.cat].bg }}>{stock.slot}</span>
                  <span style={S.nameB}>{stock.name}</span>
                  <span style={S.tkB}>{stock.ticker}</span>
                  <Stars n={stock.rating} size={15} onChange={(n) => adjustRating(stock.ticker, n)} />
                  <span style={S.rateHint}>点星调级</span>
                </div>
                {last && (
                  <div style={S.priceRow}>
                    <span style={{ ...S.price, color: chgColor(chgPct) }}>{last.close.toFixed(2)}</span>
                    <span style={{ ...S.chgB, color: chgColor(chgPct) }}>
                      {chgSign(chg)}{chg.toFixed(2)} ({chgSign(chgPct)}{chgPct.toFixed(2)}%)</span>
                  </div>
                )}
              </div>
              <div style={S.logicBar}>
                <span style={{ color: CAT[stock.cat].color, fontWeight: 600 }}>{CAT[stock.cat].label}类</span>
                <span style={{ margin: "0 8px", color: "var(--border)" }}>|</span>
                <span style={{ color: "var(--dim)" }}>{stock.logic} · {stock.detail}</span>
              </div>
            </div>

            <div style={S.tabs}>
              {[{ id: "kline", l: "K线图" }, { id: "morning", l: "早盘策略" }, { id: "evening", l: "盘后复盘" }, { id: "deep", l: "长线研判" }].map((t) => (
                <button key={t.id}
                  onClick={() => { setTab(t.id); if (t.id !== "kline" && !reports[`${stock.ticker}-${t.id}`]) fetchReport(t.id); }}
                  style={{ ...S.tab, ...(tab === t.id ? S.tabOn : {}) }}>{t.l}</button>
              ))}
              {tab === "kline" && (
                <div style={S.periodGroup}>
                  {[30, 60, 120].map((d) => (
                    <button key={d} onClick={() => changePeriod(d)}
                      style={{ ...S.periodBtn, ...(period === d ? S.periodOn : {}) }}>{d}日</button>
                  ))}
                </div>
              )}
            </div>

            <div style={S.content}>
              {tab === "kline" && (
                loading.kline ? <Loader text={`正在获取 ${stock.name} ${period}日K线...`} /> :
                kline ? (
                  <div style={S.chartBox}><KlineChart data={kline} period={period} /></div>
                ) : <div style={S.noData}>暂无数据，请稍后重试</div>
              )}
              {tab !== "kline" && (
                loading.report ? <Loader text={`AI 正在生成${tab === "morning" ? "早盘策略" : tab === "evening" ? "盘后复盘" : "长线研判"}...`} /> :
                report ? (
                  <div style={S.reportBox}>
                    <div style={S.reportHead}>
                      <span style={{ fontWeight: 700 }}>{tab === "morning" ? "🌅 早盘策略" : tab === "evening" ? "🌆 盘后复盘" : "🔭 长线研判"} — {stock.name}</span>
                      <button onClick={() => fetchReport(tab)} style={S.refreshBtn}>⟳ 重新生成</button>
                    </div>
                    <Md text={report} />
                  </div>
                ) : (
                  <div style={{ textAlign: "center", padding: 50 }}>
                    <button onClick={() => fetchReport(tab)} style={S.genBtn}>
                      生成{tab === "morning" ? "早盘策略" : tab === "evening" ? "盘后复盘" : "长线研判"}
                    </button>
                    <div style={{ color: "var(--dim)", fontSize: 12, marginTop: 12 }}>
                      AI 将搜索最新数据，按{CAT[stock.cat].label}类框架分析
                    </div>
                  </div>
                )
              )}
            </div>
          </>
        )}
      </div>

      <style>{`
        :root{--bg:#0b0d13;--card:#14171f;--bg-act:#1e2433;--border:#232838;--grid:#1a1f2d;
          --text:#e6e8ef;--dim:#6e7389;--accent:#5b7cfa;--accent2:#9db4ff;
          --mono:'SF Mono',ui-monospace,Consolas,monospace}
        *{box-sizing:border-box;margin:0;padding:0}
        ::-webkit-scrollbar{width:8px;height:8px}
        ::-webkit-scrollbar-thumb{background:var(--border);border-radius:4px}
        @keyframes spin{to{transform:rotate(360deg)}}
        @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
      `}</style>
    </div>
  );
}

function Loader({ text }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: 70, gap: 16 }}>
      <div style={{ width: 30, height: 30, border: "3px solid var(--border)", borderTop: "3px solid var(--accent)", borderRadius: "50%", animation: "spin .8s linear infinite" }} />
      <div style={{ color: "var(--dim)", fontSize: 13 }}>{text}</div>
    </div>
  );
}

const S = {
  root: { display: "flex", height: "100vh", background: "var(--bg)", color: "var(--text)", fontFamily: "system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif", fontSize: 14 },
  side: { width: 246, background: "var(--card)", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", flexShrink: 0 },
  sideH: { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "13px 12px", borderBottom: "1px solid var(--border)" },
  sideT: { fontWeight: 700, fontSize: 14, letterSpacing: 0.5 },
  refreshSm: { background: "none", border: "1px solid var(--border)", borderRadius: 6, color: "var(--dim)", cursor: "pointer", fontSize: 13, padding: "2px 8px" },
  catTabs: { display: "flex", gap: 4, padding: "8px 8px 6px" },
  catTab: { flex: 1, background: "none", border: "1px solid var(--border)", borderRadius: 6, color: "var(--dim)", padding: "4px 0", cursor: "pointer", fontSize: 12, fontFamily: "inherit", transition: "all .12s" },
  list: { flex: 1, overflowY: "auto", padding: "4px 6px" },
  item: { padding: "8px 10px", borderRadius: 7, cursor: "pointer", marginBottom: 3, transition: "background .12s" },
  itemRow: { display: "flex", alignItems: "center", gap: 7 },
  itemRow2: { display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 3 },
  slot: { fontSize: 10.5, fontWeight: 800, fontFamily: "var(--mono)" },
  name: { fontSize: 13.5, fontWeight: 600, flex: 1 },
  chgChip: { fontSize: 11, fontFamily: "var(--mono)", fontWeight: 700, padding: "1px 5px", borderRadius: 4 },
  tk: { fontSize: 10.5, color: "var(--dim)", fontFamily: "var(--mono)" },
  legend: { display: "flex", gap: 12, padding: "10px 12px", borderTop: "1px solid var(--border)", fontSize: 11 },

  main: { flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" },

  home: { flex: 1, overflowY: "auto", padding: "22px 28px" },
  homeHead: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 24 },
  homeT: { fontSize: 22, fontWeight: 800, letterSpacing: 0.5 },
  homeS: { color: "var(--dim)", fontSize: 13, marginTop: 4 },
  bigRefresh: { background: "var(--accent)", border: "none", borderRadius: 8, color: "#fff", padding: "9px 18px", cursor: "pointer", fontSize: 13.5, fontWeight: 600, fontFamily: "inherit" },
  catHead: { display: "flex", alignItems: "center", gap: 10, marginBottom: 10 },
  catBadgeBig: { fontSize: 13.5, fontWeight: 700, padding: "3px 12px", borderRadius: 6 },
  catDesc: { color: "var(--dim)", fontSize: 12 },
  grid: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(190px,1fr))", gap: 10 },
  card: { border: "1px solid var(--border)", borderRadius: 10, padding: "12px 14px", cursor: "pointer", transition: "transform .12s,border-color .12s" },
  cardTop: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  cardName: { fontSize: 15, fontWeight: 700, marginTop: 6 },
  cardTk: { fontSize: 11, color: "var(--dim)", fontFamily: "var(--mono)", marginTop: 1 },
  cardPrice: { display: "flex", alignItems: "baseline", gap: 8, marginTop: 8, minHeight: 22 },
  cardLogic: { fontSize: 11.5, color: "var(--dim)", marginTop: 6, lineHeight: 1.5, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" },

  header: { padding: "12px 24px 0" },
  backBtn: { background: "none", border: "none", color: "var(--accent2)", cursor: "pointer", fontSize: 13, padding: 0, fontFamily: "inherit", marginBottom: 8 },
  headMain: { display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 8 },
  headLeft: { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" },
  slotBig: { fontSize: 12, fontWeight: 800, padding: "3px 9px", borderRadius: 6, fontFamily: "var(--mono)" },
  nameB: { fontSize: 21, fontWeight: 800 },
  tkB: { fontSize: 13.5, color: "var(--dim)", fontFamily: "var(--mono)" },
  rateHint: { fontSize: 10.5, color: "var(--dim)" },
  priceRow: { display: "flex", alignItems: "baseline", gap: 10 },
  price: { fontFamily: "var(--mono)", fontSize: 27, fontWeight: 800 },
  chgB: { fontFamily: "var(--mono)", fontSize: 15, fontWeight: 700 },
  logicBar: { marginTop: 6, fontSize: 12.5, padding: "7px 0" },

  tabs: { display: "flex", alignItems: "center", gap: 4, padding: "4px 24px 0", borderBottom: "1px solid var(--border)" },
  tab: { background: "none", border: "none", color: "var(--dim)", padding: "8px 15px 11px", cursor: "pointer", fontSize: 13.5, fontWeight: 600, borderBottom: "2px solid transparent", transition: "all .12s", fontFamily: "inherit" },
  tabOn: { color: "var(--text)", borderBottomColor: "var(--accent)" },
  periodGroup: { marginLeft: "auto", display: "flex", gap: 4, paddingBottom: 6 },
  periodBtn: { background: "none", border: "1px solid var(--border)", borderRadius: 6, color: "var(--dim)", padding: "3px 10px", cursor: "pointer", fontSize: 12, fontFamily: "var(--mono)" },
  periodOn: { background: "var(--accent)", borderColor: "var(--accent)", color: "#fff" },

  content: { flex: 1, overflowY: "auto", padding: "14px 24px 24px" },
  chartBox: { background: "var(--card)", borderRadius: 12, border: "1px solid var(--border)", padding: "10px 12px 8px" },
  reportBox: { background: "var(--card)", border: "1px solid var(--border)", borderRadius: 12, padding: "18px 24px 22px" },
  reportHead: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, paddingBottom: 12, borderBottom: "1px solid var(--border)" },
  refreshBtn: { background: "none", border: "1px solid var(--border)", borderRadius: 7, color: "var(--text)", padding: "5px 13px", cursor: "pointer", fontSize: 12.5, fontFamily: "inherit" },
  genBtn: { background: "var(--accent)", border: "none", borderRadius: 9, color: "#fff", padding: "11px 28px", cursor: "pointer", fontSize: 14.5, fontWeight: 700, fontFamily: "inherit" },
  noData: { textAlign: "center", padding: 70, color: "var(--dim)" },
};
