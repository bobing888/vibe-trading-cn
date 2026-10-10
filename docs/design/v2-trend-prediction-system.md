# BTC/ETH 趋势预测系统 v2 设计文档

> **Status**: v2 完整设计（待 user 验收）
> **Date**: 2026-10-07
> **Author**: Cursor Assistant
> **Reviewer**: 待 user 验收
> **Supersedes**: `docs/design/v1-trend-prediction-system.md`（v1 作废）

---

## ⚠️ v2 与 v1 的根本差异

| 维度 | v1（作废） | v2（正确） |
|---|---|---|
| Agent 运行位置 | vibe-trading Python 进程内调 LLM API | **Cursor 会话内直接用 Grok** |
| LLM 成本 | 每次 tick 调 LLM API（烧钱） | **零额外成本**（复用 Cursor Grok） |
| 对抗机制 | 4 角色静态分工（不是对抗） | **Bull vs Bear 多轮辩论** |
| Dashboard | 新写 React Web UI | **Cursor Canvas（直接写 .canvas.tsx）** |
| 行情数据 | 基座接数据（代码重） | **kbkkk 服务器 MCP Server 提供** |
| 架构主体 | vibe-trading + 新增 Python 代码 | **Cursor + MCP + kbkkk 服务器** |

---

## 1. 核心设计决策（先说结论）

### 1.1 三层架构

```
┌─────────────────────────────────────────────┐
│  Layer 1：趋势分析引擎（Cursor Agent）          │
│  = 我（Cursor 主 Agent）+ Bear 子 Agent        │
│  = 用 Grok 模型，零额外成本                   │
└────────────────┬────────────────────────────┘
                 │ 对抗辩论 + 结构化输出
                 ▼
┌─────────────────────────────────────────────┐
│  Layer 2：数据管道（kbkkk 服务器 MCP Server）   │
│  = Binance WS/REST + OKX + Coinglass        │
│  = 提供行情 tick / K 线 / 资金费率 / 多空比     │
└────────────────┬────────────────────────────┘
                 │ MCP tools（btc_eth_*）
                 ▼
┌─────────────────────────────────────────────┐
│  Layer 3：展示层（Cursor Canvas）              │
│  = 实时行情图表 + 信号卡片 + 辩论记录           │
│  = Agent 每次决策后更新 .canvas.tsx           │
└─────────────────────────────────────────────┘
```

**为什么这样设计？**

- **不用任何 LLM API key**：Cursor 当前会话用的是 Grok，已含在 Cursor 订阅里
- **不用写 Web 前端**：Cursor Canvas 是 Cursor 内置的，直接写 `.canvas.tsx` 文件
- **不用额外部署**：kbkkk 服务器已跑着，只需加 MCP 工具
- **对抗检验是核心**：不是"4 个角色各自打分"，而是"2 个 Agent 真实辩论，第三者裁判"

### 1.2 对抗辩论机制（Bb vs Bear 核心）

```
┌──────────────────────────────────────────────────────────────┐
│  Round 0 — 数据准备                                          │
│  MCP 工具拉：K 线 + 资金费率 + 多空比 + OI + BTC 链上          │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Round 1 — Bull Agent 开场                                  │
│  "根据当前数据，我认为做多，理由：..."                          │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Round 1 — Bear Agent 反驳                                   │
│  "Bull 的理由站不住脚，因为：...，我建议做空"                   │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Round 2 — Bull Agent 回应反驳                                │
│  "Bear 说的 X 有道理，但 Y 反驳，因为：..."                    │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Round 2 — Bear Agent 回应回应                                │
│  "Bull 承认了 Z，我接受，但 W 还是强做空理由"                   │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  Round 3（可选）— 第三轮深化                                   │
│  双方针对剩余分歧点继续辩论                                    │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  裁判（主 Agent，即我）— 综合裁决                              │
│  综合 Bull 的最强理由 + Bear 的最强理由                        │
│  → 输出结构化推荐单（含置信度、风险点）                        │
└──────────────────────────────────────────────────────────────┘
```

**为什么不用 4 角色而用 2 角色 + 裁判？**

4 角色（Analyst/Researcher/Trader/Risk）是**分工**，不是**对抗**。分工的 Agent 各自输出自己的结论，然后合并——没有真实的冲突。

