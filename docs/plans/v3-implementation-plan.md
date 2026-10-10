# BTC/ETH/SOL 趋势预测系统 v3.4 实现计划（多周期共振版）

> **来源**：`docs/design/v3.4-trend-prediction-system.md`（已 user 批准）
> **执行方式**：SDD（Subagent-Driven Development）
> **日期**：2026-10-07
> **预估总工时**：~8 小时（4 个任务，每任务 1.5-2h）——相比 v3.3 多 130 行换多周期

---

## 文件清单

### 新增（5 个 Python + 2 配置）

| 文件 | 职责 | 行数 | vs v3.3 |
|---|---|---|---|
| `src/vibe_trading_cn/mcp_client.py` | 连基座 + 6 周期拉取 | 100 | +20 |
| `src/vibe_trading_cn/orchestrator.py` | LangGraph 13 节点编排 | 300 | +50 |
| `src/vibe_trading_cn/observability.py` | tracelet + engram 集成 | 50 | = |
| `src/vibe_trading_cn/anomaly_detector.py` | 多周期异常（4h 反转 / 1m 插针）| 60 | +10 |
| `src/vibe_trading_cn/webhook.py` | 微信/钉钉/邮件 Webhook 推送 | 30 | = |
| `.shellward.json` | 安全策略即代码 | 30 | = |
| `.cursor/hooks/post-orchestrator.sh` | 每轮 engram remember | 20 | = |
| **总计** | | **590** | **+130** |

### 测试新增（6 个 + 3 fixture）

| 文件 | 职责 | vs v3.3 |
|---|---|---|
| `tests/test_mcp_client.py` | MCP 连接 + 6 周期拉取 | 扩 |
| `tests/test_orchestrator.py` | LangGraph 13 节点 E2E | 扩 |
| `tests/test_fusion_node.py` | v3.4 多周期加权测试 | 扩 |
| `tests/test_backtest_node.py` | v3.4 1h 主周期回测 | 扩 |
| `tests/test_anomaly_detector.py` | v3.4 多周期异常 | 扩 |
| **`tests/test_mtf_alignment.py`** ⭐v3.4 | 6 周期对齐验证 | 新加 |
| `tests/fixtures/btc_1h_2y.json` | 2 年 1h K 线 fixture | 新加 |
| `tests/fixtures/btc_1m_30d.json` | 30 天 1m K 线 fixture | 沿用 |
| `tests/fixtures/btc_1d_2y.json` | 2 年 1d K 线 fixture | 新加 |

### 修改

| 文件 | 修改内容 |
|---|---|
| `.cursor/mcp.json` | 基座 mcp_server stdio + shellward MCP |
| `pyproject.toml` | tracelet + engram + shellward + httpx + langgraph |
| `README.md` | v3.4 多周期架构 + 90% 胜率目标 |
| `.cursor/rules/vibe-trading-cn.mdc` | v3.4 多周期约定 |

---

## 任务分解（4 个，按依赖排序）

### 任务 1：基座 MCP 客户端 + 6 周期 K 线拉取

**目的**：打通 Cursor 到基座的链路，验证 6 周期 K 线（1m/5m/15m/1h/4h/1d）可拉 + tracelet/engram 集成。

- 文件：`src/vibe_trading_cn/mcp_client.py`（100 行）
- 文件：`src/vibe_trading_cn/observability.py`（50 行）
- 行为：
  - `TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]` 常量
  - `fetch_multi_tf_data(symbol)`: 1 次拉 6 周期 K 线（并发，asyncio.gather）
  - `get_market_data(symbol, interval)`: 拉单周期
  - `get_macro_series(indicators)`: 拉宏观（DXY/US10Y/VIX）
  - `run_swarm(preset_name, variables, timeframes=[...], wait_seconds=120)`: 便捷封装
  - `run_backtest(strategy, timeframe="1h", lookback_bars=100)`: 回测
  - **tracelet**：每次 call 加 span
  - **engram**：每次 call 完 engram remember

