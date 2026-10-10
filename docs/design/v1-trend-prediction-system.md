# vibe-trading-cn 趋势预测系统 v1 设计文档

> **Status**: v1 完整设计（待 user 验收）
> **Date**: 2026-10-07
> **Author**: Cursor Assistant
> **Reviewer**: 待 user 验收
> **Supersedes**: vibe-trading-cn/README.md（改造目标一节）

---

## 1. Context（背景）

### 1.1 用户终极目标

> **经过策略分析或多 Agent 协作分析，准确预测 BTC 和 ETH 的趋势变化，并实时修正**
> **配套模块**：推荐单 / 复盘 / 盈亏跟踪统计 / 策略模块

### 1.2 上游基座能提供的（HKUDS/Vibe-Trading main）

- **API 层**（`agent/src/api/`）：26 个 route module，含 `portfolio_routes`、`sessions_routes`、`runs_routes`、`live_routes`、`attribution_routes`、`options_routes` 等
- **Agent 核心**（`agent/src/agent/`）：loop / tools / memory / trace / context / skills
- **策略层**（`agent/src/quantlib/`、`strategy_discovery/`、`strategy_store/`、`factors/`）
- **数据层**（`agent/src/providers/`、`market_data.py`、`openbb_bridge/`）
- **交易层**（`agent/src/trading/`、`live/`、`portfolio/`）
- **前端**（`frontend/src/components/`）：chat、portfolio、run、charts、options、settings

### 1.3 上游基座**没有**的（需要补）

| 缺口 | 影响 | 优先级 |
|---|---|---|
| **趋势预测主循环**（不只回测，要 live 推演） | 决策全靠单次 LLM call，无 regime 跟踪 | **P0** |
| **实时修正机制**（行情变动 → 重新评估） | 错失窗口期、止损滞后 | **P0** |
| **TradingAgents 4 角色** | 现在只有单 Agent loop | **P0** |
| **BTC/ETH 专用 UI 面板** | 27 个数据源 UI 噪音大 | **P1** |
| **结构化推荐单**（含 SL/TP/仓位） | 现在是 narrative 总结 | **P0** |
| **盈亏跟踪统计** | 缺归因分析、Sharpe / MaxDD | **P1** |
| **复盘模块** | 缺"对/错/为什么"分析 | **P1** |
| **虚拟盘 vs 实盘双轨** | 起步需要 paper trade 试水 | **P0** |
| **3 档时间粒度切换** | 5m/15m/1h/4h/1d 混在同 view | **P1** |
| **多数据源融合** | 单一 Binance 缺资金费率/多空比 | **P0** |

### 1.4 关键证据（v3.2 设计文档的结论）

`docs/design/polymarket-bot-v3-ma-bb.md` §0 给出 5 条致命回测：
- BTC 5m 市场均价差 p95 仅 1.4%（极高效，MA+BB 几乎无 alpha）
- BB+RSI 15m 实测 -16.82% / Max DD 18.69% / WR 59.3%（亏损）
- 78 次 BB+RSI+MA 回测：14 个 65%+ 胜率仍亏（胜率≠盈利）
- "5m bot 95% 准确率，5% 失败吃掉所有利润"
- MA+BB 在 BTC 5m/15m 上是"赔率不优"策略

**直接含义**：
1. **不可单独靠技术指标**——必须多源（资金费率、链上、宏观）
2. **必须分时段**——5m 跟 1d 完全不同 regime
3. **必须有风险护栏**（regime filter / 分批出场 / maker 优先）

---

## 2. 选型对比（3 方案）

| 维度 | A 全自建 | B 包装上游基座 | **C 包装 + 增强 alpha 源（选）** |
|---|---|---|---|
| 工作量 | 6-9 月 | 2-3 月 | **3-4 月** |
| 上游 26 route 复用 | 0% | 90%+ | **90%+** |
| LLM Agent 能力 | 全造 | 单 Agent | **4 角色编排** |
| 数据源 | 自接 | 仅 Binance | **Binance+OKX+Coinglass** |
| 实盘风险 | 0 | 0 | **paper trade 起步** |
| 趋势预测核心 | 自研 | 不支持 | **新增 live_loop** |
| 推荐单结构 | 自研 | narrative | **schema 化** |
| 复盘 / 盈亏统计 | 自研 | 部分 | **增强 attribution** |
| 失败时回退 | 难 | 易（用基座本身） | **易** |