Bull vs Bear 是**真实的认知冲突**：Bull 必须找 Bear 论证的漏洞，Bear 必须找 Bull 论证的漏洞。这种对抗才能暴露单视角的盲区。

**每个角色由谁扮演？**

- **Bull Agent**：我（Cursor 主 Agent）扮演看多方
- **Bear Agent**：Cursor 子代理（`Task(subagent_type=generalPurpose)`）扮演看空方
- **裁判**：我（Cursor 主 Agent）综合双方

---

## 2. 模式 A vs 模式 B（两条实现路径）

| 维度 | 模式 A（同步对抗） | 模式 B（串行辩论） |
|---|---|---|
| **触发方式** | 用户手动点"分析当前趋势" | 定时（每 1h/4h）或行情异动自动触发 |
| **对话结构** | Bull + Bear 子代理并行 → 汇总 → 推荐单 | Bull → Bear → Bull2 → Bear2 → 汇总 |
| **实时性** | 低（需要用户触发） | 高（行情驱动） |
| **Token 消耗** | 低（用户控制） | 中（自动跑） |
| **适用场景** | 日内关键节点 | 全天候监控 |
| **与 Canvas 更新** | 用户点分析 → Canvas 更新 | 定时更新 + 推送提醒 |
| **推荐** | **v1 推荐模式 A** | v2 可扩展到模式 B |

### v1 先做模式 A（同步对抗）

理由：
- 模式 A 逻辑简单、Token 成本低、用户有控制感
- Canvas 更新频率由用户决定（按需分析）
- 验证了 Bull vs Bear 对抗框架后，再扩展模式 B（定时自动）

---

## 3. MCP Server 设计（kbkkk 服务器）

### 3.1 架构

```
kbkkk 服务器（206.187.211.211）
│
├── 已跑的服务
│   ├── Caddy 反代（kbkkk.com）
│   ├── Vibe-Trading 前端（:8899）
│   └── Vibe-Trading 后端（容器内 :8899）
│
└── 新增：MCP Server（mcp_btc_eth.py）
    │
    ├── 数据获取
    │   ├── btc_fetch_kline(symbol, period, limit)
    │   ├── btc_fetch_orderbook(symbol)
    │   ├── btc_fetch_funding(symbol)         # Binance funding rate
    │   ├── btc_fetch_open_interest(symbol)   # OKX + Binance OI
    │   ├── btc_fetch_long_short_ratio()       # Coinglass 多空比
    │   ├── btc_fetch_liquidation(symbol)      # 爆仓数据
    │   └── btc_fetch_indicators(symbol, period) # 技术指标
    │
    └── 工具注册到 Cursor MCP
        （通过 .cursor/mcp.json 指向服务器）
```

### 3.2 工具签名

```python
# mcp_btc_eth.py — MCP 工具（npx mcp install 方式暴露给 Cursor）

def btc_fetch_kline(
    symbol: str,      # "BTC" / "ETH"
    period: str,      # "1m" / "5m" / "15m" / "1h" / "4h" / "1d"
    limit: int = 100  # K 线根数
) -> dict:
    """拉 K 线数据，返回最近 N 根 OHLCV。"""
    # Binance / OKX WS + REST fallback
    ...

def btc_fetch_indicators(
    symbol: str,
    period: str,
    indicators: list[str] = ["ma", "rsi", "macd", "bollinger", "atr"]
) -> dict:
    """计算技术指标，返回每个指标的当前值。"""
    # 本地计算，不调外部 API
    ...

def btc_fetch_sentiment() -> dict:
    """拉情绪数据（资金费率 + 多空比 + 爆仓）。"""
    # Binance funding + Coinglass API
    ...

def btc_fetch_market_context() -> dict:
    """拉宏观背景（DXY / BTC.D / ETH.D）。"""
    # Binance 或公开 API
    ...
```

### 3.3 接入方式

**方案：SSH Tunnel + MCP Server**

