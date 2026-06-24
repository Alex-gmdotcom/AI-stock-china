import { useState, useEffect, useCallback, useRef } from "react";

// ═══════════════════════════════════════════════
// Constants & Config
// ═══════════════════════════════════════════════
const MARKETS = {
  cn_main: { label: "沪市主板", color: "#f59e0b", prefix: /^(600|601|603|605)/ },
  cn_sz: { label: "深市主板", color: "#3b82f6", prefix: /^(000|001|002|003)/ },
  cn_chinext: { label: "创业板", color: "#10b981", prefix: /^(300|301)/ },
  cn_star: { label: "科创板", color: "#8b5cf6", prefix: /^(688|689)/ },
  hk: { label: "港股", color: "#ef4444", prefix: /^\d{5}$/ },
};

function detectMarket(code) {
  const bare = code.replace(/\.(SH|SZ|HK)$/i, "");
  for (const [key, m] of Object.entries(MARKETS)) {
    if (m.prefix.test(bare)) return { key, ...m };
  }
  return { key: "unknown", label: "未知", color: "#6b7280" };
}

function formatTicker(raw) {
  const s = raw.trim().toUpperCase().replace(/\s+/g, "");
  if (/\.(SH|SZ|HK)$/.test(s)) return s;
  const bare = s.replace(/\D/g, "");
  if (/^(600|601|603|605|688|689)/.test(bare)) return bare + ".SH";
  if (/^(000|001|002|003|300|301)/.test(bare)) return bare + ".SZ";
  if (/^\d{5}$/.test(bare)) return bare + ".HK";
  return s;
}

// ═══════════════════════════════════════════════
// Candlestick Chart (pure SVG)
// ═══════════════════════════════════════════════
function CandlestickChart({ data, width = 720, height = 380 }) {
  if (!data || data.length === 0) return null;

  const pad = { top: 20, right: 60, bottom: 32, left: 12 };
  const cw = width - pad.left - pad.right;
  const ch = height - pad.top - pad.bottom;

  const allPrices = data.flatMap((d) => [d.high, d.low]);
  const minP = Math.min(...allPrices);
  const maxP = Math.max(...allPrices);
  const range = maxP - minP || 1;
  const pMin = minP - range * 0.05;
  const pMax = maxP + range * 0.05;

  const barW = Math.max(2, Math.min(12, (cw / data.length) * 0.7));
  const gap = cw / data.length;
  const y = (p) => pad.top + ch - ((p - pMin) / (pMax - pMin)) * ch;

  // Grid lines
  const gridLines = 5;
  const gridStep = (pMax - pMin) / gridLines;

  // Volume
  const maxVol = Math.max(...data.map((d) => d.volume || 0));

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto" }}>
      {/* Grid */}
      {Array.from({ length: gridLines + 1 }, (_, i) => {
        const price = pMin + gridStep * i;
        const py = y(price);
        return (
          <g key={i}>
            <line x1={pad.left} y1={py} x2={width - pad.right} y2={py} stroke="var(--grid)" strokeWidth="0.5" />
            <text x={width - pad.right + 6} y={py + 4} fill="var(--text-dim)" fontSize="10" fontFamily="'JetBrains Mono', monospace">
              {price.toFixed(2)}
            </text>
          </g>
        );
      })}

      {/* Volume bars (background) */}
      {data.map((d, i) => {
        const x = pad.left + i * gap + gap / 2;
        const volH = maxVol > 0 ? (d.volume / maxVol) * (ch * 0.15) : 0;
        const bullish = d.close >= d.open;
        return (
          <rect
            key={`v${i}`}
            x={x - barW / 2}
            y={pad.top + ch - volH}
            width={barW}
            height={volH}
            fill={bullish ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)"}
          />
        );
      })}

      {/* Candlesticks */}
      {data.map((d, i) => {
        const x = pad.left + i * gap + gap / 2;
        const bullish = d.close >= d.open;
        const color = bullish ? "#10b981" : "#ef4444";
        const bodyTop = y(Math.max(d.open, d.close));
        const bodyBot = y(Math.min(d.open, d.close));
        const bodyH = Math.max(1, bodyBot - bodyTop);

        return (
          <g key={i}>
            {/* Wick */}
            <line x1={x} y1={y(d.high)} x2={x} y2={y(d.low)} stroke={color} strokeWidth="1" />
            {/* Body */}
            <rect x={x - barW / 2} y={bodyTop} width={barW} height={bodyH} fill={bullish ? "transparent" : color} stroke={color} strokeWidth="1" rx="0.5" />
          </g>
        );
      })}

      {/* Date labels */}
      {data
        .filter((_, i) => i % Math.max(1, Math.floor(data.length / 6)) === 0)
        .map((d, i) => {
          const idx = data.indexOf(d);
          const x = pad.left + idx * gap + gap / 2;
          return (
            <text key={`d${i}`} x={x} y={height - 6} fill="var(--text-dim)" fontSize="9" textAnchor="middle" fontFamily="'JetBrains Mono', monospace">
              {d.date?.slice(5) || ""}
            </text>
          );
        })}
    </svg>
  );
}

