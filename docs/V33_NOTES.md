# v3.3 修复包 — 错误日志六连修 + openclaw 信源桥

> 前置：v3.1、v3.2 已应用。

## 安装
```
解压到仓库根目录：
  替换  src/tools/api_bridge.py       kwargs 签名过滤（修 api_key 500）
  新增  src/tools/external_brief.py   openclaw 文件桥
  脚本  apply_v33_patch.py            （改 api_china / 两个agent / briefing）

执行：
  poetry run python apply_v33_patch.py
  （每文件原子化；备份 .bak3；幂等）
```

## ⚠️ 先做这一步：定位"幽灵旧版本"
日志里 `call_llm() got 'system_prompt' ... Falling back to data-only` 这段文案
**不存在于你发给我的 briefing_generator.py 中**——你机器上实际运行的和发我的
不是同一份文件（补丁可能打在了未被导入的副本上）。运行：
```
poetry run python -c "import src.strategy.briefing_generator as bg; print(bg.__file__); print('v3.1+已生效' if hasattr(bg, '_snapshot_gate') else '!!运行的是旧版本')"
```
若输出"旧版本"，按打印的真实路径对那个文件依次跑 v3.1/v3.2/v3.3 applier
（用 --path 指定），并删除多余副本。增强版早报修不修得好，取决于这一步。

## 修复明细
1. api_bridge（替换）：路由 CN/HK 时按目标函数签名统一剥离未知 kwargs。
   坑#9 当时只手工处理了 get_prices，get_financial_metrics 漏了 →
   个股分析 500。现在对全部路由函数生效。
2. A1 主力资金流：港股提前跳过（东财接口仅 sh/sz），消除 KeyError 'hk'。
3. A2 融资融券：stock_margin_account_info（doctor 实测通）优先，
   stock_margin_sse 回退——修 Length mismatch。
4. A3 板块行情：3 次重试+退避，缓解海外直连东财的 RemoteDisconnected。
5. B/C 两个 agent：SYSTEM_PROMPT 内嵌完整 JSON schema + 字段名禁令。
   换 deepseek-v4-flash 后模型返回自创字段（sentiment/risks/summary），
   Pydantic 校验 3 连败→静默回退中性，既毁个股分析又毁舆情信号。
   若硬化后仍偶发失败：改用 deepseek-v4-pro（指令遵循更强），
   或临时回退 deepseek-chat（2026-07-24 前可用）。
6. D 快照注入【外部信源简报】：读 openclaw 写的文件（见 openclaw_task.md），
   18 小时内有效，缺失安全降级。

## 验证顺序
```
1. 幽灵版本检查（上面的命令）→ 确认 bg.__file__ 与补丁目标一致
2. poetry run python src/main_china.py --ticker 00148.HK
   预期: 不再 500；自动切港股分析师组合；末尾有结论段
3. Web 控制台生成 AI 增强版早报
   预期: 不再回退 data-only；含指数温度计/电报/个股新闻段落
4. 配好 openclaw 任务一后再生成早报
   预期: 快照多出【外部信源简报（openclaw）】段
```