```
Cursor（本地 Mac）
  │  SSH 隧道
  ▼
kbkkk 服务器（206.187.211.211）
  ├── :2222 → MCP Server（mcp_btc_eth.py）
  │   ├── btc_fetch_kline
  │   ├── btc_fetch_indicators
  │   ├── btc_fetch_sentiment
  │   └── btc_fetch_market_context
  │
  └── :8899 → Vibe-Trading（数据 + paper engine 复用）
```

在 `.cursor/mcp.json` 里加：

```json
{
  "mcpServers": {
    "btc_eth_data": {
      "command": "ssh",
      "args": ["-L", "9000:localhost:9000", "root@206.187.211.211", "-N"],
      "env": {}
    }
  }
}
```

**或者更简单：直接用 HTTP REST**

MCP Server 在服务器跑一个 FastAPI，提供 `/mcp/` 前缀的工具端点，Cursor 用 `curl` 或 `httpx` 调用——不用 MCP 协议那么重。

```python
# kbkkk 服务器上的 FastAPI
@app.get("/mcp/btc_fetch_kline")
async def btc_fetch_kline(symbol: str, period: str, limit: int = 100):
    ...

@app.get("/mcp/btc_fetch_indicators")
async def btc_fetch_indicators(symbol: str, period: str):
    ...
```

Cursor Agent 直接 `curl http://kbkkk.com/mcp/btc_fetch_kline?symbol=BTC&period=1h` 调用。

### 3.4 为什么不直接用 ccxt / akshare / python-binance？

| 方案 | 问题 |
|---|---|
| ccxt | 部署在本地 Mac，每次运行要装依赖 |
| python-binance | 要服务器上装，有版本兼容问题 |
| **HTTP REST（kbkkk 服务器上跑）** | **Cursor Agent 直接 curl，零依赖，服务器已有数据** |

服务器已有 Vibe-Trading 的数据管道（`agent/src/providers/`），复用其 Binance 连接代码，只加一个 FastAPI 包装。

---

## 4. Cursor Canvas Dashboard 设计

### 4.1 页面结构

```
┌─────────────────────────────────────────────────────────────────┐
│  BTC/ETH 趋势分析仪表盘                     [时间: 2026-10-07 11:05]│
├──────────────┬──────────────────────────────────────────────────┤
│              │                                                   │
│  BTC 行情    │  K 线图表（lightweight-charts 内联）               │
│  $62,450     │  ┌──────────────────────────────────────────┐   │
│  ▲ +1.2%     │  │ K 线 + MA20 + MA60 + 成交量柱             │   │
│  1h 趋势: ↑  │  └──────────────────────────────────────────┘   │
│              │                                                   │
├──────────────┤  信号卡片                                         │
│              │  ┌────────────────┐ ┌────────────────┐          │
│  ETH 行情    │  │ 🟢 Bull 信号   │ │ 🔴 Bear 信号   │          │
│  $3,240      │  │ 置信度 68%    │ │ 置信度 52%    │          │
│  ▲ +0.8%     │  │ 主理由: ...   │ │ 主理由: ...   │          │
│  1h 趋势: →  │  └────────────────┘ └────────────────┘          │
│              │                                                   │
├──────────────┤  推荐单                                           │
│  情绪面板    │  ┌──────────────────────────────────────────┐   │
│  资金费率 0.01%│  │ action: LONG                            │   │
│  多空比 52:48  │  │ entry: $62,500  SL: $61,800             │   │
│  爆仓 $12M    │  │ TP1: $63,200  TP2: $64,000              │   │
│              │  │ 仓位: 5%  置信度: 62%                     │   │
│  时间粒度    │  │ 风险: 资金费率开始转负，需监控               │   │
│  [日内][波段] │  └──────────────────────────────────────────┘   │
│  [趋势]      │                                                   │
├──────────────┤  辩论记录                                         │
│  [分析当前]  │  ┌──────────────────────────────────────────┐   │
│  [切换粒度]  │  │ Round 1 Bull: 看多，理由：MA 金叉...       │   │
│  [清空记录]  │  │ Round 1 Bear: 看空，理由：量能不足...      │   │
│              │  │ Round 2 Bull: 反驳：量能是滞后指标...       │   │
│  推荐历史    │  │ Round 2 Bear: 接受，但 RSI 已超买...        │   │
│  ─────────── │  │ 裁判综合: 做多，但 TP2 收窄至 $63,500      │   │
│  11:05 LONG  │  └──────────────────────────────────────────┘   │
│  10:30 HOLD  │                                                   │
│  09:15 SHORT │  盈亏统计（paper trade）                         │
│  ...         │  胜率 62% | 盈亏比 1.8 | Sharpe 1.4            │
└──────────────┴──────────────────────────────────────────────────┘
```