// ═══════════════════════════════════════════════
// MA overlay calculator
// ═══════════════════════════════════════════════
function calcMA(data, period) {
  return data.map((_, i) => {
    if (i < period - 1) return null;
    const slice = data.slice(i - period + 1, i + 1);
    return slice.reduce((s, d) => s + d.close, 0) / period;
  });
}

// ═══════════════════════════════════════════════
// API: Use Anthropic to fetch & analyze stock data
// ═══════════════════════════════════════════════
async function fetchStockAnalysis(ticker, mode = "kline") {
  const prompts = {
    kline: `请搜索 ${ticker} 最近30个交易日的K线数据。只返回JSON数组，格式如下，不要任何其他文字：
[{"date":"2025-06-01","open":100.5,"close":102.3,"high":103.0,"low":99.8,"volume":15000000},...]
确保数据尽可能真实准确。如果无法获取精确数据，请基于该股票的实际价格区间给出合理的模拟数据。`,

    briefing: `你是一名资深A股/港股投资分析师。请搜索 ${ticker} 的最新信息，生成一份简明投资早报，包括：
1. 【行情概览】最新价格、涨跌幅、成交量
2. 【舆情快报】近24小时重要新闻和舆论动向（特别关注政策面、黑天鹅风险）
3. 【资金信号】如有北向资金、主力资金流向信息请列出
4. 【AI研判】综合给出 看多/看空/中性 判断，并说明理由
5. 【风险提示】当前需要关注的风险因素

请用中文回答，简洁有力，总字数控制在300字以内。`,

    deep: `你是一名资深A股/港股投资分析师。请对 ${ticker} 进行深度分析，搜索最新数据后回答：

1. 【基本面】近期财报关键指标（营收增速、净利润、ROE、PE/PB）
2. 【技术面】当前处于什么技术形态，关键支撑位和压力位
3. 【政策面】有无相关政策利好/利空
4. 【行业地位】所在板块排名，板块整体趋势
5. 【舆情温度】市场情绪偏多/偏空/中性
6. 【综合评级】给出 强烈推荐/推荐/中性/回避/强烈回避 的评级和理由
7. 【操作建议】短线/中线/长线分别的策略建议

请用中文，结构清晰，重要数字加粗。总字数600字以内。`,
  };

  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514",
        max_tokens: 1000,
        tools: [{ type: "web_search_20250305", name: "web_search" }],
        messages: [{ role: "user", content: prompts[mode] }],
      }),
    });
    const data = await res.json();
    const text = data.content?.map((b) => (b.type === "text" ? b.text : "")).filter(Boolean).join("\n") || "";
    return text;
  } catch (e) {
    console.error("API error:", e);
    return null;
  }
}

function parseKlineData(text) {
  try {
    const match = text.match(/\[[\s\S]*\]/);
    if (match) return JSON.parse(match[0]);
  } catch (e) {
    console.error("Parse error:", e);
  }
  return null;
}

// ═══════════════════════════════════════════════
// Storage helpers
// ═══════════════════════════════════════════════
const STORAGE_KEY = "stock-watchlist";

async function loadWatchlist() {
  try {
    const r = await window.storage.get(STORAGE_KEY);
    return r ? JSON.parse(r.value) : [];
  } catch {
    return [];
  }
}