**选 C 理由**：
- 上游基座已搭好 80% 脚手架（API/Agent/Portfolio/UI），重造是浪费
- 4 个 P0 缺口都是"在基座上补**业务逻辑**"，不需要自建脚手架
- 数据源增强是**纯增量**（`providers/` 目录加新 provider）
- Paper trade 模式上游已有 live trading 影子账号（`shadow_account/`），可直接复用

---

## 3. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│  前端（React + 中文改造 + BTC/ETH 专用面板）                  │
│  ┌────────┬────────┬────────┬────────┬────────┬────────┐    │
│  │ 行情   │ 推荐单 │ 复盘   │ 盈亏   │ 策略库 │ 设置   │    │
│  │ 面板   │ (新)   │ (新)   │ 统计   │ (新)   │        │    │
│  │        │        │        │ (新)   │        │        │    │
│  └───┬────┴───┬────┴───┬────┴───┬────┴───┬────┴────────┘    │
│      │        │        │        │        │                    │
│  ────┴────────┴────────┴────────┴────────┴──── (HTTP+WS) ──  │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  API 层（vibe-trading 上游 26 routes + 4 新增）              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 新增 4 个 route（本次改造核心）：                       │   │
│  │ • recommendation_routes.py — 结构化推荐单 CRUD+流推送 │   │
│  │ • review_routes.py        — 复盘会话                  │   │
│  │ • pnl_routes.py           — 盈亏统计（Sharpe/MaxDD）  │   │
│  │ • strategy_routes.py      — 策略库（保存/复盘/对比）  │   │
│  └──────────────────────────────────────────────────────┘   │
│  + 上游 26 route 全保留                                      │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  业务核心层（vibe_trading_cn/ — 本次新增）                    │
│  ┌────────────────────────────────────────────────────┐      │
│  │ trend_engine/   趋势预测主循环（vibe-trading 没的）  │      │
│  │ ├─ live_loop.py       行情 tick → 重评估 → 修正     │      │
│  │ ├─ regime.py          市场状态检测（trend/range/...） │      │
│  │ ├─ horizon.py         3 档时间粒度（日内/波段/趋势）  │      │
│  │ └─ conflation.py      多源信号融合（Binance+OKX+...）│      │
│  ├────────────────────────────────────────────────────┤      │
│  │ agents/          TradingAgents 4 角色编排            │      │
│  │ ├─ analyst.py         技术 + 链上 + 资金费率         │      │
│  │ ├─ researcher.py      宏观 + 消息面 + on-chain       │      │
│  │ ├─ trader.py          出结构化推荐单                  │      │
│  │ └─ risk.py            SL/TP/仓位 + 风险预算          │      │
│  ├────────────────────────────────────────────────────┤      │
│  │ portfolio_cn/    BTC/ETH 专用组合管理                │      │
│  │ ├─ paper_engine.py    虚拟盘撮合（P0 起步）          │      │
│  │ ├─ live_engine.py     实盘对接（Phase 2）            │      │
│  │ ├─ pnl_tracker.py     盈亏归因 + Sharpe / MaxDD      │      │
│  │ └─ reviewer.py        复盘：对/错/为什么             │      │
│  ├────────────────────────────────────────────────────┤      │
│  │ strategy/        策略模块（保存/版本/对比）          │      │
│  │ ├─ store.py           策略持久化                     │      │
│  │ ├─ version.py         策略版本管理                   │      │
│  │ └─ compare.py         多策略 A/B 对比                │      │
│  ├────────────────────────────────────────────────────┤      │
│  │ data/           多源数据融合                          │      │
│  │ ├─ binance.py         现货 K 线 + 资金费率           │      │
│  │ ├─ okx.py             永续合约 funding + OI          │      │
│  │ ├─ coinglass.py       多空比 + 爆仓数据（P0 第 3 源）│      │
│  │ └─ conflation.py      信号融合 + 时间对齐            │      │
│  └────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  复用基座（vibe-trading 上游 23 个 module）                   │
│  agent/, api/, channels/, core/, factors/, live/, memory/,   │
│  portfolio/, providers/, quantlib/, session/, skills/,       │
│  swarm/, tools/, trading/ ...                                │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 数据流（一次完整决策）