### 4.2 实时更新机制

```
每次 Bull vs Bear 对抗结束后（用户点"分析当前"）：
1. 主 Agent 综合辩论 → 结构化推荐单
2. 主 Agent 写 Cursor Canvas 文件（覆盖 .canvas.tsx）
3. Canvas 热更新，Dashboard 自动刷新

Canvas 文件位置：
/Users/hahaha/.cursor/projects/Users-hahaha-Desktop-CODE/canvases/trading-dashboard.canvas.tsx
```

### 4.3 Canvas vs Web UI（为什么选 Canvas）

| 维度 | Cursor Canvas | 独立 Web UI（v1 设计） |
|---|---|---|
| 开发成本 | 写一个 .canvas.tsx 文件 | 需要 vite + react + 路由 + 部署 |
| 模型更新 | Agent 直接写文件，热更新 | 需要 WebSocket 推送 |
| 样式系统 | cursor/canvas SDK | 自行设计 |
| 多标签 | 支持（Cursor 内置） | 需要 Tab 路由 |
| 部署 | 零部署（Cursor 内置） | 需要域名 + HTTPS |
| 实时性 | 秒级（每次决策后更新） | 同等 |
| 长期维护 | Cursor 团队维护 SDK | 自维护 |
| **适合场景** | **个人工具、实时监控仪表盘** | **多用户产品** |

**结论**：你一个人用，不需要独立 Web UI。Cursor Canvas 完全够用，且开发成本降低 90%。

---

## 5. Bull vs Bear 对抗流程（完整 Prompt 设计）

### 5.1 数据收集（工具调用）

```
主 Agent 调用（并行）：
1. btc_fetch_kline(BTC, 1h, 100) → K 线
2. btc_fetch_indicators(BTC, 1h, [ma,rsi,macd,bollinger,atr]) → 技术指标
3. btc_fetch_sentiment() → 资金费率 + 多空比 + 爆仓
4. btc_fetch_market_context() → 宏观（DXY / BTC.D）
```

### 5.2 Bull vs Bear 对抗 Prompt

```markdown
## 你的角色：Bull Agent（看多方）

你是一个经验丰富的加密货币交易员，专长是发现做多机会。

## 当前数据
[K 线数据]
[技术指标：MA20=62100, MA60=60500, RSI=58, MACD=金叉, 布林带=上轨62400/中轨61500/下轨60600, ATR=850]
[情绪数据：资金费率=0.012%, 多空比=BTC52:48, 24h爆仓=$28M多头/$15M空头]
[宏观数据：DXY=106.2, BTC.D=52.3%]

## 任务
1. 根据上述数据，给出你的看多理由（至少 3 条，有数据支撑）
2. 预判 Bear Agent（看空方）会怎么反驳你
3. 为每个反驳准备回应

## 输出格式
```
Bull 论点：
1. [论点 + 数据]
2. [论点 + 数据]
3. [论点 + 数据]

预期反驳：
- Bear 会说：[反驳点]
- 我的回应：[回应]
```

---
```

```markdown
## 你的角色：Bear Agent（看空方）

你是一个经验丰富的加密货币交易员，专长是识别风险和做空机会。

## 当前数据
[K 线数据]
[技术指标：MA20=62100, MA60=60500, RSI=58, MACD=金叉, 布林带=上轨62400/中轨61500/下轨60600, ATR=850]
[情绪数据：资金费率=0.012%, 多空比=BTC52:48, 24h爆仓=$28M多头/$15M空头]
[宏观数据：DXY=106.2, BTC.D=52.3%]

## 任务
1. 根据上述数据，给出你的看空理由（至少 3 条，有数据支撑）
2. 预判 Bull Agent（看多方的反驳）
3. 为每个反驳准备回应

## 输出格式
（同上）
```

### 5.3 裁判综合 Prompt

