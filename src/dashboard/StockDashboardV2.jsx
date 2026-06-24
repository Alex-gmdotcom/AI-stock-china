import { useState, useEffect, useCallback } from "react";

const CAT = {
  V: { label: "估值", en: "Value", color: "#3b82f6", bg: "rgba(59,130,246,0.12)", freq: "每周深度检查" },
  T: { label: "趋势", en: "Trend", color: "#ef4444", bg: "rgba(239,68,68,0.12)", freq: "每日盘后必查" },
  N: { label: "叙事", en: "Narrative", color: "#eab308", bg: "rgba(234,179,8,0.12)", freq: "每周检查2次" },
};

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
  { slot:"N4",ticker:"002050.SZ",name:"三花智控",cat:"N",rating:3,logic:"热管理→机器人零部件",detail:"验证:机器人订单占比 | 证伪:产业化大幅低于预期" },
  { slot:"N5",ticker:"002594.SZ",name:"比亚迪",cat:"N",rating:4,logic:"新能源全球化+智能化",detail:"验证:海外产能+智驾 | 证伪:价格战+政策壁垒" },
];

const SK = "stock-dashboard-v2";
async function loadState() { try { const r = await window.storage.get(SK); return r ? JSON.parse(r.value) : null; } catch { return null; } }
async function saveState(s) { try { await window.storage.set(SK, JSON.stringify(s)); } catch(e) { console.error(e); } }

async function callLLM(prompt, sys) {
  try {
    const r = await fetch("https://api.anthropic.com/v1/messages", { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ model:"claude-sonnet-4-20250514", max_tokens:1000, system: sys || "", tools:[{type:"web_search_20250305",name:"web_search"}], messages:[{role:"user",content:prompt}] }) });
    const d = await r.json();
    return d.content?.filter(b=>b.type==="text").map(b=>b.text).join("\n") || "";
  } catch(e) { return "API调用失败: "+e.message; }
}

function CandlestickChart({ data }) {
  if (!data?.length) return null;
  const W=700, H=340, pad={t:16,r:56,b:28,l:8};
  const cw=W-pad.l-pad.r, ch=H-pad.t-pad.b;
  const all=data.flatMap(d=>[d.high,d.low]), mn=Math.min(...all), mx=Math.max(...all), rng=mx-mn||1;
  const pMin=mn-rng*.05, pMax=mx+rng*.05;
  const gap=cw/data.length, bw=Math.max(2,Math.min(11,gap*.7));
  const y=p=>pad.t+ch-((p-pMin)/(pMax-pMin))*ch;
  const gl=5, gs=(pMax-pMin)/gl, maxV=Math.max(...data.map(d=>d.volume||0));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{width:"100%",height:"auto"}}>
      {Array.from({length:gl+1},(_,i)=>{const p=pMin+gs*i,py=y(p);return(
        <g key={i}><line x1={pad.l} y1={py} x2={W-pad.r} y2={py} stroke="var(--grid)" strokeWidth=".5"/>
        <text x={W-pad.r+4} y={py+3} fill="var(--dim)" fontSize="10" fontFamily="monospace">{p.toFixed(2)}</text></g>
      )})}
      {data.map((d,i)=>{const x=pad.l+i*gap+gap/2,vh=maxV>0?(d.volume/maxV)*(ch*.13):0,bull=d.close>=d.open;
        return <rect key={`v${i}`} x={x-bw/2} y={pad.t+ch-vh} width={bw} height={vh} fill={bull?"rgba(16,185,129,.13)":"rgba(239,68,68,.13)"}/>})}
      {data.map((d,i)=>{const x=pad.l+i*gap+gap/2,bull=d.close>=d.open,c=bull?"#10b981":"#ef4444";
        const bt=y(Math.max(d.open,d.close)),bb=y(Math.min(d.open,d.close)),bh=Math.max(1,bb-bt);
        return(<g key={i}><line x1={x} y1={y(d.high)} x2={x} y2={y(d.low)} stroke={c} strokeWidth="1"/>
          <rect x={x-bw/2} y={bt} width={bw} height={bh} fill={bull?"transparent":c} stroke={c} strokeWidth="1" rx=".5"/></g>)})}
      {data.filter((_,i)=>i%Math.max(1,Math.floor(data.length/6))===0).map((d)=>{
        const idx=data.indexOf(d),x=pad.l+idx*gap+gap/2;
        return <text key={`d${idx}`} x={x} y={H-4} fill="var(--dim)" fontSize="9" textAnchor="middle" fontFamily="monospace">{d.date?.slice(5)||""}</text>})}
    </svg>
  );
}