```
[1] 行情 tick (Binance WS)
   └→ trend_engine.live_loop 接收
       ├→ 触发条件：价格变动 > 0.3% OR 资金费率翻转 OR 新 K 线
       └→ 调 analyst_agent 评估

[2] Analyst Agent（多源并行）
   ├─ K 线 + 技术指标（10+）
   ├─ 链上数据（ETH gas / BTC 流出）
   ├─ 资金费率 + OI 变化
   └─ 消息面（X/Twitter API，Phase 2）
   ↓ 输出：bullish / bearish / neutral + 置信度

[3] Researcher Agent
   ├─ 宏观背景（DXY / 美债 / 黄金 / 标普）
   ├─ 时间粒度 context（3 档切其一）
   └─ 历史相似形态
   ↓ 输出：narrative 总结 + 调整后方向

[4] Trader Agent
   ├─ 接 Analyst + Researcher 输出
   ├─ 套 regime filter（trend / range / volatile）
   └─ 出结构化推荐单
       {action: long|short|hold, entry, SL, TP1, TP2, 仓位%, conf}

[5] Risk Agent
   ├─ 校验：仓位 ≤ 单笔上限（5%）
   ├─ 校验：当前组合回撤 ≤ 总预算（20%）
   ├─ 校验：相关品种暴露（BTC+ETH 同向不超过 8%）
   └─ 通过 → 写入 recommendation 表 + 推前端 WebSocket

[6] 推荐单落库
   ├→ recommendation_routes.WS 推前端
   ├→ 用户在 UI 看：行情 + 推荐单 + 当前 PnL
   └→ 用户接受 / 拒绝 / 修改

[7] Paper Engine 撮合（虚拟盘）
   ├─ 接收用户的"接受推荐"动作
   ├─ 模拟成交（按推荐时点 + 0.05% 滑点）
   ├─ 写入 paper_trades 表
   └─ 持续跟踪 PnL（mark-to-market 每 30s）

[8] 复盘（每小时 / 每日 / 用户手动触发）
   ├─ 收集：所有推荐 + 所有 paper trades + 行情快照
   ├─ 计算：单笔 PnL、Sharpe、MaxDD、胜率、盈亏比
   ├─ 对比：实际 vs 推荐（按/没按推荐做的差异）
   └─ 输出：review_routes 推送，UI 复盘 tab 显示
```

---

## 5. 模块详细设计

### 5.1 trend_engine（新增 — 趋势预测核心）

```python
# vibe_trading_cn/trend_engine/live_loop.py
class TrendLoop:
    """实时趋势预测主循环。

    与上游 agent/loop.py 的区别：
    - 上游 loop 是「单次任务执行」（用户问 → 调工具 → 回答）
    - 本 loop 是「持续 tick → 重评估 → 修正」（无终止）
    """
    async def start(self, symbols: list[str], horizons: list[Horizon]):
        """订阅行情，启动 3 档时间粒度 × N 品种的并行评估。"""
        for sym in symbols:
            for hz in horizons:
                task = asyncio.create_task(self._run_symbol_horizon(sym, hz))
                self._tasks[(sym, hz)] = task

    async def _run_symbol_horizon(self, symbol, horizon):
        """单 symbol × 单 horizon 的循环。"""
        while not self._stop.is_set():
            # 1. 拉最新行情（binance WS / REST）
            tick = await self._data_fetcher.fetch(symbol, horizon)

            # 2. 触发条件判断（避免无脑每秒重算）
            if not self._should_re_evaluate(tick, symbol, horizon):
                await asyncio.sleep(self._next_tick_seconds(horizon))
                continue

            # 3. 触发 analyst + researcher + trader + risk 流水线
            recommendation = await self._pipeline.run(symbol, horizon, tick)

            # 4. 推送（写库 + WS）
            await self._publish(recommendation)

            # 5. 间隔（horizon 决定：1m→5s / 15m→30s / 1h→5m）
            await asyncio.sleep(self._interval(horizon))
```

### 5.2 agents/（TradingAgents 4 角色，集成到基座）