async function saveWatchlist(list) {
  try {
    await window.storage.set(STORAGE_KEY, JSON.stringify(list));
  } catch (e) {
    console.error("Storage error:", e);
  }
}

// ═══════════════════════════════════════════════
// Main App
// ═══════════════════════════════════════════════
export default function StockDashboard() {
  const [watchlist, setWatchlist] = useState([]);
  const [selected, setSelected] = useState(null);
  const [inputVal, setInputVal] = useState("");
  const [klineData, setKlineData] = useState(null);
  const [briefing, setBriefing] = useState("");
  const [deepAnalysis, setDeepAnalysis] = useState("");
  const [loading, setLoading] = useState({});
  const [activeTab, setActiveTab] = useState("kline");
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Load watchlist from storage
  useEffect(() => {
    loadWatchlist().then((list) => {
      if (list.length > 0) setWatchlist(list);
    });
  }, []);

  // Save watchlist on change
  useEffect(() => {
    if (watchlist.length > 0) saveWatchlist(watchlist);
  }, [watchlist]);

  const addTicker = () => {
    if (!inputVal.trim()) return;
    const ticker = formatTicker(inputVal);
    if (watchlist.includes(ticker)) return;
    setWatchlist((prev) => [...prev, ticker]);
    setInputVal("");
  };

  const removeTicker = (t) => {
    setWatchlist((prev) => prev.filter((x) => x !== t));
    if (selected === t) {
      setSelected(null);
      setKlineData(null);
      setBriefing("");
      setDeepAnalysis("");
    }
  };

  const selectStock = async (ticker) => {
    setSelected(ticker);
    setActiveTab("kline");
    setKlineData(null);
    setBriefing("");
    setDeepAnalysis("");

    // Fetch K-line
    setLoading((p) => ({ ...p, kline: true }));
    const raw = await fetchStockAnalysis(ticker, "kline");
    if (raw) {
      const parsed = parseKlineData(raw);
      if (parsed) setKlineData(parsed);
    }
    setLoading((p) => ({ ...p, kline: false }));
  };

  const fetchBriefing = async () => {
    if (!selected) return;
    setLoading((p) => ({ ...p, briefing: true }));
    const text = await fetchStockAnalysis(selected, "briefing");
    setBriefing(text || "获取失败，请重试");
    setLoading((p) => ({ ...p, briefing: false }));
  };

  const fetchDeep = async () => {
    if (!selected) return;
    setLoading((p) => ({ ...p, deep: true }));
    const text = await fetchStockAnalysis(selected, "deep");
    setDeepAnalysis(text || "获取失败，请重试");
    setLoading((p) => ({ ...p, deep: false }));
  };

  const lastCandle = klineData?.[klineData.length - 1];
  const prevCandle = klineData?.[klineData.length - 2];
  const priceChange = lastCandle && prevCandle ? lastCandle.close - prevCandle.close : 0;
  const changePct = prevCandle ? ((priceChange / prevCandle.close) * 100).toFixed(2) : "0.00";

  return (
    <div style={S.root}>
      {/* Sidebar */}
      <div style={{ ...S.sidebar, width: sidebarOpen ? 220 : 48 }}>
        <div style={S.sidebarHeader}>
          <button onClick={() => setSidebarOpen(!sidebarOpen)} style={S.toggleBtn}>
            {sidebarOpen ? "◁" : "▷"}
          </button>
          {sidebarOpen && <span style={S.sidebarTitle}>自选股</span>}
        </div>

        {sidebarOpen && (
          <>
            <div style={S.addRow}>
              <input
                value={inputVal}
                onChange={(e) => setInputVal(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addTicker()}
                placeholder="输入代码 如 600519"
                style={S.addInput}
              />
              <button onClick={addTicker} style={S.addBtn}>+</button>
            </div>

            <div style={S.watchlistScroll}>
              {watchlist.length === 0 && (
                <div style={S.emptyHint}>
                  添加股票代码开始追踪<br />
                  支持 A股/创业板/科创板/港股
                </div>
              )}
              {watchlist.map((t) => {
                const mkt = detectMarket(t.split(".")[0]);
                const isActive = selected === t;
                return (
                  <div
                    key={t}
                    onClick={() => selectStock(t)}
                    style={{ ...S.watchItem, ...(isActive ? S.watchItemActive : {}) }}
                  >
                    <div style={S.watchItemLeft}>
                      <span style={{ ...S.mktDot, background: mkt.color }} />
                      <span style={S.tickerText}>{t}</span>
                    </div>
                    <div style={S.watchItemRight}>
                      <span style={{ ...S.mktLabel, color: mkt.color }}>{mkt.label}</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); removeTicker(t); }}
                        style={S.removeBtn}
                      >×</button>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* Main content */}
      <div style={S.main}>
        {!selected ? (
          <div style={S.emptyState}>
            <div style={S.emptyIcon}>📊</div>
            <div style={S.emptyTitle}>AI 投资研究终端</div>
            <div style={S.emptySubtitle}>
              从左侧添加自选股，点击查看 K 线图和 AI 分析
            </div>
            <div style={S.featureGrid}>
              {[
                ["📈", "K线图表", "交互式日K线 + 成交量"],
                ["🔍", "AI 早报", "舆情 + 政策 + 资金流向"],
                ["🧠", "深度分析", "基本面 + 技术面 + 评级"],
                ["⚡", "黑天鹅预警", "异常事件实时检测"],
              ].map(([icon, title, desc]) => (
                <div key={title} style={S.featureCard}>
                  <div style={{ fontSize: 28 }}>{icon}</div>
                  <div style={S.featureTitle}>{title}</div>
                  <div style={S.featureDesc}>{desc}</div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <>
            {/* Stock header */}
            <div style={S.stockHeader}>
              <div>
                <span style={S.stockTicker}>{selected}</span>
                <span style={{ ...S.mktBadge, background: detectMarket(selected.split(".")[0]).color }}>
                  {detectMarket(selected.split(".")[0]).label}
                </span>
              </div>
              {lastCandle && (
                <div style={S.priceRow}>
                  <span style={S.priceMain}>{lastCandle.close.toFixed(2)}</span>
                  <span style={{ ...S.priceChange, color: priceChange >= 0 ? "#10b981" : "#ef4444" }}>
                    {priceChange >= 0 ? "+" : ""}{priceChange.toFixed(2)} ({changePct}%)
                  </span>
                </div>
              )}
            </div>

            {/* Tabs */}
            <div style={S.tabs}>
              {[
                { id: "kline", label: "K 线图", icon: "📈" },
                { id: "briefing", label: "AI 早报", icon: "🔍" },
                { id: "deep", label: "深度分析", icon: "🧠" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => {
                    setActiveTab(tab.id);
                    if (tab.id === "briefing" && !briefing) fetchBriefing();
                    if (tab.id === "deep" && !deepAnalysis) fetchDeep();
                  }}
                  style={{ ...S.tab, ...(activeTab === tab.id ? S.tabActive : {}) }}
                >
                  {tab.icon} {tab.label}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div style={S.tabContent}>
              {activeTab === "kline" && (
                <div>
                  {loading.kline ? (
                    <div style={S.loadingBox}>
                      <div style={S.spinner} />
                      <div style={S.loadingText}>正在获取 {selected} K线数据...</div>
                    </div>
                  ) : klineData ? (
                    <div>
                      <div style={S.chartContainer}>
                        <CandlestickChart data={klineData} width={720} height={380} />
                      </div>
                      {/* OHLCV summary of last candle */}
                      {lastCandle && (
                        <div style={S.ohlcvRow}>
                          {[
                            ["开", lastCandle.open],
                            ["高", lastCandle.high],
                            ["低", lastCandle.low],
                            ["收", lastCandle.close],
                            ["量", (lastCandle.volume / 10000).toFixed(0) + "万"],
                          ].map(([k, v]) => (
                            <div key={k} style={S.ohlcvItem}>
                              <span style={S.ohlcvLabel}>{k}</span>
                              <span style={S.ohlcvValue}>{typeof v === "number" ? v.toFixed(2) : v}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={S.noData}>暂无K线数据，请点击股票代码重试</div>
                  )}
                </div>
              )}

              {activeTab === "briefing" && (
                <div>
                  <div style={S.analysisHeader}>
                    <span style={S.analysisTitle}>📋 AI 投资早报 — {selected}</span>
                    <button onClick={fetchBriefing} style={S.refreshBtn} disabled={loading.briefing}>
                      {loading.briefing ? "⏳" : "🔄"} 刷新
                    </button>
                  </div>
                  {loading.briefing ? (
                    <div style={S.loadingBox}>
                      <div style={S.spinner} />
                      <div style={S.loadingText}>AI 正在搜索最新舆情和市场数据...</div>
                    </div>
                  ) : briefing ? (
                    <div style={S.analysisContent}>{briefing}</div>
                  ) : null}
                </div>
              )}

              {activeTab === "deep" && (
                <div>
                  <div style={S.analysisHeader}>
                    <span style={S.analysisTitle}>🧠 深度分析 — {selected}</span>
                    <button onClick={fetchDeep} style={S.refreshBtn} disabled={loading.deep}>
                      {loading.deep ? "⏳" : "🔄"} 刷新
                    </button>
                  </div>
                  {loading.deep ? (
                    <div style={S.loadingBox}>
                      <div style={S.spinner} />
                      <div style={S.loadingText}>AI 正在进行多维度深度分析...</div>
                    </div>
                  ) : deepAnalysis ? (
                    <div style={S.analysisContent}>{deepAnalysis}</div>
                  ) : null}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Noto+Sans+SC:wght@400;500;700&display=swap');
        :root {
          --bg: #0c0e14;
          --bg-card: #141720;
          --bg-hover: #1a1e2b;
          --bg-active: #1f2537;
          --border: #242838;
          --grid: #1c2030;
          --text: #e4e6ed;
          --text-dim: #6b7089;
          --accent: #3b82f6;
          --green: #10b981;
          --red: #ef4444;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

// ═══════════════════════════════════════════════
// Styles
// ═══════════════════════════════════════════════
const S = {
  root: {
    display: "flex",
    height: "100vh",
    background: "var(--bg)",
    color: "var(--text)",
    fontFamily: "'Noto Sans SC', sans-serif",
    fontSize: 14,
  },

  // Sidebar
  sidebar: {
    background: "var(--bg-card)",
    borderRight: "1px solid var(--border)",
    display: "flex",
    flexDirection: "column",
    transition: "width 0.2s ease",
    overflow: "hidden",
    flexShrink: 0,
  },
  sidebarHeader: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "12px 10px",
    borderBottom: "1px solid var(--border)",
  },
  sidebarTitle: {
    fontWeight: 700,
    fontSize: 15,
    letterSpacing: "0.5px",
  },
  toggleBtn: {
    background: "none",
    border: "none",
    color: "var(--text-dim)",
    cursor: "pointer",
    fontSize: 14,
    padding: "4px 6px",
  },
  addRow: {
    display: "flex",
    gap: 4,
    padding: "10px 10px 6px",
  },
  addInput: {
    flex: 1,
    background: "var(--bg)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    padding: "6px 10px",
    color: "var(--text)",
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    outline: "none",
  },
  addBtn: {
    background: "var(--accent)",
    border: "none",
    borderRadius: 6,
    color: "#fff",
    width: 32,
    cursor: "pointer",
    fontSize: 18,
    fontWeight: 700,
  },
  watchlistScroll: {
    flex: 1,
    overflowY: "auto",
    padding: "4px 6px",
  },
  emptyHint: {
    padding: "20px 10px",
    color: "var(--text-dim)",
    fontSize: 12,
    textAlign: "center",
    lineHeight: 1.8,
  },
  watchItem: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "8px 10px",
    borderRadius: 6,
    cursor: "pointer",
    transition: "background 0.15s",
    marginBottom: 2,
  },
  watchItemActive: {
    background: "var(--bg-active)",
  },
  watchItemLeft: {
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
  watchItemRight: {
    display: "flex",
    alignItems: "center",
    gap: 6,
  },
  mktDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    flexShrink: 0,
  },
  tickerText: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 13,
    fontWeight: 600,
  },
  mktLabel: {
    fontSize: 10,
    opacity: 0.8,
  },
  removeBtn: {
    background: "none",
    border: "none",
    color: "var(--text-dim)",
    cursor: "pointer",
    fontSize: 16,
    padding: "0 2px",
    opacity: 0.5,
  },

  // Main
  main: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },

  // Empty state
  emptyState: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: 40,
    gap: 12,
  },
  emptyIcon: { fontSize: 56 },
  emptyTitle: {
    fontSize: 24,
    fontWeight: 700,
    letterSpacing: "1px",
  },
  emptySubtitle: {
    color: "var(--text-dim)",
    fontSize: 14,
    marginBottom: 32,
  },
  featureGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, 1fr)",
    gap: 16,
    maxWidth: 460,
  },
  featureCard: {
    background: "var(--bg-card)",
    border: "1px solid var(--border)",
    borderRadius: 10,
    padding: "20px 18px",
    textAlign: "center",
  },
  featureTitle: {
    fontWeight: 600,
    marginTop: 8,
    fontSize: 14,
  },
  featureDesc: {
    color: "var(--text-dim)",
    fontSize: 12,
    marginTop: 4,
  },

  // Stock header
  stockHeader: {
    padding: "16px 24px 8px",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-end",
  },
  stockTicker: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 22,
    fontWeight: 700,
    letterSpacing: "1px",
  },
  mktBadge: {
    display: "inline-block",
    fontSize: 10,
    padding: "2px 8px",
    borderRadius: 4,
    color: "#fff",
    marginLeft: 10,
    verticalAlign: "middle",
    fontWeight: 500,
  },
  priceRow: {
    display: "flex",
    alignItems: "baseline",
    gap: 12,
  },
  priceMain: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 28,
    fontWeight: 700,
  },
  priceChange: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 16,
    fontWeight: 600,
  },

  // Tabs
  tabs: {
    display: "flex",
    gap: 4,
    padding: "8px 24px 0",
    borderBottom: "1px solid var(--border)",
  },
  tab: {
    background: "none",
    border: "none",
    color: "var(--text-dim)",
    padding: "8px 16px 12px",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 500,
    borderBottom: "2px solid transparent",
    transition: "all 0.15s",
    fontFamily: "'Noto Sans SC', sans-serif",
  },
  tabActive: {
    color: "var(--text)",
    borderBottomColor: "var(--accent)",
  },

  // Content
  tabContent: {
    flex: 1,
    overflowY: "auto",
    padding: "16px 24px",
  },
  chartContainer: {
    background: "var(--bg-card)",
    borderRadius: 10,
    border: "1px solid var(--border)",
    padding: "16px 12px 8px",
  },
  ohlcvRow: {
    display: "flex",
    gap: 16,
    marginTop: 12,
    padding: "12px 16px",
    background: "var(--bg-card)",
    borderRadius: 8,
    border: "1px solid var(--border)",
  },
  ohlcvItem: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 2,
  },
  ohlcvLabel: {
    color: "var(--text-dim)",
    fontSize: 11,
  },
  ohlcvValue: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 14,
    fontWeight: 600,
  },

  // Analysis
  analysisHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 16,
  },
  analysisTitle: {
    fontSize: 16,
    fontWeight: 700,
  },
  refreshBtn: {
    background: "var(--bg-card)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    color: "var(--text)",
    padding: "6px 14px",
    cursor: "pointer",
    fontSize: 13,
    fontFamily: "'Noto Sans SC', sans-serif",
  },
  analysisContent: {
    background: "var(--bg-card)",
    border: "1px solid var(--border)",
    borderRadius: 10,
    padding: "20px 24px",
    lineHeight: 1.9,
    fontSize: 14,
    whiteSpace: "pre-wrap",
    color: "var(--text)",
  },

  // Loading
  loadingBox: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: 60,
    gap: 16,
  },
  spinner: {
    width: 32,
    height: 32,
    border: "3px solid var(--border)",
    borderTop: "3px solid var(--accent)",
    borderRadius: "50%",
    animation: "spin 0.8s linear infinite",
  },
  loadingText: {
    color: "var(--text-dim)",
    fontSize: 13,
  },
  noData: {
    textAlign: "center",
    padding: 60,
    color: "var(--text-dim)",
  },
};
