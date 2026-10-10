# vibe-trading-cn 真实生产部署指南

> **版本**：v3.6+  
> **目标**：Linux (systemd) + .env 密钥隔离 + 健康检查 + journald 日志  
> **不适用**：macOS / Windows 开发机（仅作代码贡献者）

## 📋 部署前清单

| 项 | 要求 |
|---|---|
| OS | Linux（Ubuntu 20.04+ / Debian 11+ / CentOS 8+）|
| 权限 | root 或 sudo |
| Python | 3.11+ |
| 工具 | git、jq、systemd |
| 网络 | 可访问 `github.com`（git clone）+ `api.openai.com` / 国内 LLM 端点 + `binance.com`（ccxt）|

## 🚀 一键安装

```bash
# 1. 克隆 + 进入
cd /tmp && git clone https://github.com/bobing888/vibe-trading-cn.git
cd vibe-trading-cn

# 2. 跑安装脚本
sudo bash deploy/install.sh
```

安装脚本会自动：
1. 装系统依赖（python3 / git / jq）
2. 创建 `vibe-trading` 系统用户（无登录权限）
3. 创建 `/opt/vibe-trading-cn`（代码）+ `/var/lib/vibe-trading-cn`（数据）+ `/etc/vibe-trading-cn`（密钥）
4. 克隆仓库 + 装依赖到 `.venv`
5. 复制 `.env.example` → `/etc/vibe-trading-cn/secrets.env`（chmod 600）
6. 安装 3 个 systemd unit
7. 启用服务（不启动，等填密钥）

## 🔐 配置密钥

```bash
sudo vi /etc/vibe-trading-cn/secrets.env
```

至少填一个 LLM provider 密钥：

```bash
# OpenAI（推荐）
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o-mini

# 或国内（Kimi）
KIMI_API_KEY=sk-xxx
KIMI_MODEL=moonshot-v1-8k
```

可选：填数据 vendor 密钥（CCXT 走 Binance 公开 API 不需要 key；私有功能才需要）。

## ▶️ 启动 + 验证

```bash
# 启动主服务
sudo systemctl start vibe-trading-cn.service
sudo systemctl status vibe-trading-cn.service

# 看日志
journalctl -u vibe-trading-cn -f

# 健康检查 timer
systemctl list-timers vibe-trading-cn-healthcheck.timer

# 手动跑健康检查
sudo -u vibe-trading /opt/vibe-trading-cn/deploy/healthcheck.sh
```

健康检查输出示例：

```
[healthcheck] ✅ service active
[healthcheck] ✅ scheduler fresh: last run 142s ago
[healthcheck] ✅ last run all tasks OK
[healthcheck] ✅ disk usage 12%
```

## 🔧 运维命令

```bash
# 状态
sudo systemctl status vibe-trading-cn

# 重启
sudo systemctl restart vibe-trading-cn

# 停
sudo systemctl stop vibe-trading-cn

# 单次跑（不依赖 systemd）
sudo -u vibe-trading /opt/vibe-trading-cn/.venv/bin/python \
    -m src.vibe_trading_cn.cli_scheduler --once

# 单次分析某个 ticker
sudo -u vibe-trading /opt/vibe-trading-cn/.venv/bin/python \
    -m src.vibe_trading_cn.cli_analyze --ticker BTCUSDT --date 2026-10-10
```

## 📁 目录结构

```
/opt/vibe-trading-cn/           # 代码
├── .venv/                      # 虚拟环境
├── src/vibe_trading_cn/        # Python 包
├── tests/                      # 测试
├── deploy/                     # 部署配置
│   ├── .env.example
│   ├── install.sh
│   ├── healthcheck.sh
│   ├── vibe-trading-cn.service
│   ├── vibe-trading-cn-healthcheck.service
│   └── vibe-trading-cn-healthcheck.timer
└── pyproject.toml

/etc/vibe-trading-cn/
└── secrets.env                 # 真实密钥（chmod 600）

/var/lib/vibe-trading-cn/
├── decisions.jsonl             # 决策日志（持续 append）
└── scheduler-state.json        # 调度器状态（atomic write）
```

## 🛡️ 安全设计

| 措施 | 作用 |
|---|---|
| `User=vibe-trading` | 非 root 运行 |
| `NoNewPrivileges=true` | 禁止提权 |
| `ProtectSystem=strict` | 文件系统只读（除白名单）|
| `ProtectHome=true` | 屏蔽 /home /root /etc |
| `ReadWritePaths=/var/lib/vibe-trading-cn` | 仅数据目录可写 |
| `secrets.env` 600 权限 | 仅 root 可读 |
| `EnvironmentFile=` 而非命令行参数 | 密钥不进 ps 输出 |
| `journald` 日志 | 不写明文到磁盘 |

## 🔄 升级流程

```bash
cd /opt/vibe-trading-cn
sudo -u vibe-trading git pull origin main
sudo -u vibe-trading .venv/bin/pip install -e .
sudo systemctl restart vibe-trading-cn
```

## 🆘 故障排查

| 症状 | 排查命令 |
|---|---|
| 服务起不来 | `journalctl -u vibe-trading-cn -n 50` |
| scheduler 不跑 | `systemctl status vibe-trading-cn` + `journalctl` |
| LLM 调用失败 | `journalctl \| grep -i "llm\|api_key"` |
| 数据 vendor 失败 | `journalctl \| grep -i "vendor\|ccxt\|yfinance"` |
| 磁盘满 | `du -sh /var/lib/vibe-trading-cn/*` |
| 健康检查失败 | 手动跑 `sudo -u vibe-trading /opt/vibe-trading-cn/deploy/healthcheck.sh` |

## 📊 监控集成

`healthcheck.sh` 已支持 webhook 告警：

```bash
# /etc/vibe-trading-cn/secrets.env 加
ALERT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
ALERT_WEBHOOK_TYPE=wechat  # 或 dingtalk / slack
```

健康检查失败时自动 POST 告警。

## 🔗 关联

- 设计文档：`docs/design/v3-trend-prediction-system.md` §11.9 v3.4.3.9 完工报告
- 部署时间：v3.6 PR-11（scheduler 持久化）后
- 下一阶段：接基座 PyPI + 多进程 scheduler