```markdown
## 裁判任务

你是一个中立的交易裁判，负责综合 Bull Agent 和 Bear Agent 的辩论，给出最终裁决。

## Bull 的论点
[Bull 的完整输出]

## Bear 的论点
[Bear 的完整输出]

## 裁决标准
1. 哪方的数据支撑更强？（有具体数字的 > 定性判断）
2. 哪方的反驳更有说服力？（接受对方合理点的 > 死扛的）
3. 当前市场环境更适合哪方？（趋势市 / 震荡市 / 高波动市）

## 最终输出（必须包含）

**裁决**：[做多 / 做空 / 观望]（置信度：X%）

**理由**：
- 支持裁决的最强 2 个理由

**风险点**：
- 主要风险（如果做多：下行空间多大？）

**结构化推荐单**：
```json
{
  "action": "long",        // long / short / hold
  "symbol": "BTC",
  "entry_range": [62100, 62400],  // 最佳入场区间
  "stop_loss": 60800,      // 止损位
  "take_profit_1": 63100,  // 第一止盈（0.5%风险）
  "take_profit_2": 64500,  // 第二止盈（1.5%风险）
  "size_pct": 0.05,        // 仓位 5%
  "confidence": 0.62,      // 置信度 62%
  "horizon": "intraday",   // intraday / swing / position
  "regime": "trend",       // trend / range / volatile
  "bull_strongest": "...", // Bull 最强理由（裁判引用）
  "bear_strongest": "...", // Bear 最强理由（裁判引用）
  "risks": ["..."]
}
```

**辩论质量评估**：
- Bull 论点质量：X/10
- Bear 论点质量：X/10
- 辩论是否揭示了重要风险：[是/否，具体说明]
```

---

## 6. 推荐单 + 盈亏跟踪（P0 范围）

### 6.1 数据模型（SQLite，轻量）

```python
# paper_trades.db（位于 kbkkk 服务器 /opt/vibe-trading/data/）
# 用 SQLAlchemy 或 sqlite3 直接操作

CREATE TABLE recommendations (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,           -- BTC / ETH
    horizon TEXT NOT NULL,          -- intraday / swing / position
    action TEXT NOT NULL,           -- long / short / hold
    entry_range_low REAL,
    entry_range_high REAL,
    stop_loss REAL,
    take_profit_1 REAL,
    take_profit_2 REAL,
    size_pct REAL,                  -- 0-1
    confidence REAL,                -- 0-1
    bull_strongest TEXT,
    bear_strongest TEXT,
    regime TEXT,                    -- trend / range / volatile
    bull_score REAL,               -- 裁判给 Bull 打分
    bear_score REAL,               -- 裁判给 Bear 打分
    bull_points TEXT,              -- Bull 完整论点（JSON）
    bear_points TEXT,              -- Bear 完整论点（JSON）
    debate_rounds INTEGER,         -- 辩论轮数
    status TEXT DEFAULT 'pending', -- pending / accepted / rejected / filled / closed
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE paper_trades (
    id INTEGER PRIMARY KEY,
    recommendation_id INTEGER REFERENCES recommendations(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,            -- long / short
    entry_price REAL,
    exit_price REAL,
    size_usd REAL,
    pnl_pct REAL,
    pnl_usd REAL,
    status TEXT DEFAULT 'open',   -- open / closed
    close_reason TEXT,             -- tp1 / tp2 / sl / manual / expired
    opened_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE pnl_stats (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    horizon TEXT,
    period_start TEXT,
    period_end TEXT,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate REAL,                -- 0-1
    avg_win_pct REAL,
    avg_loss_pct REAL,
    profit_loss_ratio REAL,
    sharpe_ratio REAL,
    max_drawdown REAL,
    updated_at TEXT
);
```

### 6.2 Paper Trade 撮合逻辑

