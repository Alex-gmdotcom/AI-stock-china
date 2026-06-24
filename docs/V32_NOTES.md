# v3.2 信息增强包 — 对齐 openclaw 信源能力（AKShare 边界内）

> 前置：必须已应用 v3.1 补丁。

## 安装
```
解压到仓库根目录：
  替换  src/tools/quotes_fallback.py     新增 fetch_tencent_indices()
  新增  src/tools/news_collector.py      财联社电报+个股新闻（多级回退）
  脚本  apply_briefing_patch_v32.py

执行：
  poetry run python apply_briefing_patch_v32.py
  （备份 .bak2；精确匹配否则中止；幂等可重复运行；
   会先检查 v3.1 是否已应用，未应用则拒绝执行）

自测：
  poetry run python src/tools/quotes_fallback.py    # 个股+全球指数
  poetry run python src/tools/news_collector.py     # 电报+个股新闻
```

## 快照新增段落（早晚报 LLM 输入）
1.【全球指数温度计】上证/深成/创业板/科创50 + 恒指/恒生科技 + 隔夜美股三大
  （腾讯源 qt.gtimg.cn，海外 IP 友好）→ 对齐 openclaw 的"隔夜美股映射"
2.【全球财经电报】财联社电报 → 东财快讯 → 新浪快讯 逐级回退，最新12条
  → 对齐 openclaw 的事件级催化剂（伊朗停火/政策发文这类信息从这里来）
3.【观察池个股新闻】东财个股新闻，A股每只2条（doctor 实测 0.1s 可通；
  港股不覆盖，自动跳过）→ 红黑榜驱动逻辑、公告排雷的信源
同时早报 prompt 要求宏观定调必须基于指数温度计、催化剂必须引用新闻原文要点，
晚报红黑榜驱动逻辑优先引用个股新闻。新闻列名按候选探测+位置兜底，
防 AKShare 版本漂移（踩坑#12 教训）。

## 仍未覆盖（信源边界）
- 大宗商品（油/铜/多晶硅）：AKShare futures 接口待接，下一迭代
- 个股主力资金/融资余额注入：等资金流接口在新网络环境下验证恢复后加
- 美股个股（费半成分股涨跌）：腾讯支持 us 个股代码，可按需扩展
