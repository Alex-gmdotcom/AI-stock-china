# openclaw × ai-hedge-fund 信源桥 — 任务配置

## 选用的 skill（从你的清单中）

| skill/插件 | 用途 | 必要性 |
|---|---|---|
| **agent-reach** | 微博/雪球/微信文章/Twitter/Reddit 跨平台搜索——补齐社交舆情与海外视角（openclaw 早报里 NYT 报道、SemiAnalysis 这类信源就来自这里） | 核心 |
| **web-tools-guide** | search→fetch 拿宏观要闻、隔夜大宗（油/铜/多晶硅夜盘） | 核心 |
| **taskflow** | 定时编排下面两个简报任务 | 核心 |
| **feishu 插件** | 把生成好的早晚报直接推给郑超——待办清单里的"飞书推送脚本"不用再写 Python 了，openclaw 顺手做 | 推荐 |
| browser-automation | 需要登录的信源（雪球深度帖等）兜底 | 可选 |

不需要：meme-maker / video-frames / canvas / diagram-maker 等媒体类。

## 桥接机制

openclaw 定时写文件，Python 侧读文件（v3.3 已接入）：
- 路径：`%USERPROFILE%\.ai-hedge-fund\external_brief.md`
- Python 侧仅读取 **18 小时内更新过** 的文件，超长截断至 4000 字
- openclaw 不在线时系统照常运行，只是快照少一个段落（安全降级）

## 任务一：盘前简报（北京时间每个交易日 07:45）

给 openclaw 的指令（粘贴进 taskflow 定时任务）：

```
用 agent-reach 和 web 搜索，编写 A股盘前外部信源简报，直接覆盖写入文件
~USERPROFILE%\.ai-hedge-fund\external_brief.md（纯文本 Markdown，总长≤4000字）。

内容分五节，每条标注【信源·时间】，没有内容的节写"无"：
1. 隔夜美股驱动：三大指数及费半涨跌的核心驱动事件（不要只给数字，要事件归因）
2. 地缘与宏观：影响 A股风险偏好的要闻（地缘冲突、美联储、汇率、重大数据）
3. 政策面：国常会/央行/证监会/工信部/发改委过去24小时发文或表态
4. 大宗夜盘：原油/铜/多晶硅价格变动及驱动
5. 观察池雷达：以下15只标的在微博/雪球/新闻中的重大讨论或事件
   （无重大事件的标的不要写）：
   巨星科技 福耀玻璃 美的集团 海信家电 伊利股份
   中际旭创 大族激光 景旺电子 新易盛 安集科技
   优必选(HK) 地平线(HK) 中国核电 三花智控 比亚迪

要求：只写事实和信源，不做投资判断；优先官方与主流媒体，社交平台内容标注
"传闻级"；同一事件不重复。
```

## 任务二：盘后简报（北京时间每个交易日 17:30）

同上格式，第1节改为"今日 A股/港股盘面事件归因"，第4节改为"今日大宗与商品股联动"，
其余不变。写入同一文件（覆盖）。

## 任务三（推荐）：飞书推送

早晚报生成后（08:10 / 18:30），让 openclaw 读取
`%USERPROFILE%\.ai-hedge-fund\briefings\` 下当日最新的
`YYYY-MM-DD-morning.md` / `-evening.md`，用 feishu 插件发给郑超。