```python
# kbkkk 服务器上跑，Cursor Agent 通过 HTTP 调用

@app.post("/paper/submit")
async def submit_trade(rec_id: int, user_action: str):
    """用户接受/修改推荐单 → 模拟撮合"""
    rec = db.get_recommendation(rec_id)
    if user_action == "reject":
        rec.status = "rejected"
        return {"status": "rejected"}

    # 模拟成交（entry 取推荐区间中点 + 0.02% 滑点）
    entry = (rec.entry_range_low + rec.entry_range_high) / 2 * 1.0002
    trade = PaperTrade(
        recommendation_id=rec.id,
        symbol=rec.symbol,
        side=rec.action,
        entry_price=entry,
        size_usd=PAPER_CAPITAL * rec.size_pct,
        status="open",
        opened_at=datetime.utcnow().isoformat(),
    )
    db.insert(trade)
    rec.status = "filled"
    return {"status": "filled", "entry": entry, "trade_id": trade.id}
```

### 6.3 盈亏统计计算

```python
@app.get("/paper/stats")
async def get_pnl_stats(symbol: str | None = None):
    """计算 PnL 统计"""
    trades = db.list_closed_trades(symbol=symbol)
    if not trades:
        return {"total": 0, "stats": None}

    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]

    returns = [t.pnl_pct for t in trades]
    sharpe = compute_sharpe(returns) if len(returns) > 1 else 0
    max_dd = compute_max_drawdown(returns)

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": len(wins) / len(trades),
        "avg_win_pct": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
        "avg_loss_pct": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
        "profit_loss_ratio": abs(
            sum(t.pnl_pct for t in wins) / sum(t.pnl_pct for t in losses)
        ) if losses else 0,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
    }
```

---

## 7. 复盘模块（Auto-Review）

```python
@app.post("/paper/review/{trade_id}")
async def review_trade(trade_id: int):
    """对单笔交易做复盘：裁判 Agent 分析为什么会赢/输"""
    trade = db.get_trade(trade_id)
    rec = db.get_recommendation(trade.recommendation_id)

    # LLM 复盘 Prompt
    review_prompt = f"""
    交易回顾：
    - 品种：{trade.symbol}
    - 方向：{trade.side}
    - 入场：${trade.entry_price}
    - 出场：${trade.exit_price}
    - 结果：{'盈利' if trade.pnl_usd > 0 else '亏损'} ${trade.pnl_usd} ({trade.pnl_pct:.2f}%)
    - 离场原因：{trade.close_reason}

    推荐单信息：
    - Bull 论点：{rec.bull_points}
    - Bear 论点：{rec.bear_points}
    - 裁判裁决理由：{rec.bull_strongest} + {rec.bear_strongest}
    - 置信度：{rec.confidence}

    复盘问题：
    1. 交易是否按推荐执行？（{trade.side == rec.action}）
    2. 如果盈利，主要是因为什么？（Bull 赢了还是 Bear 错了？）
    3. 如果亏损，主要是因为什么？（市场反转 / 数据未预期 / 情绪突变？）
    4. 这次辩论揭示了哪些重要风险被遗漏了？
    5. 下次类似情况，Bull/Bear/裁判应该如何改进？
    """

    # 调用 Grok（通过 Cursor Agent）
    review_result = await call_grok_for_review(review_prompt)

    return {"review": review_result, "trade": trade, "recommendation": rec}
```

---

## 8. Vibe-Trading 基座的角色

### 8.1 复用 Vibe-Trading 的部分

| 基座模块 | 复用方式 |
|---|---|
| `providers/` | Binance/OKX 数据连接代码直接复用 |
| `quantlib/` | 技术指标计算（TA-Lib 或纯 Python） |
| `backtest/` | 策略回测框架（复盘时跑 7d 历史对比） |
| `live/`（shadow account） | Paper trade 撮合逻辑参考 |
| `attribution/` | 归因分析框架（P&L 拆解） |
| `portfolio/` | 组合管理（资金管理规则复用） |
| `channels/` | 多数据源接入（同 Coinglass / OKX） |

### 8.2 不复用 / 重写的部分

| 基座模块 | 决策 | 原因 |
|---|---|---|
| `agent/loop.py` | 不复用 | 单次任务 loop，不是持续 tick 循环 |
| `agent/src/agent/tools.py` | 参考接口 | MCP 工具自己定义 |
| `frontend/` | 不复用 | 全部用 Cursor Canvas 替代 |
| `sessions_routes.py` | 不复用 | 不是对话式，用推荐单表 |
| 27 个数据源 | 只用 BTC/ETH 2 个 | 基座支持 27 个，太重 |

