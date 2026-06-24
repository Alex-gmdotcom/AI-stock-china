# v3.4 — 个股分析根治：对照原版仓库的完整复盘与修复

> 触发问题：00148.HK 分析输出 hold/0/置信度100%/"No valid trade available"，
> 6 个分析师 5 个中性。本次按原版 virattt/ai-hedge-fund 源码逐行复盘，
> 全部修复在开发环境通过 27 项自动化验证（含在原版代码上复现 bug 与验证修复）。

## 根因链（对照原版源码的结论）

**根因一：价格链单源失败 → 决策引擎确定性短路。**
原版 risk_manager 用 get_prices 取价，取不到则 current_price=0、仓位额度=0；
原版 portfolio_manager 的 compute_allowed_actions 在 price=0 时只剩 hold，
直接预填 `hold/0/100%/"No valid trade available"`——根本不进 LLM。
那个"置信度100%"不是模型的判断，是短路路径的硬编码。
而 api_china 的港股取价只用东财 stock_hk_hist（push2his 子域，正是代理环境/
海外网络最易失败的一类），单源挂 = 整条决策链作废。

**根因二：line_items 被清空 → 价值类 agent 集体残废。**
api_bridge 此前对 CN/HK 的 search_line_items 一律返回空列表。对照原版
warren_buffett.py：盈利一致性/定价权/账面价值增长/管理层质量/内在价值
五项子分析全部以 line_items 为输入；nassim_taleb 的脆弱性分析同样依赖。
没有这条数据腿，buffett/taleb 永远只能输出低置信中性。

## 修复内容

| 修复 | 文件 | 方式 |
|---|---|---|
| 港股/A股价格新浪回退（修短路） | api_china.py | 补丁 E1/E2 |
| line items 中国实现（A股新浪三表+港股东财报表→原版LineItem，符号对齐） | line_items_china.py | 新增 |
| search_line_items 路由到中国实现 | api_bridge.py | 整文件替换 |
| Web 控制台注入强制结论层（服务端+前端） | web_app.py | 补丁 E3/E4 |
| "No valid trade available" 绊线诊断（明示这是数据故障非市场观点） | decision_summary.py | 整文件替换 |

## 验证记录（开发环境，27/27 通过）

- T1 在**原版代码**上复现 bug：空价格 → risk 限额=0 → PM 预填短路 ✓
- T2 价格恢复 → PM 进入 LLM 路径产生真实买卖决策 ✓
- T3 补丁后的 api_china：东财抛 ProxyError → 新浪回退 → 日期区间过滤正确 ✓
- T4 line items 映射（A股5期/港股5期，字段与符号断言）→ 喂入**原版 buffett
  的分析函数**，产出有效的盈利一致性结论、owner earnings=8.05e8、分红识别 ✓
- T5 bridge：api_key 剥离不再崩溃、search_line_items 正确路由、insider 安全空 ✓
- T6 结论层：短路绊线触发、纯文本无色码、web 接口可用 ✓
- E3/E4 在真实 web_app.py 上命中、编译通过、幂等 ✓

## 预期效果（00148.HK 重测基线）

修复前: 5/6 中性，hold/0/100%/"No valid trade available"，无结论
修复后: risk 拿到真实价格 → PM 进入 LLM 决策；buffett/taleb 基于真实财报
输出有依据的信号；Web 控制台输出末尾出现【结论】段（评级/信号构成/核心
依据/数据缺口）；若价格链再次断裂，结论段会显式打出"数据链路警报"而不是
伪装成市场判断。

## 已知边界

- line items 按年度报表近似 TTM；issuance_or_purchase_of_equity_shares
  暂无可靠映射（置 None，buffett 容错为"无显著增发"）
- 财报科目名映射做了多候选+子串兜底，但 AKShare 真实返回若与候选全不匹配，
  对应字段为 None（agent 逐字段容错，不会崩）——首次实跑后若发现某字段
  全 None，把 `poetry run python src/tools/line_items_china.py` 的输出发回即可补候选名

---

# v3.4.1 增量 — 基于真实数据实跑结果的修正（2026-06-13）

实跑反馈（600519 + 00148.HK 真实财报）暴露两个问题，已修复并验证：

1. **A股报告期混排** → 仅取年报。新浪报表为年初至今累计口径，Q1(272亿) 与
   年报(823亿) 混入同一序列会让 buffett 的盈利一致性/增长分析失真。
   现在只取 12-31 年报（不足2期时回退全部期间）。
2. **毛利字段全空** → 双重派生。新浪/东财报表无"毛利"行：
   gross_profit = 营业收入 − 营业成本（精确匹配"营业成本"，已验证不会误取
   "营业总成本"）；并派生 gross_margin = 毛利/收入 —— 原版 buffett 定价权
   分析实际读取的是 gross_margin。验证：5期年报毛利率扩张序列喂入原版
   analyze_pricing_power，正确识别 "Expanding gross margins"，score=4。

**网络策略（基于 6-13 doctor 报告的新诊断）**：ProxyError 已清除（NO_PROXY
生效），现在东财全系 502/RemoteDisconnected = 东财服务器拒绝海外直连 IP。
新浪/腾讯可用，核心链路（价格/快照/决策）不受影响；受影响仅板块排名与
主力资金流。三个方案见 doctor 新增的【诊断结论】输出；若选方案①（东财走
大陆代理节点），设 `AHF_DISABLE_NO_PROXY=1` 关闭 NO_PROXY 注入（proxy_guard
v3.4.1 新增开关）。doctor 同步新增新浪/腾讯备源检测项与场景化结论。

**已知边界（诚实声明）**：港股的 get_financial_metrics 走东财 spot 接口，
在拒连海外IP的网络下可能为空 → buffett 对港股的定价权/护城河两项子分析
（需要 metrics）会降级，但一致性/owner earnings/管理层/估值四项不受影响
（只依赖 line_items，已通新浪/东财报表）。彻底解决同样依赖网络方案①或②。