function Stars({n}) { return <span style={{color:"#eab308",letterSpacing:1}}>{"★".repeat(n)}{"☆".repeat(5-n)}</span>; }

export default function App() {
  const [pool, setPool] = useState(DEFAULT_POOL);
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("kline");
  const [catFilter, setCatFilter] = useState("all");
  const [kline, setKline] = useState(null);
  const [briefing, setBriefing] = useState("");
  const [loading, setLoading] = useState({});
  const [sidebar, setSidebar] = useState(true);

  useEffect(()=>{ loadState().then(s=>{ if(s?.pool) setPool(s.pool); }); },[]);
  useEffect(()=>{ saveState({pool}); },[pool]);

  const stock = selected ? pool.find(s=>s.ticker===selected) : null;
  const filtered = catFilter==="all" ? pool : pool.filter(s=>s.cat===catFilter);

  const selectStock = async (ticker) => {
    setSelected(ticker); setTab("kline"); setKline(null); setBriefing("");
    setLoading(p=>({...p,kline:true}));
    const s = pool.find(x=>x.ticker===ticker);
    const raw = await callLLM(
      `搜索 ${s?.name||ticker} (${ticker}) 最近30个交易日K线数据。只返回JSON数组，格式：[{"date":"2026-06-01","open":100,"close":102,"high":103,"low":99,"volume":15000000},...] 不要任何其他文字。基于真实价格区间。`,
      "你是一个金融数据助手。只返回JSON，不要任何解释文字。"
    );
    try { const m=raw.match(/\[[\s\S]*\]/); if(m) setKline(JSON.parse(m[0])); } catch(e){ console.error(e); }
    setLoading(p=>({...p,kline:false}));
  };

  const fetchBriefing = async (type) => {
    if (!stock) return;
    setLoading(p=>({...p,brief:true}));
    const catInfo = CAT[stock.cat];
    const prompts = {
      morning: `你是中国市场首席策略师。搜索 ${stock.name}(${stock.ticker}) 最新信息，生成今日早盘策略推演。

该股属于【${catInfo.label}类】，${stock.logic}。${stock.detail}。

请按以下结构输出：
1. 宏观情绪定调（隔夜美股/中概/大宗映射）
2. 盘前催化剂（利多+利空，标注信号权重）
3. ${catInfo.label}类专项分析：${stock.cat==="V"?"压制因素是否边际改善？":stock.cat==="T"?"景气度信号是否延续？拐点预警？":"叙事逻辑是否被证实/证伪？"}
4. 反向假说（最大反面理由+证伪条件）
5. 今日走势剧本（主线+备用+关键节点）
字数500字以内。`,

      evening: `你是量化策略复盘员。搜索 ${stock.name}(${stock.ticker}) 今日收盘数据，生成盘后复盘。

该股属于【${catInfo.label}类】，评级 ${"★".repeat(stock.rating)}。${stock.detail}。

请按以下结构输出：
1. 收盘体温计（涨跌幅+成交额+北向资金）
2. 异动归因（驱动逻辑+是否改变分类逻辑）
3. ${catInfo.label}类盘后跟踪：${stock.cat==="V"?"压制因素变化+估值水位":stock.cat==="T"?"景气度信号+拐点预警等级(无/轻/中/重)":"叙事进展+置信度变化"}
4. 迁移检测：是否需要从${catInfo.label}类迁移到其他类别？
5. 评级建议：维持/上调/下调，原因
6. 明日校准重点
字数500字以内。`,

      deep: `你是资深投资研究员。搜索 ${stock.name}(${stock.ticker}) 进行全面深度分析。

三分法分类：【${catInfo.label}的钱】— ${stock.cat==="V"?"赚价格向内在价值回归的差价":stock.cat==="T"?"赚业绩兑现+估值抬升的双击":stock.cat==="N"?"赚想象力的钱，靠远期故事定价"}

请输出：
1. 基本面（PE/PB/ROE/营收增速）vs 同行业
2. 技术面（关键支撑/压力位，当前形态）
3. 政策面（相关政策利好/利空）
4. ${stock.cat==="V"?"压制因素深度评估：各因素改善概率和时间线":stock.cat==="T"?"景气度拐点分析：高频数据趋势，拐点概率":stock.cat==="N"?"叙事验证进度：已验证/待验证/已证伪的假设"}
5. 综合评级：强烈推荐/推荐/中性/回避（附理由）
6. 操作建议：短线/中线/长线策略
字数600字以内。`
    };
    const text = await callLLM(prompts[type]||prompts.morning);
    setBriefing(text);
    setLoading(p=>({...p,brief:false}));
  };

  const last = kline?.[kline.length-1], prev = kline?.[kline.length-2];
  const chg = last&&prev ? last.close-prev.close : 0;
  const chgPct = prev ? (chg/prev.close*100).toFixed(2) : "0.00";

  return (
    <div style={S.root}>
      <div style={{...S.side, width:sidebar?240:44}}>
        <div style={S.sideH}>
          <button onClick={()=>setSidebar(!sidebar)} style={S.togBtn}>{sidebar?"◁":"▷"}</button>
          {sidebar && <span style={S.sideT}>三分法观察池</span>}
        </div>
        {sidebar && <>
          <div style={S.catTabs}>
            {[{k:"all",l:"全部"},{k:"V",l:"🔵估值"},{k:"T",l:"🔴趋势"},{k:"N",l:"🟡叙事"}].map(c=>(
              <button key={c.k} onClick={()=>setCatFilter(c.k)}
                style={{...S.catTab,...(catFilter===c.k?S.catTabOn:{})}}>{c.l}</button>
            ))}
          </div>
          <div style={S.list}>
            {filtered.map(s=>{const ci=CAT[s.cat], act=selected===s.ticker;return(
              <div key={s.ticker} onClick={()=>selectStock(s.ticker)}
                style={{...S.item,...(act?{background:"var(--bg-act)"}:{})}}>
                <div style={S.itemTop}>
                  <span style={{...S.slot,color:ci.color,background:ci.bg}}>{s.slot}</span>
                  <span style={S.name}>{s.name}</span>
                </div>
                <div style={S.itemBot}>
                  <span style={S.ticker}>{s.ticker}</span>
                  <Stars n={s.rating}/>
                </div>
              </div>
            )})}
          </div>
        </>}
      </div>

      <div style={S.main}>
        {!selected ? (
          <div style={S.empty}>
            <div style={{fontSize:48}}>📊</div>
            <div style={S.emptyT}>三分法 AI 投资终端</div>
            <div style={S.emptyS}>点击左侧观察池中的标的，查看K线、AI早报和深度分析</div>
            <div style={S.cats}>
              {Object.entries(CAT).map(([k,v])=>(
                <div key={k} style={{...S.catCard,borderColor:v.color}}>
                  <div style={{color:v.color,fontWeight:600,fontSize:16}}>{v.label}的钱</div>
                  <div style={{color:"var(--dim)",fontSize:12,marginTop:4}}>{v.en} · {v.freq}</div>
                  <div style={{color:"var(--text)",fontSize:12,marginTop:8}}>
                    {k==="V"?"好公司+外部压制=保守定价，赚回归差价":k==="T"?"景气上行+业绩加速，赚双击":"远期想象力定价，信仰+纪律"}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div style={S.header}>
              <div>
                <span style={{...S.slotBig,color:CAT[stock.cat].color,background:CAT[stock.cat].bg}}>{stock.slot}</span>
                <span style={S.nameB}>{stock.name}</span>
                <span style={S.tickerB}>{stock.ticker}</span>
                <Stars n={stock.rating}/>
              </div>
              <div style={S.catBadge}>
                <span style={{color:CAT[stock.cat].color,fontWeight:600}}>{CAT[stock.cat].label}类</span>
                <span style={{color:"var(--dim)",fontSize:11,marginLeft:6}}>{stock.logic}</span>
              </div>
              {last && (
                <div style={S.priceRow}>
                  <span style={S.price}>{last.close.toFixed(2)}</span>
                  <span style={{...S.chg,color:chg>=0?"#10b981":"#ef4444"}}>
                    {chg>=0?"+":""}{chg.toFixed(2)} ({chgPct}%)
                  </span>
                </div>
              )}
            </div>

            <div style={S.tabs}>
              {[{id:"kline",l:"📈 K线图"},{id:"morning",l:"🌅 AI早报"},{id:"evening",l:"🌆 盘后复盘"},{id:"deep",l:"🧠 深度分析"}].map(t=>(
                <button key={t.id} onClick={()=>{setTab(t.id);if(t.id!=="kline"&&!briefing)fetchBriefing(t.id);}}
                  style={{...S.tab,...(tab===t.id?S.tabOn:{})}}>{t.l}</button>
              ))}
            </div>

            <div style={S.content}>
              {tab==="kline" && (
                loading.kline ? <Loader text={`获取 ${stock.name} K线数据...`}/> :
                kline ? <div>
                  <div style={S.chartBox}><CandlestickChart data={kline}/></div>
                  <div style={S.detailBar}>
                    <span style={{color:"var(--dim)",fontSize:12}}>{stock.detail}</span>
                  </div>
                  {last && <div style={S.ohlcv}>
                    {[["开",last.open],["高",last.high],["低",last.low],["收",last.close],["量",(last.volume/1e4).toFixed(0)+"万"]].map(([k,v])=>(
                      <div key={k} style={S.ov}><span style={S.ovL}>{k}</span><span style={S.ovV}>{typeof v==="number"?v.toFixed(2):v}</span></div>
                    ))}
                  </div>}
                </div> : <div style={S.noData}>暂无K线数据</div>
              )}
              {tab!=="kline" && (
                loading.brief ? <Loader text={`AI正在生成${tab==="morning"?"早报":tab==="evening"?"复盘":"深度分析"}...`}/> :
                briefing ? <div style={S.brief}>{briefing}</div> :
                <div style={{textAlign:"center",padding:40}}>
                  <button onClick={()=>fetchBriefing(tab)} style={S.genBtn}>
                    {tab==="morning"?"🌅 生成今日早报":tab==="evening"?"🌆 生成盘后复盘":"🧠 生成深度分析"}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
      <style>{`
        :root{--bg:#0c0e14;--card:#141720;--bg-act:#1f2537;--border:#242838;--grid:#1c2030;--text:#e4e6ed;--dim:#6b7089;--accent:#3b82f6}
        *{box-sizing:border-box;margin:0;padding:0}
        @keyframes spin{to{transform:rotate(360deg)}}
      `}</style>
    </div>
  );
}

function Loader({text}){return(
  <div style={{display:"flex",flexDirection:"column",alignItems:"center",padding:60,gap:14}}>
    <div style={{width:28,height:28,border:"3px solid var(--border)",borderTop:"3px solid var(--accent)",borderRadius:"50%",animation:"spin .8s linear infinite"}}/>
    <div style={{color:"var(--dim)",fontSize:13}}>{text}</div>
  </div>
)}

const S = {
  root:{display:"flex",height:"100vh",background:"var(--bg)",color:"var(--text)",fontFamily:"system-ui,-apple-system,sans-serif",fontSize:14},
  side:{background:"var(--card)",borderRight:"1px solid var(--border)",display:"flex",flexDirection:"column",transition:"width .2s",overflow:"hidden",flexShrink:0},
  sideH:{display:"flex",alignItems:"center",gap:8,padding:"12px 10px",borderBottom:"1px solid var(--border)"},
  sideT:{fontWeight:700,fontSize:14},
  togBtn:{background:"none",border:"none",color:"var(--dim)",cursor:"pointer",fontSize:13,padding:"4px 6px"},
  catTabs:{display:"flex",gap:2,padding:"8px 6px 4px",flexWrap:"wrap"},
  catTab:{background:"none",border:"1px solid var(--border)",borderRadius:6,color:"var(--dim)",padding:"3px 8px",cursor:"pointer",fontSize:11,fontFamily:"inherit"},
  catTabOn:{background:"var(--accent)",borderColor:"var(--accent)",color:"#fff"},
  list:{flex:1,overflowY:"auto",padding:"4px 6px"},
  item:{padding:"8px 10px",borderRadius:8,cursor:"pointer",marginBottom:2,transition:"background .15s"},
  itemTop:{display:"flex",alignItems:"center",gap:8},
  itemBot:{display:"flex",justifyContent:"space-between",alignItems:"center",marginTop:4},
  slot:{fontSize:10,fontWeight:700,padding:"1px 6px",borderRadius:4,fontFamily:"monospace"},
  name:{fontSize:13,fontWeight:600},
  ticker:{fontSize:11,color:"var(--dim)",fontFamily:"monospace"},
  main:{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"},
  empty:{flex:1,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",padding:32,gap:10},
  emptyT:{fontSize:22,fontWeight:700},
  emptyS:{color:"var(--dim)",fontSize:13,marginBottom:24},
  cats:{display:"flex",gap:14,flexWrap:"wrap",justifyContent:"center"},
  catCard:{background:"var(--card)",border:"1px solid",borderRadius:12,padding:"18px 20px",width:200,textAlign:"center"},
  header:{padding:"14px 24px 6px"},
  slotBig:{fontSize:12,fontWeight:700,padding:"2px 8px",borderRadius:5,fontFamily:"monospace",marginRight:8},
  nameB:{fontSize:20,fontWeight:700},
  tickerB:{fontSize:14,color:"var(--dim)",fontFamily:"monospace",marginLeft:10},
  catBadge:{marginTop:4,fontSize:12},
  priceRow:{display:"flex",alignItems:"baseline",gap:10,marginTop:6},
  price:{fontFamily:"monospace",fontSize:26,fontWeight:700},
  chg:{fontFamily:"monospace",fontSize:15,fontWeight:600},
  tabs:{display:"flex",gap:4,padding:"6px 24px 0",borderBottom:"1px solid var(--border)"},
  tab:{background:"none",border:"none",color:"var(--dim)",padding:"7px 14px 10px",cursor:"pointer",fontSize:13,fontWeight:500,borderBottom:"2px solid transparent",transition:"all .15s",fontFamily:"inherit"},
  tabOn:{color:"var(--text)",borderBottomColor:"var(--accent)"},
  content:{flex:1,overflowY:"auto",padding:"14px 24px"},
  chartBox:{background:"var(--card)",borderRadius:10,border:"1px solid var(--border)",padding:"14px 10px 6px"},
  detailBar:{marginTop:8,padding:"8px 12px",background:"var(--card)",borderRadius:8,border:"1px solid var(--border)"},
  ohlcv:{display:"flex",gap:14,marginTop:10,padding:"10px 14px",background:"var(--card)",borderRadius:8,border:"1px solid var(--border)"},
  ov:{display:"flex",flexDirection:"column",alignItems:"center",gap:2},
  ovL:{color:"var(--dim)",fontSize:11},
  ovV:{fontFamily:"monospace",fontSize:14,fontWeight:600},
  brief:{background:"var(--card)",border:"1px solid var(--border)",borderRadius:10,padding:"20px 24px",lineHeight:1.9,fontSize:14,whiteSpace:"pre-wrap"},
  genBtn:{background:"var(--accent)",border:"none",borderRadius:8,color:"#fff",padding:"10px 24px",cursor:"pointer",fontSize:14,fontWeight:600,fontFamily:"inherit"},
  noData:{textAlign:"center",padding:60,color:"var(--dim)"},
};