### 8.3 复用接口的代码示例

```python
# kbkkk 服务器上，复用 vibe-trading 的 Binance provider
import sys
sys.path.insert(0, "/app/agent/src")

from providers.binance.spot import BinanceSpotProvider

class BtcEthProvider:
    """BTC/ETH 专用数据提供器，复用基座 Binance provider。"""
    def __init__(self):
        self.binance = BinanceSpotProvider()

    async def get_klines(self, symbol: str, period: str, limit: int = 100):
        # symbol: "BTCUSDT" / "ETHUSDT"
        binance_symbol = f"{symbol}USDT"
        return await self.binance.fetch_ohlcv(binance_symbol, period, limit)

    async def get_funding_rate(self, symbol: str):
        binance_symbol = f"{symbol}USDT"
        return await self.binance.fetch_funding_rate(binance_symbol)

    async def get_open_interest(self, symbol: str):
        # Binance USD-M 永续 OI
        binance_symbol = f"{symbol}USDT"
        return await self.binance.fetch_open_interest(binance_symbol)
```

---

## 9. 技术栈总览

```
┌─────────────────────────────────────────────────────────┐
│  Cursor（本地 Mac）                                      │
│  ├── 主 Agent（我）= Bull Agent + 裁判                   │
│  ├── Bear 子代理（generalPurpose）= Bear Agent           │
│  ├── MCP 工具（btc_fetch_*）= 通过 HTTP 调用服务器          │
│  └── Canvas Dashboard（.canvas.tsx）= 实时展示            │
└─────────────────────────────────────────────────────────┘
                         │
                         │ HTTP REST（curl / httpx）
                         ▼
┌─────────────────────────────────────────────────────────┐
│  kbkkk 服务器（206.187.211.211）                          │
│  ├── Caddy（已跑）                                       │
│  ├── Vibe-Trading 容器（已跑）：复用 providers / quantlib  │
│  └── 新增：FastAPI MCP Server                           │
│      ├── /mcp/btc_fetch_kline                            │
│      ├── /mcp/btc_fetch_indicators                       │
│      ├── /mcp/btc_fetch_sentiment                        │
│      ├── /mcp/btc_fetch_market_context                   │
│      ├── /paper/submit                                   │
│      ├── /paper/stats                                    │
│      ├── /paper/review/{id}                             │
│      └── /paper/trigger_check（自动止损/止盈检查）          │
└─────────────────────────────────────────────────────────┘
                         │
                         │ SQLite
                         ▼
                    paper_trades.db
```

---

## 10. v2 完整数据流

```
[用户点"分析当前"]
       │
       ▼
[主 Agent 调用 btc_fetch_*]
       │
       ▼
[数据汇总] ──────────────────────────────────┐
       │                                        │
       ▼                                        ▼
[Bull 子代理]                          [Bear 子代理]
生成看多论点                           生成看空论点
+ 预判反驳 + 准备回应                  + 预判反驳 + 准备回应
       │                                        │
       └──────────────┬─────────────────────────┘
                      ▼
              [主 Agent 裁判]
        读取双方论点 + 裁决 + 推荐单
                      │
                      ▼
              [写 paper_trades.db]
              [更新 Cursor Canvas Dashboard]
                      │
                      ▼
              [用户看 Dashboard]
              接受 / 拒绝 / 修改推荐单
                      │
                      ▼
              [paper/submit]
              虚拟盘撮合（开仓）
                      │
                      ▼
              [定时任务（每 30s）]
              检查所有 open trade：
              - 价格触发 SL → 平仓
              - 价格触发 TP1/TP2 → 分批平仓
              - 更新 unrealized_pnl
                      │
                      ▼
              [盈亏统计更新]
              用户在 Dashboard 看到实时 PnL
```

---

## 11. 实施阶段（writing-plans 用的路线图）