```python
# vibe_trading_cn/agents/analyst.py
class AnalystAgent:
    """数据汇总：技术 + 链上 + 资金费率 + 持仓。"""

    async def analyze(self, symbol: str, horizon: Horizon, tick: Tick) -> Analysis:
        # 并行拉多源（每源独立超时，整体 2s 兜底）
        async with asyncio.timeout(2.0):
            tech_task = self.tech_indicators.compute(symbol, horizon)
            funding_task = self.data.get_funding(symbol)
            oi_task = self.data.get_open_interest(symbol)
            onchain_task = self.data.get_onchain_metrics(symbol)  # Coinglass
            tech, funding, oi, onchain = await asyncio.gather(
                tech_task, funding_task, oi_task, onchain_task,
                return_exceptions=True
            )
        return Analysis(
            symbol=symbol, horizon=horizon,
            technical=tech, funding=funding, oi=oi, onchain=onchain,
            score=self._score(tech, funding, oi, onchain),  # -1 ~ +1
            confidence=self._confidence(tech, funding, oi, onchain),  # 0~1
        )
```

```python
# vibe_trading_cn/agents/risk.py
class RiskAgent:
    """风险护栏：仓位 + 组合 + 相关性。"""

    def check(self, recommendation: Recommendation, portfolio: Portfolio) -> RiskCheck:
        # 1. 单笔仓位上限（默认 5%，可配）
        if recommendation.size_pct > self.config.max_single_position_pct:
            return RiskCheck(blocked=True, reason="单笔仓位超过上限")

        # 2. 当前组合回撤（默认总预算 20%）
        current_dd = portfolio.current_drawdown
        if current_dd > self.config.max_portfolio_drawdown:
            return RiskCheck(blocked=True, reason="组合已达最大回撤")

        # 3. BTC + ETH 同向暴露（默认 8%）
        same_dir = portfolio.exposure_in_direction(recommendation.action)
        if same_dir + recommendation.size_pct > self.config.max_same_direction_pct:
            return RiskCheck(blocked=True, reason="同向暴露超限")

        # 4. 流动性检查（24h 成交额 < 阈值 → 拒绝）
        if recommendation.symbol_volume_24h < self.config.min_liquidity_usd:
            return RiskCheck(blocked=True, reason="流动性不足")

        return RiskCheck(passed=True)
```

### 5.3 数据层（3 源 + 融合）

```python
# vibe_trading_cn/data/conflation.py
class ConflationEngine:
    """多源信号融合：每源独立打分 → 加权求和 → 归一化。

    权重来自历史回测（每周自动重训，存 weights.json）。
    """
    DEFAULT_WEIGHTS = {
        "technical": 0.30,    # 技术指标
        "funding": 0.20,      # 资金费率（情绪）
        "oi": 0.15,           # 持仓量（杠杆）
        "onchain": 0.20,      # 链上（BTC 流出/流入）
        "macro": 0.10,        # 宏观（DXY / 美债）
        "news": 0.05,         # 消息面（Phase 2）
    }

    def fuse(self, signals: dict[str, float], confidences: dict[str, float]) -> float:
        weighted_sum = sum(
            signals[k] * self.weights[k] * confidences[k]
            for k in signals
        )
        return max(-1.0, min(1.0, weighted_sum))  # 钳制到 [-1, 1]
```

### 5.4 虚拟盘引擎（P0 起步）

```python
# vibe_trading_cn/portfolio_cn/paper_engine.py
class PaperTradingEngine:
    """虚拟盘：跟实盘 API 同接口，但用本地模拟撮合。

    设计：完全复用上游 live/ 模块的接口 → Phase 2 切换实盘只换实现。
    """
    async def submit(self, recommendation: Recommendation, user_action: UserAction):
        if user_action == "accept":
            # 模拟成交（按推荐时点 + 0.05% 滑点）
            fill_price = self._simulate_fill(recommendation)
            trade = PaperTrade(
                symbol=recommendation.symbol,
                side=recommendation.action,
                entry=fill_price,
                size=recommendation.size_pct * self.portfolio.total_equity,
                sl=recommendation.stop_loss,
                tp1=recommendation.take_profit_1,
                tp2=recommendation.take_profit_2,
                opened_at=datetime.utcnow(),
                status="open",
            )
            await self.repo.insert(trade)
            await self.notifier.broadcast(trade)

    async def mark_to_market(self):
        """每 30s 更新所有 open trade 的浮动盈亏。"""
        for trade in await self.repo.list_open():
            current = await self.data.get_price(trade.symbol)
            trade.unrealized_pnl = self._calc_pnl(trade, current)
            await self.repo.update(trade)
```