- 测试：
  - `test_call_health`: 连基座 + `list_swarm_presets` 返回 5 preset
  - `test_get_market_data_1m`: 拉 BTC 1m K 线
  - `test_get_market_data_1h`: 拉 BTC 1h K 线（**v3.4 关键**）
  - `test_get_market_data_1d`: 拉 BTC 1d K 线
  - `test_fetch_multi_tf_data`: 1 次拉 6 周期，返回 6 个 dict
  - `test_get_macro_series`: 拉 DXY + US10Y + VIX
  - `test_run_swarm_macro_with_timeframes`: macro_rates_fx_desk + timeframes=["1d", "4h"]
  - `test_run_swarm_quant_with_timeframes`: quant_strategy_desk + timeframes=["1h", "15m"]
  - `test_run_swarm_crypto_with_timeframes`: crypto_trading_desk + timeframes=["5m", "1m"]
  - `test_run_backtest_1h_main`: 100 根 1h K 线回测（**v3.4 主周期**）
  - `test_tracelet_emits_span`
  - `test_engram_remembers_call`

- 跑：`pytest tests/test_mcp_client.py tests/test_observability.py -v`
- 期望：12 passed / 0 failed

**审查重点**：
- 6 周期并发拉取延迟（asyncio.gather）
- 基座是否真的支持 1d/4h/15m 周期（**v3.4 新风险**）
- tracelet span 不阻塞主流程
- engram remember 失败不影响主流程

---

### 任务 2：LangGraph 13 节点编排 + mtf_alignment + 多周期融合

**目的**：13 节点 LangGraph 图，含 mtf_alignment_node 共振验证 + fusion_node 多周期加权 + recommendation_node 三档位。

- 文件：`src/vibe_trading_cn/orchestrator.py`（300 行）
- 行为：
  - `VibeState` TypedDict：含 symbol / multi_tf_data (6 周期) / 5 preset / mtf_alignment / fusion / backtest / recommendation / alert / position
  - 13 节点（v3.3 是 11 节点）：
    - `fetch_multi_tf_data`: 1 次拉 6 周期
    - `macro`: macro_rates_fx_desk（看 1d/4h 大周期）—— **v3.4 按周期分工**
    - `quant`: quant_strategy_desk（看 1h/15m 中周期）—— **v3.4 按周期分工**
    - `crypto`: crypto_trading_desk（看 5m/1m 短周期）—— **v3.4 按周期分工**
    - `ic`: investment_committee（6 周期综合）—— **v3.4 看全周期**
    - `risk`: risk_committee（看 1d 风险）
    - **`mtf_alignment`**: 6 周期方向一致性 → STRONG/WEAK/DIVERGENCE —— **v3.4 关键**
    - `fusion`: 多周期加权（大 70% + 中 20% + 小 10%）—— **v3.4 多周期**
    - `backtest`: 1h 主周期回测（最近 100 根 1h K 线）—— **v3.4 1h 主周期**
    - `recommendation`: 三档位 + 6 周期动量字段 —— **v3.4 加 mtf_alignment 字段**
    - **`DIVERGENCE 时不推单`** —— **v3.4 关键规则**
    - `anomaly_detection`: 多周期异常（4h 反转 + 1m 插针 + 持仓亏 1%）
    - `alert`: Webhook 推送
  - `app = graph.compile()`

- 测试（13 个）：
  - `test_build_graph_compiles`: StateGraph compile 成功
  - `test_fetch_multi_tf_data_node`: 拉 6 周期，state["multi_tf_data"] 含 6 周期
  - `test_presets_run_in_parallel`: 4 preset 并发
  - `test_risk_receives_4_inputs`
  - **`test_mtf_alignment_strong`**: 6 周期全 UP → STRONG —— **v3.4 关键**
  - **`test_mtf_alignment_weak`**: 大中 UP，小 DOWN → WEAK —— **v3.4 关键**
  - **`test_mtf_alignment_divergence`**: 大 UP / 中 DOWN / 小 UP → DIVERGENCE —— **v3.4 关键**
  - **`test_fusion_node_diverge_hold`**: DIVERGENCE → HOLD —— **v3.4 关键**
  - `test_fusion_node_weighted_voting`: STRONG + BUYs → score > 0.5 → BUY
  - `test_fusion_node_weak_position_halved`: WEAK → position_pct=0.5%
  - `test_backtest_node_validates_fusion_1h`: 1h 主周期回测
  - `test_recommendation_node_generates_3level_with_mtf`: 含 mtf_alignment 字段
  - `test_recommendation_node_skips_on_divergence`: DIVERGENCE → None
  - `test_anomaly_detection_large_tf_reversal`: 4h 反转 → LARGE_TF_REVERSAL
  - `test_run_btc_e2e`: 跑 BTC 全流程 13 节点

