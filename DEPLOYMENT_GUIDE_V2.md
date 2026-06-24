# 完整部署指南 V2 — 本地 Windows + 远程服务器

> 最后更新：2026-06-11
> 本指南基于实际部署踩坑经验更新，覆盖 Windows 本地部署的全部已知问题。

---

# Part 1 · 本地部署（Windows）

## 1.1 Python 环境（已踩坑总结）

### 坑1：`python` 命令提示去微软商店下载

**原因**：微软商店的假 Python 别名劫持了命令。

**解决**：设置 → 应用 → 高级应用设置 → 应用执行别名 → 关闭所有 `python.exe` / `python3.exe`。

### 坑2：`py -3.12` 报 "cannot find the file specified"

**原因**：之前某些工具（如 Accio）安装的 Python 残留注册表项，实际文件已删除。

**解决**：用能正常工作的版本（如 `py -3.13 --version` 能输出版本号就用 3.13），或从 https://www.python.org/downloads/ 重装，**安装时勾选 "Add python.exe to PATH"**。

### 坑3：numpy 编译失败（Meson / Unknown compiler 错误）

**原因**：原版 `pyproject.toml` 锁定 numpy <2.0，旧版 numpy 没有 Python 3.13 的预编译包，需要本地编译，而 Windows 没有 C 编译器。

**解决**：编辑 `pyproject.toml`，把：

```toml
numpy = "^1.24.0"
```

改成：

```toml
numpy = ">=1.26,<3.0"
```

然后：

```powershell
del poetry.lock
poetry install
```

> 其他包如果出现同样的 Meson/compiler 编译错误，处理方式相同：放宽该包的版本约束，删 lock 重装。

## 1.2 完整安装步骤（汇总版）

```powershell
# 1. 克隆原版仓库
cd D:\AI-tool
git clone https://github.com/virattt/ai-hedge-fund.git
cd ai-hedge-fund

# 2. 解压中国版补丁（覆盖到当前目录）
tar xzf ai-hedge-fund-china-v2.tar.gz

# 3. 修改 pyproject.toml 中的 numpy 版本（见上文坑3）

# 4. 安装依赖
py -3.13 -m pip install poetry
poetry install
poetry run pip install akshare python-dateutil

# 5. 配置 API Key（见 1.3）

# 6. 验证
poetry run python -c "from src.markets.ticker import parse_ticker; print(parse_ticker('600519'))"
```

## 1.3 配置 DeepSeek API Key

在项目根目录（`pyproject.toml` 所在目录）创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=sk-你的deepseek密钥
```

> 获取 key：https://platform.deepseek.com → API Keys → 创建

**重要**：终端运行时必须指定模型参数，否则默认用 OpenAI 会报错：

```powershell
poetry run python src/main_china.py --ticker 600519 --model-name deepseek-chat --model-provider DeepSeek
```

Web 界面（见下文）会自动检测 .env 中的 key 并选择对应模型，无需手动指定。

## 1.4 可视化运行（无需终端）

新增了 Web 控制台 `src/web_app.py`。启动一次后全部操作在浏览器完成：

```powershell
poetry run python src/web_app.py
```

然后浏览器打开 **http://localhost:8000**，你会看到：

| 功能区 | 操作 |
|--------|------|
| 📋 早晚报生成 | 点按钮生成早报/晚报，快速版（免 token）和 AI 增强版二选一 |
| 🔍 个股 AI 分析 | 输入代码 → 勾选分析师 → 点"开始分析" → 显示交易决策和各分析师信号 |
| 📊 三分法观察池 | 展示全部 15 只标的的分类和评级 |

**开机自启（可选）**：创建 `start_dashboard.bat` 放到启动文件夹：

```bat
@echo off
cd /d D:\AI-tool\Stock\ai-hedge-fund
poetry run python src/web_app.py
```

> React 版高级界面（StockDashboardV3.jsx，含K线图/热力图）适合在 Claude 对话中作为 artifact 使用，或部署为独立前端（见 Part 2.5）。

## 1.5 已知数据接口问题

运行日志中可能出现的警告及处理：

| 警告 | 原因 | 影响 | 处理 |
|------|------|------|------|
| `stock_hsgt_north_net_flow_in_em` 不存在 | AKShare 改名 | 北向资金数据缺失 | 已在新版补丁中修复（多接口名回退） |
| `Length mismatch` (margin) | AKShare 返回格式变化 | 融资融券缺失 | 程序会跳过，不影响其他分析 |
| `'SH'` capital flow 错误 | 参数大小写 | 主力资金缺失 | 已在新版补丁中修复 |
| `eastmoney.com ProxyError` | 网络代理拦截 | 板块数据缺失 | 关闭系统代理/VPN 后重试，AKShare 数据源是国内站不需要代理 |
| `Error fetching company facts 404` | 原版美股接口对 A 股无效 | 无影响 | 正常现象，可忽略 |

**重要提示**：如果你开着 VPN/代理，AKShare 反而会连不上东方财富等国内数据源。建议：AKShare 数据获取时关代理，或在代理软件中把 `*.eastmoney.com`、`*.sina.com.cn` 设为直连。

---

# Part 2 · 远程服务器部署（Linux）

适合：7×24 自动运行早晚报、定时推送、多设备访问。

## 2.1 服务器选择

| 方案 | 价格参考 | 适用 |
|------|---------|------|
| 阿里云/腾讯云 轻量 2C2G | ~¥50-100/月 | 国内访问快，AKShare 直连 ✅ 推荐 |
| 海外 VPS (Vultr/DO) | ~$6-12/月 | 访问 Claude/OpenAI 方便，但 AKShare 可能慢 |

**推荐国内云服务器**：AKShare 数据源都在国内，配合 DeepSeek（国内直连）整条链路无需任何代理。

## 2.2 服务器初始化（Ubuntu 22.04/24.04）

```bash
# 1. 更新系统 + 安装基础工具
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3.12 python3.12-venv python3-pip curl