### 5.5 前端模块（中文 + BTC/ETH 专用）

```typescript
// frontend/src/pages-cn/Recommendations.tsx — 推荐单模块
const RecommendationsPage: React.FC = () => {
  const [recs, setRecs] = useState<Recommendation[]>([]);

  useEffect(() => {
    // WS 订阅实时推荐
    const ws = new WebSocket('/ws/recommendations');
    ws.onmessage = (e) => setRecs(prev => [JSON.parse(e.data), ...prev]);
    return () => ws.close();
  }, []);

  return (
    <Tabs defaultValue="intraday">
      <TabsList>
        <TabsTrigger value="intraday">日内 (15m/1h/4h)</TabsTrigger>
        <TabsTrigger value="swing">波段 (1d)</TabsTrigger>
        <TabsTrigger value="position">趋势 (1w)</TabsTrigger>
      </TabsList>
      <TabsContent value="intraday">
        <RecommendationList horizon="intraday" recs={recs} />
      </TabsContent>
      {/* ... */}
    </Tabs>
  );
};
```

```typescript
// frontend/src/pages-cn/Review.tsx — 复盘模块
const ReviewPage: React.FC = () => {
  return (
    <div>
      <ReviewMetrics />        {/* 胜率 / 盈亏比 / Sharpe */}
      <PnlChart />             {/* 累计 PnL 曲线 */}
      <TradeList />            {/* 历史交易列表 */}
      <ReviewNotes />          {/* LLM 复盘总结 */}
    </div>
  );
};
```

---

## 6. 状态机

```
推荐单生命周期：
  GENERATED → PUSHED → ACCEPTED → FILLED → OPEN → (TP_HIT|SL_HIT|EXPIRED) → CLOSED → REVIEWED
                ↓
            REJECTED → ARCHIVED
                ↓
            MODIFIED → ACCEPTED

虚拟盘交易状态：
  PENDING → OPEN → (TP1_HIT|TP2_HIT|SL_HIT|MANUAL_CLOSE) → CLOSED
            ↓
          EXPIRED (max hold 超过 horizon 上限)
```

---

## 7. 数据模型（SQLite / SQLAlchemy）

```python
# vibe_trading_cn/storage/models.py
class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str]              # BTC / ETH
    horizon: Mapped[str]             # intraday / swing / position
    action: Mapped[str]              # long / short / hold
    entry: Mapped[float]
    stop_loss: Mapped[float]
    take_profit_1: Mapped[float]
    take_profit_2: Mapped[float]
    size_pct: Mapped[float]          # 0-1
    confidence: Mapped[float]        # 0-1
    analyst_score: Mapped[float]     # -1 ~ +1
    researcher_narrative: Mapped[str]
    risk_check: Mapped[str]          # passed / blocked + reason
    status: Mapped[str]              # generated / pushed / accepted / ...
    generated_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]

class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"))
    symbol: Mapped[str]
    side: Mapped[str]
    entry_price: Mapped[float]
    current_price: Mapped[float]
    size_usd: Mapped[float]
    unrealized_pnl: Mapped[float]
    realized_pnl: Mapped[float | None]
    status: Mapped[str]              # open / closed
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    close_reason: Mapped[str | None]  # tp1 / tp2 / sl / manual / expired

class Strategy(Base):
    """用户保存的策略配置（参数 + 历史表现）。"""
    __tablename__ = "strategies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    version: Mapped[int]             # 每次调整 +1
    config_json: Mapped[str]         # 各 Agent 的 prompt + 权重
    parent_id: Mapped[int | None]    # 版本链
    backtest_result: Mapped[str | None]  # JSON
    is_active: Mapped[bool]
```

---

## 8. 4 阶段实施路线（writing-plans 用）

