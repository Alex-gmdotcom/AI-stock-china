# v3.1 补丁包 — 数据链路修复 + 报告 fail-fast + 个股强制结论

> 2026-06-12 | 针对问题：① 增强版早晚报满屏【数据缺口】 ② 个股分析全员中性无结论

## 一、根因（doctor 诊断结论）

环境变量无代理，但东财子域（push2his / 82.push2 / 17.push2.eastmoney.com）全部
ProxyError —— **Windows 系统代理**（Clash/v2rayN"系统代理"模式，写在注册表，
requests 在 Windows 自动读取，doctor 的环境变量检测查不到）劫持了请求，且代理
规则只放行了主域。佐证：主域直连 1.1s 通过；北向/融资融券/新闻走其他主机通过；
港股日线 6912 行说明命中的是新浪备用接口。**不是 AKShare 坏了，是代理劫持了
一半东财接口。** 你本机直连东财链路是通的。

## 二、安装

```
解压到仓库根目录（D:\AI-tool\Stock\ai-hedge-fund）：
  新增  src/tools/proxy_guard.py        进程内注入 NO_PROXY，绕开系统代理
  新增  src/tools/quotes_fallback.py    腾讯行情兜底（qt.gtimg.cn，海外友好）
  新增  src/utils/decision_summary.py   个股强制结论层
  替换  src/main_china.py               港股分析师自动路由 + 模型自动选择 + 结论层
  脚本  apply_briefing_patch.py         对 briefing_generator.py 打8处补丁

然后执行：
  poetry run python apply_briefing_patch.py
  （自动备份 .bak；精确匹配，找不到目标会中止不改文件；可重复运行）
```

**同时在代理客户端加直连(DIRECT)规则**（若用 TUN/全局模式，NO_PROXY 无效，此步必做）：
`*.eastmoney.com` `qt.gtimg.cn` `*.sinajs.cn` `*.sina.com.cn` `*.10jqka.com.cn`

## 三、行为变更

1. **fail-fast**：早晚报采集阶段记录每个数据源的成败与异常原文；行情覆盖率 <60%
   时拒绝调用 LLM、不写历史日志，输出逐项错误诊断。空数据报告（尤其带评分的晚报）
   会作为假锚点进入次日早报校准回路，比不生成更有害。
2. **评分护栏**：无早报对照或存在数据缺口时，晚报第三、五节只输出"不适用"，
   禁止打分 —— 修复 6-11 晚报"基准期给自己100分A级"的问题。
3. **行情双轨**：东财失败的标的自动回退腾讯实时行情（现价/当日涨跌/量比[A股]），
   标注 `[腾讯实时·5日数据缺口]`；快照末尾附健康度行。
4. **个股强制结论**：CLI 分析末尾追加结论段（评级/信号构成/核心依据/数据缺口/
   下一步），区分"数据不足型中性"与"多空平衡型中性"——前者剔除出评级并给补救
   建议，杜绝 "hold 0 / No valid trade available" 式空结论。
5. **港股自动路由**：纯港股标的自动切换 `get_recommended_hk_analysts()` 组合
   （此函数原先 import 了但从未使用）。
6. **模型 ID 更新**：deepseek-chat → deepseek-v4-flash（旧 ID 2026-07-24 弃用）、
   claude-sonnet-4-20250514 → claude-sonnet-4-6、gpt-4.1 → gpt-5.2。

## 四、验证步骤（按顺序）

```
1. poetry run python doctor_china.py
   预期: A股日线/实时/资金流/板块 由 ProxyError 转为通过
2. poetry run python src/tools/quotes_fallback.py
   预期: 打印 600519/00148.HK 等实时报价（腾讯源自测）
3. 在 Web 控制台生成晚报
   预期: либо 含真实数据的完整晚报（末尾有快照健康度行），
   либо 明确的"生成中止+逐项错误"诊断 —— 不再有满屏占位符的假报告
4. poetry run python src/main_china.py --ticker 00148.HK
   预期: 自动切换港股分析师组合，末尾输出【00148.HK】结论段
```

## 五、注意

- `_collect_market_snapshot()` 签名变更为返回 `(文本, 健康度dict)`；
  web_app.py 若只调用 `generate_llm_*` 则不受影响，若有直接调用需同步改。
- 腾讯接口字段解析（A股量比取第49位）按公开格式实现，未在本机实测，
  若个别字段异常请把 quotes_fallback.py 自测输出贴回。
- 与 openclaw 的剩余差距是新闻事件级归因，属信源问题非代码问题：
  下一步可在快照采集中接入 ak.stock_news_em（已验证可通，0.1s）做观察池
  标的的新闻注入，或配 ANTHROPIC_API_KEY 启用联网模型。