# 2. 安装 Poetry
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# 3. 克隆项目 + 解压补丁
cd ~
git clone https://github.com/virattt/ai-hedge-fund.git
cd ai-hedge-fund
# 上传补丁包: 本地执行 scp ai-hedge-fund-china-v2.tar.gz user@服务器IP:~/ai-hedge-fund/
tar xzf ai-hedge-fund-china-v2.tar.gz

# 4. 修改 numpy 版本约束（同本地部署坑3）
sed -i 's/numpy = "\^1.24.0"/numpy = ">=1.26,<3.0"/' pyproject.toml
rm -f poetry.lock

# 5. 安装依赖
poetry install
poetry run pip install akshare python-dateutil

# 6. 配置 API Key
cat > .env << 'EOF'
DEEPSEEK_API_KEY=sk-你的key
EOF

# 7. 验证
poetry run python -c "from src.markets.ticker import parse_ticker; print(parse_ticker('600519'))"
```

## 2.3 Web 控制台常驻运行（systemd）

```bash
# 创建 systemd 服务
sudo tee /etc/systemd/system/hedge-fund-web.service << EOF
[Unit]
Description=AI Hedge Fund China Web UI
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/ai-hedge-fund
ExecStart=$HOME/.local/bin/poetry run python src/web_app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 启动并设置开机自启
sudo systemctl daemon-reload
sudo systemctl enable --now hedge-fund-web

# 查看状态
sudo systemctl status hedge-fund-web
```

然后浏览器访问 `http://服务器IP:8000`。

**⚠️ 安全提醒**：

```bash
# 1. 云服务器安全组只对你自己的 IP 开放 8000 端口
# 2. 或者用 SSH 隧道访问（最安全，不暴露端口）：
#    本地执行: ssh -L 8000:localhost:8000 user@服务器IP
#    然后本地浏览器打开 http://localhost:8000
```

## 2.4 定时早晚报（crontab）

```bash
crontab -e

# 添加以下内容（服务器时区需为北京时间，或自行换算）
# 查看时区: timedatectl  | 改时区: sudo timedatectl set-timezone Asia/Shanghai

# 早报：周一到周五 08:00
0 8 * * 1-5 cd ~/ai-hedge-fund && ~/.local/bin/poetry run python -c "from src.strategy.three_categories import ThreeCategoryPool; from src.strategy.briefing_generator import generate_llm_morning_briefing; print(generate_llm_morning_briefing(ThreeCategoryPool()))" >> ~/briefings.md 2>&1

# 晚报：周一到周五 16:15
15 16 * * 1-5 cd ~/ai-hedge-fund && ~/.local/bin/poetry run python -c "from src.strategy.three_categories import ThreeCategoryPool; from src.strategy.briefing_generator import generate_llm_evening_briefing; print(generate_llm_evening_briefing(ThreeCategoryPool()))" >> ~/briefings.md 2>&1
```