- 跑：`pytest tests/test_orchestrator.py tests/test_mtf_alignment.py -v`
- 期望：15 passed / 0 failed

**审查重点**：
- mtf_alignment 是决策门（DIVERGENCE 直接 HOLD，不能 fusion 后再判断）
- 5 preset 按周期分工（不是都看 1m）
- 1h 主周期用 multi_tf_data["1h"]["close"] 计算三档位
- LangGraph 异步集成（asyncio + StateGraph）
- Send API 并发节点
- 节点失败降级（return_exceptions=True）
- 1 分钟循环调度（apscheduler 或 asyncio.create_task）

---

### 任务 3：多周期异常检测 + Webhook 推送

**目的**：检测多周期异常（4h 大周期反转 + 1m 短周期插针）+ 1 分钟内 Webhook 推送。

- 文件：`src/vibe_trading_cn/anomaly_detector.py`（60 行）
- 文件：`src/vibe_trading_cn/webhook.py`（30 行）
- 行为：
  - `anomaly_detector.py`：
    - `detect_large_tf_reversal(change_4h, change_1d)`: 4h 和 1d 方向相反 → LARGE_TF_REVERSAL
    - `detect_spike(change_1m, threshold=3.0)`: 1m 涨跌幅 > 3% → SPIKE
    - `detect_sudden_volume(volume, avg_volume, threshold=3.0)`: 成交量 > 3 倍均值 → SUDDEN_VOLUME
    - `detect_position_risk(position, current_pnl)`: 持仓亏 1% → STOP_LOSS_TRIGGER
  - `webhook.py`：
    - `send_wechat/d钉talk/email`
    - `send(channel, title, content)`: 统一接口
    - 重试 3 次 + 多通道冗余

- 测试（6 个）：
  - `test_detect_large_tf_reversal`: 4h +5% / 1d -3% → LARGE_TF_REVERSAL
  - `test_detect_spike_above_threshold`
  - `test_detect_position_risk`
  - `test_send_wechat_with_retry`
  - `test_send_multi_channel_redundancy`
  - `test_webhook_within_1_minute`

- 跑：`pytest tests/test_anomaly_detector.py -v`
- 期望：6 passed / 0 failed

**审查重点**：
- Webhook 失败不能阻塞主流程
- 多通道冗余（微信失败 → 钉钉 → 邮件）
- 异常检测阈值可配置

---

### 任务 4：2 年 1h 主周期回测基线 + 模拟盘启动

**目的**：2 年 BTC + ETH + SOL 1h K 线 walk-forward 回测 + 3 个月模拟盘启动。

- 文件：`tests/test_backtest_validation.py`（v3.4 必做）
- 文件：`docs/runbooks/paper-trading-runbook.md`
- 行为：
  - 拉 2 年 1h K 线（基座 OK）
  - walk-forward：训练 30 天 / 测试 7 天 / 步进 7 天
  - out-of-sample：最后 20% 数据
  - 验收（v3.4 更严）：
    - 胜率 ≥ 60% / Sharpe ≥ 1.0 / 最大回撤 < 20%
    - **STRONG 共振胜率 ≥ 70%**（v3.4 新加）
  - 模拟盘：实际跑 3 个月，统计胜率
  - **回测不达标 → 调策略 + 重跑，不进入模拟盘**

- 测试（6 个）：
  - `test_backtest_2y_walk_forward_1h`: 2 年 1h walk-forward 跑通
  - `test_backtest_winrate_baseline`: 胜率 ≥ 60%
  - `test_backtest_sharpe_baseline`: Sharpe ≥ 1.0
  - `test_backtest_max_drawdown_baseline`: 最大回撤 < 20%
  - **`test_backtest_mtf_strong_winrate`**: STRONG 共振胜率 ≥ 70% —— **v3.4 关键**
  - `test_backtest_out_of_sample`
  - `test_paper_trading_starts`