| Phase | 目标 | 交付物 | 周期 |
|---|---|---|---|
| **P0-v2-1** | MCP Server 基础（kbkkk 服务器） | `mcp_btc_eth.py` + 4 个 HTTP 端点 | 1 周 |
| **P0-v2-2** | Cursor Canvas Dashboard | `trading-dashboard.canvas.tsx` | 3-5 天 |
| **P0-v2-3** | Bull vs Bear 对抗逻辑 | 主 Agent Prompt + Bear 子代理 | 3-5 天 |
| **P0-v2-4** | Paper Trade 基础 | `/paper/submit` + SQLite 表 | 3-5 天 |
| **P0-v2-5** | 盈亏统计 + 自动止损止盈 | `/paper/stats` + 定时检查 | 3-5 天 |
| **P1-v2-1** | 复盘模块 | `/paper/review/{id}` + LLM 复盘 | 1 周 |
| **P1-v2-2** | 多时间粒度（3 档） | horizon 切换 + 对应 K 线 | 3-5 天 |
| **P2-v2-1** | OKX 数据源接入 | `/mcp/btc_fetch_funding_okx` | 3-5 天 |
| **P2-v2-2** | Coinglass 数据源接入 | `/mcp/btc_fetch_sentiment_coinglass` | 3-5 天 |

**总工期**：P0-v2 约 3 周，P1-v2 约 2 周，P2-v2 约 2 周 → **共约 7 周**

---

## 12. 不变量（v2 设计必须保证）

1. **Agent 用 Cursor Grok，零额外 LLM 成本**
2. **MCP 数据来自 kbkkk 服务器，不在本地 Mac 安装数据依赖**
3. **Dashboard 用 Cursor Canvas，不写独立 Web UI**
4. **Bull vs Bear 是真实辩论，不是分工**（每方必须反驳对方）
5. **Paper trade 是撮合模拟，不是策略**（模拟撮合，记录结果）
6. **所有决策落库**（推荐单 + 交易 + 盈亏），这是复盘的数据基础
7. **Vibe-Trading 基座的复用只取其数据层，不复用其 UI/对话逻辑**
8. **上线标准**：Paper trade 跑满 1 个月 + 胜率 > 55% + 盈亏比 > 1.5

---

## 13. 对抗性检验（Self-Review）

### 问题 1：Bull vs Bear 会不会变成互相抬杠，没有实质分歧？

**检验**：裁判 Prompt 里加了"接受对方合理点的 > 死扛的"评分标准。如果双方都在承认对方合理的同时坚持己见，这才是健康的辩论。如果一方无脑反对另一方，裁判会低分并注明原因。

### 问题 2：Cursor 子代理（Bearer）会不会比主 Agent（Bull）弱，导致结论不公平？

**检验**：Bear 子代理用同样的数据、同样的结构化 Prompt、同样的输出格式。差异在于"立场"而不是"智力"。裁判综合时，评分标准只看数据支撑强度，不看 Agent 身份。

### 问题 3：没有真实 LLM API 调用，成本是零，但模型能力受限于 Cursor Grok？

**检验**：Cursor Grok 是 xAI 的 Grok-4（最新版本），能力足够。v3.2 设计文档的结论——"BTC 是极高效市场，MA+BB 无 alpha"——是 Grok 跑出来的，说明 Grok 能处理这个量级的分析。

### 问题 4：如果 kbkkk 服务器挂了，趋势分析就停了？

**缓解**：MCP Server 加 HTTP 降级（如果服务器 5s 内无响应，返回缓存数据 + "数据可能过期"提示）。不影响用户看到历史推荐单。

### 问题 5：Canvas Dashboard 不是实时 WebSocket，是 Agent 写文件后刷新？

**缓解**：Cursor Canvas 文件写入后热更新，不需要手动刷新。但更新频率取决于用户点"分析当前"的频率（模式 A），或定时任务（模式 B）。对于趋势跟踪，5 分钟更新一次足够。

---

## 14. 决策点（待 user 拍板）

1. **模式 A 还是 A+B？** v1 先做同步对抗（模式 A），定时自动（模式 B）后期加。你同意吗？
2. **辩论轮数**：默认 2 轮（Bull + Bear × 2），是否需要第 3 轮深化？
3. **Paper capital**：虚拟盘初始资金设多少？（建议 $10,000 或 $100,000）
4. **时间粒度 v1 做哪个**：日内（1h/4h）先做，还是 3 档全做？
5. **Coinglass API**：需要 API key 吗？还是用免费端点？
