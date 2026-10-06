# Vibe-Trading CN

> **基座**：[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) 的二次开发 fork
> **定位**：BTC + ETH 多 Agent AI 交易平台（中文控制面板）
> **License**：继承上游 MIT

## 与上游的关系

| 模块 | 来源 | 改造点 |
|---|---|---|
| 27 个数据源 | 上游保留 | 只用 Binance / CoinGecko（BTC + ETH） |
| 回测引擎 | 上游保留 | USD-M 永续合同 |
| 多 Agent 决策 | **集成 TradingAgents** | Analyst/Researcher/Trader/Risk 4 角色 |
| MCP 工具 | 上游 74 个 + 新增 | `kb_recall`、`engram_remember`、`btc_eth_*` |
| Web UI | 上游 React + 中文改造 | 顶部导航中文 + BTC/ETH 专用面板 |
| 券商连接 | 上游 Binance USD-M + OKX | 仅这两个（BTC + ETH 实盘） |

## 优先级（10/06/2026 决策）

1. **MCP 工具链** — 连接 meiduo-workspace 的 `kb` + `engram`
2. **多 Agent 集成** — TradingAgents 4 角色（Analyst/Researcher/Trader/Risk）
3. **中文 UI** — BTC/ETH 专用控制面板

## 开发约束

- **TDD（红绿重构）** — 见 `.cursor/rules/tdd.mdc`
- **完成前验证** — 见 `.cursor/rules/verification.mdc`
- **中文规范** — 见 `.cursor/rules/chinese-style.mdc`
- **安全护栏** — 见 `.cursor/rules/safety.mdc`

## 目录结构

```
vibe-trading-cn/
├── README.md           # 本文件
├── pyproject.toml      # 依赖：vibe-trading-ai + langchain + ...
├── docs/
│   ├── design/         # 设计文档
│   ├── plans/          # 实施计划
│   └── runbooks/       # 操作手册
├── src/
│   └── vibe_trading_cn/
│       ├── agents/     # 集成 TradingAgents
│       ├── mcp/        # MCP 工具（kb recall、btc/eth）
│       └── ui/         # 中文 UI 改造
├── tests/
└── .cursor/            # Cursor 配置（继承自上级）
```

## 改造进度

- [ ] MCP 工具链（kb recall + engram）
- [ ] 多 Agent 集成
- [ ] 中文 UI 改造
- [ ] BTC/ETH 实盘对接