- 跑：`pytest tests/test_backtest_validation.py -v`
- 期望：7 passed / 0 failed

**审查重点**：
- 回测不能过拟合（in-sample vs out-of-sample 表现差异 < 10%）
- 1h 主周期回测比 1m 噪音小（**v3.4 优势**）
- STRONG vs WEAK 胜率分层（v3.4 关键）
- 模拟盘必须人工确认（不能直接实盘）

---

## 自检清单

- [x] 规格覆盖：v3.4 设计 12 章节全覆盖
- [x] 步骤扫描：无"待定""适当""相关"含糊词
- [x] 类型一致性：`VibeState` TypedDict 在任务 1/2 签名一致
- [x] 审查重点：边界值 / 错误恢复 / 并发安全 / 资源清理 / 1 分钟响应
- [x] 任务独立：任务 1 不依赖 LangGraph；任务 2 依赖任务 1；任务 3 依赖任务 2；任务 4 依赖任务 2
- [x] 时间预算：每任务 ≤ 2 小时

## 执行方式

**SDD（Subagent-Driven Development）**——每任务派全新 subagent。

**账本**：`vibe-trading-cn/.cursor/sdd/progress.md`

**审查者**：每任务完成后派 `code-reviewer` 子代理审查（CRITICAL / IMPORTANT / MINOR 三档）

**修复循环**：每任务最多 5 轮修复，超出回到设计

**最终审查**：所有任务完成后派 `code-reviewer` 做整分支审查

---

## v3.3 vs v3.4 对比

| 任务 | v3.3 | v3.4 | 差异 |
|---|---|---|---|
| **1. MCP 客户端** | 1m + 5m 双周期 | **6 周期（1m/5m/15m/1h/4h/1d）** | +1 周期拉取 + 多周期 fixture |
| **2. 编排** | 11 节点 | **13 节点（+mtf_alignment）** | +mtf_alignment 决策门 + 多周期 fusion |
| **3. 异常** | 1m 插针 | **4h 反转 + 1m 插针** | +大周期异常 |
| **4. 回测** | 1m 主周期（过拟合）| **1h 主周期** | 主周期变更 + STRONG 共振胜率 |
| **总计** | ~7h | **~8h** | +1h |

**自建代码对比**：
- v3.3：~460 行
- **v3.4：~590 行**（+130 行换多周期）

**功能对比（v3.4 完全达到你期望）**：

| 功能 | v3.3 | v3.4 |
|---|---|---|
| **数据周期** | ❌ 1m + 5m | ✅ **6 周期（1m/5m/15m/1h/4h/1d）** |
| **Preset 分工** | ❌ 都看 1m | ✅ **按周期分工**（macro 大 / quant 中 / crypto 小）|
| **融合规则** | ❌ 加权投票 | ✅ **多周期对齐共振**（DIVERGENCE 不推单）|
| **主决策周期** | ❌ 1m（噪音大）| ✅ **1h**（噪音小 + 信号足）|
| **主回测周期** | ❌ 1m（过拟合）| ✅ **1h** |
| **异常检测** | 1m 插针 | **4h 反转 + 1m 插针 + 持仓风险** |
| **胜率目标** | 90% | 90%（**STRONG 共振 ≥ 70%**）|
| **响应速度** | 1 分钟 | 1 分钟 |
| **数据源** | 25（1m K 线）| 25（**1m + 1h + 1d 优先**）|

---

## 一句话

> **「v3.4 自建 590 行 Python 串 13 个 LangGraph 节点，6 周期（1m/5m/15m/1h/4h/1d）综合跟踪 + 5 preset 按周期分工 + mtf_alignment 共振验证 + fusion 多周期加权 + 1h 主决策 + 1h 主周期回测，异常 1 分钟内 Webhook 推送，2 年回测 + 3 个月模拟盘验证 90% 胜率。」**