> 早晚报会同时自动写入 `~/.ai-hedge-fund/briefing-log.md`（程序内置持久化），crontab 中的 `>> ~/briefings.md` 是额外副本。

## 2.5 部署 React 高级界面（可选）

如果想把 StockDashboardV3（K线图/热力图版）部署成独立网页：

```bash
# 服务器上
sudo apt install -y nodejs npm
npx create-vite@latest dashboard --template react
cd dashboard
# 把 StockDashboardV3.jsx 内容替换到 src/App.jsx
# 注意：需要把 callLLM 函数改为调用你自己的后端代理（见下），不要在前端暴露 API key

npm install && npm run build
# 用 nginx 托管 build 产物
sudo apt install -y nginx
sudo cp -r dist/* /var/www/html/
```

**API 代理（必须）**：在 `src/web_app.py` 中加一个 `/api/llm` 端点转发到 DeepSeek/Claude，前端调用自己的后端而不是直连 Anthropic。这样 key 始终在服务器端。

## 2.6 Docker 部署（可选，进阶）

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install poetry
COPY pyproject.toml ./
RUN sed -i 's/numpy = "\^1.24.0"/numpy = ">=1.26,<3.0"/' pyproject.toml && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction
RUN pip install akshare python-dateutil
COPY . .
EXPOSE 8000
CMD ["python", "src/web_app.py"]
```

```bash
docker build -t hedge-fund-china .
docker run -d --name hedge-fund -p 8000:8000 --env-file .env --restart always hedge-fund-china
```

---

# Part 3 · 飞书推送（衔接你现有工作流）

你原有系统是推送到飞书的。在服务器上把早晚报推到飞书群：

```bash
# 1. 飞书群 → 设置 → 群机器人 → 添加自定义机器人 → 复制 Webhook 地址

# 2. 在 .env 中添加
echo 'FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/你的token' >> .env
```

创建 `src/strategy/push_feishu.py`：

```python
import os, requests, sys
from dotenv import load_dotenv
load_dotenv()

def push(content: str):
    url = os.getenv("FEISHU_WEBHOOK")
    if not url:
        print("FEISHU_WEBHOOK 未配置"); return
    requests.post(url, json={"msg_type": "text", "content": {"text": content[:9000]}})

if __name__ == "__main__":
    from src.strategy.three_categories import ThreeCategoryPool
    from src.strategy import briefing_generator as bg
    t = sys.argv[1] if len(sys.argv) > 1 else "morning"
    pool = ThreeCategoryPool()
    fn = bg.generate_llm_morning_briefing if t == "morning" else bg.generate_llm_evening_briefing
    push(fn(pool))
```

crontab 改为：

```bash
0 8 * * 1-5 cd ~/ai-hedge-fund && ~/.local/bin/poetry run python -m src.strategy.push_feishu morning
15 16 * * 1-5 cd ~/ai-hedge-fund && ~/.local/bin/poetry run python -m src.strategy.push_feishu evening
```

---

# Part 4 · 快速排错速查表

| 现象 | 原因 | 解决 |
|------|------|------|
| `python` 跳转微软商店 | 别名劫持 | 关闭应用执行别名 |
| `Unable to create process` | 残留的坏 Python | 用 `py --list` 找可用版本 |
| numpy Meson 编译错误 | 版本锁定太旧 | pyproject.toml 放宽 numpy 到 >=1.26,<3.0 |
| `No module named 'src'` | 没装项目包 | `poetry install`（不带 --no-root）或设置 PYTHONPATH |
| `OPENAI_API_KEY` 报错 | 未指定模型 | 加 `--model-name deepseek-chat --model-provider DeepSeek` |
| AKShare ProxyError | 系统代理拦截国内站 | 关代理或设置国内域名直连 |
| AKShare 接口不存在 | AKShare 版本更新改名 | `poetry run pip install akshare --upgrade` + 用新补丁 |
| Web 页面打不开 | 防火墙/安全组 | 本地检查 8000 端口，云服务器开安全组或用 SSH 隧道 |

---

*AI Hedge Fund China Edition · 部署指南 V2*