| Phase | 目标 | 周期 | 关键交付 |
|---|---|---|---|
| **P0-A** | 趋势预测主循环 + 虚拟盘撮合 | 3-4 周 | `trend_engine/` + `paper_engine/` + `recommendation_routes.py` |
| **P0-B** | 多 Agent 4 角色 + 推荐单 UI | 3-4 周 | `agents/` 4 个 + `Recommendations.tsx` + WS 推送 |
| **P0-C** | 复盘 + 盈亏统计 + 策略库 | 2-3 周 | `review_routes.py` + `pnl_routes.py` + `strategy_routes.py` + 对应 UI |
| **P1** | 多源数据接入 + 实盘对接 | 4-6 周 | `data/okx.py` + `data/coinglass.py` + `live_engine.py` |

---

## 9. 风险 & 缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 上游基座 API 变更导致 route 失效 | 中 | 高 | **Pin 依赖**（vibe-trading-ai==0.1.16） + 自有 4 路由用基座 router 注册（不直接 import） |
| Paper trade 与实盘行为不一致 | 高 | 中 | Phase 2 灰度：先 1% 仓位实盘并行跑 1 周 |
| 多 Agent 决策延迟过高（>5s） | 中 | 中 | Analyst 用本地预计算（不是每 tick 调 LLM）；Researcher/Trader/Risk 1 个 LLM call 串行 |
| 资金费率/Coinglass 限流 | 高 | 低 | 本地缓存 + 退避重试；单源失败不阻塞（其他源继续） |
| LLM 成本失控 | 中 | 中 | Trader/Risk 用小模型（gpt-5-mini）；Analyst/Researcher 可用大模型 |
| 趋势预测准确率 < 50%（不如随机） | 中 | 高 | **必须 paper trade 跑 1 个月验证**；上线标准：胜率 > 55% + 盈亏比 > 1.5 |
| 多用户同时跑趋势循环资源竞争 | 低 | 中 | 每用户独立 TrendLoop 实例 + symbol 订阅去重 |

---

## 10. 不变量（v1 设计必须保证）

1. **基座 23 个 module 全部保留**，只增不删
2. **4 个新增 API route 不 import 彼此**，全挂在基座 `api_server.py` 的 router 上
3. **Paper trade 与 Live trade 接口同形**（同样的 `submit/fill/cancel`），Phase 2 切换只换 engine
4. **3 档 horizon 数据流独立**：日内 (15m/1h/4h) / 波段 (1d) / 趋势 (1w) 互不污染
5. **风险护栏不可绕过**：Risk agent 拒绝的推荐不能进 paper trade
6. **所有推荐单落库**（即使被拒绝）—— 复盘统计的输入
7. **多源信号权重可调**（weights.json + UI 配置），不写死在代码里
8. **LLM 输出必须有结构化 schema**（Pydantic），不接受裸文本

---

## 11. 验证标准（Phase 0 完成时）

- [ ] BTC 1h 行情 WS 接入，能看到 K 线
- [ ] 4 角色 Agent 各跑通 1 个 mock 推荐单（不需要真 LLM）
- [ ] Paper engine 模拟撮合 1 笔开仓 + 1 笔 TP_HIT 平仓
- [ ] Recommendation + PaperTrade 落库
- [ ] 前端推荐单页能展示 mock 数据
- [ ] 所有新代码 TDD（先测试后实现）
- [ ] 测试覆盖率 > 80%（核心逻辑）

---

## 12. 决策点（待 user 决定）

1. **Coinglass 是否作为第 3 数据源**？（资金费率 + 多空比 + 爆仓）
   - 选 Yes：BTC/ETH 资金费率有 7d/30d 历史 → 强 alpha
   - 选 No：仅 Binance + OKX（自接 funding 即可）
2. **策略库的粒度**？策略 = (symbol × horizon × 4 角色 prompt 版本)？
3. **多用户隔离**？v1 是单用户还是 multi-tenant？
4. **回测集成**？是否在推荐生成前先跑 7d 数据回测（基座已有 backtest/）？
5. **LLM 选型**？Analyst/Researcher 用 gpt-5.5，Trader/Risk 用 gpt-5-mini？

---

## 13. 下一步

**待 user 批准后**：
1. 调 `writing-plans` skill，把 P0-A 拆成可执行任务步骤
2. 创建 `docs/plans/p0-a-trend-loop.md`
3. 开始 TDD：先 `tests/trend_engine/test_live_loop.py` 红 → 绿 → 重构
