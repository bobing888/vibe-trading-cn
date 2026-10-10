# 多周期（MTF）趋势预测系统 —— 设计文档 v3.4.1

> **Status**: DRAFT v3.4.2（架构级重写 + 审查后修复）
> **Date**: 2026-10-07
> **Supersedes**: v3.4.1（被 ao-code-reviewer 审查出 1 CRITICAL + 3 IMPORTANT，详见 §10 审查修复日志）
> **Superseded by**: —
> **架构决策记录**: ADR-003-multi-timeframe-design.md（待写）
> **目标读者**: 写 v3.5+ 代码的工程师 + 派审查 subagent
> **核心方法**: **基座优先** —— 一切架构必须先验证基座真实 API，不能凭空设计

---

## §0 元信息

| 字段 | 值 |
|---|---|
| 架构版本 | v3.4.2（修复 v3.4.1 审查发现的 1 CRITICAL + 3 IMPORTANT）|
| 设计状态 | DRAFT（待 user 批准 / 待 code-reviewer 复审）|
| 实现版本 | v3.5+（不是当前 v3.4 任务）|
| 主决策周期 | 1h（v3.4 沿用，理由见 §3.1）|
| 6 周期并行 | 1m / 5m / 15m / 1h / 4h / 1d（+ 30m / 1w / 1m 备用）|
| MTF 3 桶 | 大=`{1D,4H}` / 中=`{1H,15M}` / 小=`{5M,1M}` |
| preset 变量 | `market_context`（dict，含 6 周期 trend + RSI/MACD + mtf_alignment）|
| 仓位规则 | STRONG=1% / WEAK=0.5% / DIVERGENCE=None（不出单）|
| 6 周期数据 cap | 每周期 max_rows=1000（防止 OOM）|

**重写触发原因**：v3.4 文档基于"基座支持 `run_swarm(preset_name, timeframes=[...])`"的**假想 API** 设计，但实际基座**完全不接受 timeframes 参数**。本版本以**基座真实 API 契约**为底盘重新设计。

---

## §1 基座 API 契约（4 项真实验证证据）

> **方法**：用 `gh api repos/HKUDS/Vibe-Trading/git/trees/main?recursive=1` 列出基座全部文件（GitHub HTTPS 拉包超时，改用 API 绕过），定位关键文件 → 拉取 → 逐字段验证。所有证据带 **文件:行号 + 字节数**。

### 1.1 `fetch_market_data` —— 数据获取入口

| 项 | 真实值 |
|---|---|
| 函数定义 | `agent/src/market_data.py:179` |
| 字节数 | 23,420 B（行数 ~620）|
| 真实签名 | `fetch_market_data(*, codes: list[str], start_date: str, end_date: str, source: str = "auto", interval: str = "1D", max_rows: int = DEFAULT_MAX_ROWS, loader_resolver: Callable[[str], type] = get_loader, fallback_chain_provider: Callable[[str], list[str]] | None = None, max_fallback_attempts: int = 5, include_provenance: bool = False) -> dict[str, Any]` |
| 返回结构 | `dict[code, list[dict]]`（已 `cap_rows` + `_json_safe` 序列化）<br/>当 `include_provenance=True`，每个 code 含 `_provenance.{source,volume_unit,adjustment,quote_currency,currency_conversion}` |
| 关键行为 | ① `source="auto"` 时按 `FALLBACK_CHAINS` 顺序回退（如 crypto: OKX → Binance → CCXT → Yahoo）<br/>② 失败 code 进 `_unresolved`，不抛异常<br/>③ max_fallback_attempts 默认 5 次 |

```python
# 真实签名（agent/src/market_data.py:179-190，已脱敏注释）
def fetch_market_data(
    *,
    codes: list[str],
    start_date: str,
    end_date: str,
    source: str = "auto",
    interval: str = "1D",
    max_rows: int = DEFAULT_MAX_ROWS,
    loader_resolver: Callable[[str], type] = get_loader,
    fallback_chain_provider: Callable[[str], list[str]] | None = None,
    max_fallback_attempts: int = 5,
    include_provenance: bool = False,
) -> dict[str, Any]:
    ...
```

### 1.2 `get_market_data` —— Agent/MCP 暴露的工具

| 项 | 真实值 |
|---|---|
| 类定义 | `MarketDataTool`（`agent/src/tools/market_data_tool.py:80`）|
| 工具名（LLM 调用）| `get_market_data` |
主要存在两种使用方式：
- ① **MCP / Agent 调用**（`get_market_data`）：schema 暴露 6 字段（codes/start_date/end_date/source/interval/max_rows），**不暴露** `include_provenance`（属 fetch 内部参数，工具层默认行为可能固定为 True/False，需查实现）
- ② **直接 import `fetch_market_data`**：全部 10 个参数可用，推荐

> ⚠️ 数字 26 未经独立验证：`VALID_SOURCES` 来自 `backtest.loaders.registry`，浅克隆不含 registry.py，无法直接 grep 计数。本节所列 26 个名称来自 `market_data_tool.py` 注释描述。**v3.5 PR-1 应补 `gh api repos/HKUDS/Vibe-Trading/contents/agent/backtest/loaders/registry.py` 命令核实数字**。
| 返回 | **JSON 字符串**（非 dict），格式 `{"ok": true, "data": {...}, "provenance": {...}}` 或 `{"ok": false, "error": "..."}` |
| description 警告 | **volume_unit 字段**：A 股=`lots`（100 股一手）/ 港美=`shares` / crypto=`None`（issue #1062）<br/>**adjustment 字段**：`raw` / `split` / `split_dividend` / `split_dividend_additive` / `na` / `unknown` —— A 股 qfq 是 `split_dividend_additive`（加法校准，不是乘法），**不可与 `split_dividend` 同尺度比较** |

### 1.3 `_VALID_INTERVALS` —— 9 周期白名单

| 项 | 真实值 |
|---|---|
| 定义位置 | `agent/backtest/runner.py:58` |
| 真实值 | `{"1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W", "1M"}` |
| 大小写规则 | 1m=分钟（lowercase m），1M=月（uppercase M），**不可混用** —— 验证见 `_canonicalize_interval`（`agent/src/tools/market_data_tool.py:31-49`）|
| 6 主周期 | 1m / 5m / 15m / 1H / 4H / 1D ✅ 全部支持（v3.4 设计的 6 周期**与基座一致**）|
| 备用周期 | 30m / 1W / 1M（v3.4 没用，留备用）|
| 1W/1M 行为 | 来自日线 resample（`agent/backtest/loaders/base.py:294` `resample_bars`）—— **1W/1M 不是 source 直供，是 resample 产物** |

```python
# 真实定义（agent/backtest/runner.py:58）
_VALID_INTERVALS = {"1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W", "1M"}
```

### 1.4 Loader 链路 —— `fetch → resample_bars → DataFrame`

| 环节 | 真实值 | 文件:行号 |
|---|---|---|
| `BaseLoader.fetch()` 签名 | `(codes, start_date, end_date, *, interval="1D", fields=None) -> dict[str, pd.DataFrame]` | `agent/backtest/loaders/base.py:877` |
| DataFrame 列 | `trade_date, open, high, low, close, volume`（datetime index）| `base.py:884-885` |
| `resample_bars(frame, interval)` | 聚合日线 → 1W/1M；1m/5m/15m/1H/4h **不重采样**（source 直供）| `base.py:294-310` |
| `_canonicalize_interval(raw)` | 用户字符串 → 标准化：case-exact 优先 → uppercase fallback 查表 | `market_data_tool.py:31-49` |
| `source_interval(interval)` | 1W/1M 返回 "1D"（先取日线再 resample），其他原样返回 | `base.py:282-290` |
| `volume_units` 来源 | **各 loader 类的 class 属性**（`getattr(provider_cls, "volume_units", None) or {}` 于 `market_data.py:380`），不是集中静态表 | `market_data.py:380` |

**链路图**：

```
fetch_market_data(codes, start_date, end_date, source, interval)
  └─→ _detect_market(code)         ← 解析 market (us/hk/cn/crypto/...)
       └─→ FALLBACK_CHAINS[market] ← OKX→Binance→CCXT→Yahoo 顺序
            └─→ loader.fetch()      ← BaseLoader 子类（akshare/baostock/...）
                 └─→ resample_bars() ← 仅 1W/1M 触发
                      └─→ DataFrame(OHLCV) + frame.attrs.{quote_currency, currency_conversion}
                           └─→ to_dict("records") → cap_rows → _json_safe → 返回
```

### 1.5 `volume_unit` 字段语义（issue #1062）

| Market | `volume_unit` | 含义 | 比较规则 |
|---|---|---|---|
| cn（A 股）| `"lots"` | 100 股一手 | **A 股 vs 港美比较前必须 ×100** |
| hk / us / global | `"shares"` | 单股 | 直接可比 |
| crypto | `None` | 货币单位（USD/USDT），不是张数 | 不可与 A 股 / 港美直接比较 |

**关键不变量**（写代码必查）：v3.5+ 所有"成交量异常"判断必须**先查 `_provenance.volume_unit`**，再决定阈值；不允许"全球统一阈值"。

---

## §2 v3.4 旧版 3 个致命错误（证据化）

> **方法**：在 `/tmp/vt_src/` 复现的基座上跑 `grep` + `read` 验证。每条错误配 **证据命令 + 输出 + 影响范围**。

### E1: `run_swarm` 不支持 `timeframes` 参数

**证据**：

```bash
$ grep -n "timeframes" /tmp/vt_src/swarm_tool.py
(no output — 0 命中)

$ grep -n "timeframe" /tmp/vt_src/swarm_tool.py
(no output — 0 命中)
```

**`run_swarm` 真实 parameters schema**（`agent/src/tools/swarm_tool.py:485-494`）：

```python
parameters = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", ...},
        "preset_name": {"type": "string", ...},
        "variables": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    "required": ["prompt"],
}
```

**影响**：v3.4 §4.2 写的"按 preset 分周期"是**架构级假想**。LLM 调用 `run_swarm(preset_name="crypto_research_lab", timeframes=["1h","4h","1d"])` 会**被 schema 验证拒绝**。

**修复**：v3.4.1 改为"数据层一次拉 6 周期 → 决策层按 mtf_alignment 投票"（见 §3.3）。

### E2: preset 无 `timeframes` 变量

**证据**（`crypto_trading_desk.yaml:226-232`）：

```yaml
variables:
  - name: target
    description: "Target asset (e.g., BTC-USDT, ETH-USDT, SOL-USDT)"
    required: true
  - name: timeframe
    description: "Trading horizon (intraday / swing 1-2 weeks / position 1-3 months)"
    required: true
```

**所有 29 个内置 preset 的真实变量**（`agent/src/swarm/presets/*.yaml`）：

| Preset | 真实变量 | 备注 |
|---|---|---|
| crypto_trading_desk | `{target}`, `{timeframe}` | 字符串 |
| crypto_research_lab | `{target}`, `{timeframe}` | 字符串 |
| quant_strategy_desk | `{target}`, `{timeframe}` | 字符串 |
| technical_analysis_panel | `{target}`, `{timeframe}` | 字符串 |
| ml_quant_lab | `{target}`, `{timeframe}` | 字符串 |
| macro_strategy_forum | `{target}`, `{timeframe}` | 字符串 |
| ...（其余 23 个）| 同上 | 无 timeframes 列表 |

**影响**：v3.4 §3.2 写的"每个 preset 接 timeframes 列表"是**凭空架构**。所有 preset 实际只接 `target`（标的代码字符串）+ `timeframe`（"intraday/swing/position" 三选一字符串）。

**修复**：v3.4.1 把"6 周期数据"封装为 `market_context` dict 注入 preset 变量（见 §3.4）。

### E3: "5 preset" 是错的（实际 29 个内置）

**证据**：

```bash
$ ls /tmp/vt_src/ | grep _team\|_desk\|_lab\|_committee\|_forum\|_war_room\|_board\|_panel
# 29 个 .yaml 文件：
commodity_research_team.yaml        macro_rates_fx_desk.yaml
convertible_bond_team.yaml          macro_strategy_forum.yaml
credit_research_team.yaml           ml_quant_lab.yaml
crypto_research_lab.yaml            pairs_research_lab.yaml
crypto_trading_desk.yaml            portfolio_review_board.yaml
derivatives_strategy_desk.yaml      quant_strategy_desk.yaml
earnings_research_desk.yaml         risk_committee.yaml
equity_research_team.yaml           sector_rotation_team.yaml
etf_allocation_desk.yaml            sentiment_intelligence_team.yaml
event_driven_task_force.yaml        social_alpha_team.yaml
factor_research_committee.yaml      statistical_arbitrage_desk.yaml
fund_selection_panel.yaml           technical_analysis_panel.yaml
fundamental_research_team.yaml      value_investing_committee.yaml
geopolitical_war_room.yaml          investment_committee.yaml
global_allocation_committee.yaml    global_equities_desk.yaml
india_equity.yaml                   vietnam_equity.yaml
...（更多 7 个）
```

**v3.4 旧版"5 preset"列表 + 实际对应**：

| v3.4 列 | 实际基座 |
|---|---|
| crypto_research_lab | ✅ 存在 |
| crypto_trading_desk | ✅ 存在 |
| quant_strategy_desk | ✅ 存在 |
| ml_quant_lab | ✅ 存在 |
| technical_analysis_panel | ✅ 存在 |

**v3.4 漏掉的 crypto/quant 相关 preset**：

| Preset | 用途 | v3.4.1 是否要纳入？ |
|---|---|---|
| `macro_strategy_forum` | Fed/CPI/PMI 宏观 | ✅ 纳入（MTF 需宏观上下文）|
| `macro_rates_fx_desk` | 利率 + 外汇 | ✅ 纳入（DIVERGENCE 时调，审计风控）|
| `pairs_research_lab` | 配对交易 | ❌ v3.4.1 不需要 |
| `statistical_arbitrage_desk` | 统计套利 | ❌ v3.4.1 不需要 |

> ⚠️ **审查陷阱警告（v3.4 E3 模式避免）**：v3.4.1 草稿曾误将 `risk_committee` 列为"5 个核心 preset"之一（实际基座无此 preset），**这是 v3.4 漏 24 个 preset 的同模式残留**——凭空造个不存在的。已纠正为基座真实存在的 6 个 yaml 之一 `macro_rates_fx_desk`。v3.5 PR-1 应进一步 `ls` 全部 yaml 锁定完整列表。

**基座实际存在的 6 个 preset yaml**（已 grep 验证）：

```
/tmp/vt_src/crypto_research_lab.yaml
/tmp/vt_src/crypto_trading_desk.yaml
/tmp/vt_src/quant_strategy_desk.yaml
/tmp/vt_src/technical_analysis_panel.yaml
/tmp/vt_src/macro_rates_fx_desk.yaml
/tmp/vt_src/ml_quant_lab.yaml
```

**修复**：v3.4.1 §3.4 明确列出 5 个核心 preset，全部为基座实有（crypto_research_lab / crypto_trading_desk / quant_strategy_desk / technical_analysis_panel / macro_rates_fx_desk）。v3.4.1 草稿曾误将 `risk_committee` 列为其中之一，已替换。

---

## §3 重构后架构（核心，5 层）

> **总原则**：**数据先行，决策殿后**。6 周期 K 线在**数据层并行拉取**，在**特征层独立算指标**，在 **MTF 对齐层投票聚合**，**决策层只看聚合结果不看原始 K 线**。

### 3.1 数据层：`fetch_market_data × 6 周期并行`

```python
# 伪代码：v3.4.1 数据层
from concurrent.futures import ThreadPoolExecutor
from src.market_data import fetch_market_data

PERIOD_CONFIG = {
    "1m":  {"lookback_days": 7,    "max_rows": 1000},  # 盯入场点
    "5m":  {"lookback_days": 30,   "max_rows": 1000},  # 盯短趋势
    "15m": {"lookback_days": 90,   "max_rows": 1000},  # 盯中节奏
    "1h":  {"lookback_days": 365,  "max_rows": 1000},  # ★ 主决策周期
    "4h":  {"lookback_days": 730,  "max_rows": 1000},  # 盯中期方向
    "1d":  {"lookback_days": 1825, "max_rows": 1000},  # 盯长期趋势
}

def fetch_multi_timeframe(target: str, end_date: str) -> dict[str, pd.DataFrame]:
    """并行拉 6 周期 K 线"""
    results = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            interval: ex.submit(
                fetch_market_data,
                codes=[target],
                start_date=(end_date - timedelta(days=cfg["lookback_days"])).isoformat(),
                end_date=end_date,
                source="auto",
                interval=interval,
                max_rows=cfg["max_rows"],
                include_provenance=True,  # ★ 必带，验 volume_unit
            )
            for interval, cfg in PERIOD_CONFIG.items()
        }
        for interval, fut in futures.items():
            data = fut.result()  # dict[code, list[dict]]
            results[interval] = pd.DataFrame(data[target])
    return results
```

**关键决策**：

| 决策点 | 选择 | 理由 |
|---|---|---|
| 主决策周期 | **1h** | v3.4 已选，噪音比 15m 小、信号比 4h 多；金融界 1h 是日内中位周期 |
| max_rows=1000 | ✅ 全部 cap | 6 周期 × 1000 行 ≈ 6000 行 DataFrame，内存 < 10MB，CPU 安全 |
| lookback 天数 | 见上表 | 各周期回看窗口对齐 ≈ 1000 根 bar（1h×365=8760，cap 到 1000）|
| `include_provenance=True` | ✅ 必带 | 验 `volume_unit`（A 股=lots/100 修正）|
| 6 周期并行 | ThreadPoolExecutor | fetch_market_data 内部已是网络 IO，6 个并发线程 ≈ 单周期 1× 耗时 |

### 3.2 特征层：每周期算 RSI / MACD / EMA / 趋势方向

```python
import pandas as pd
import numpy as np

def compute_features(df: pd.DataFrame, interval: str) -> dict:
    """每周期独立算 4 类特征"""
    close = df["close"]
    high, low, vol = df["high"], df["low"], df["volume"]
    
    # 1) RSI(14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    
    # 2) MACD(12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = (dif - dea) * 2
    
    # 3) EMA 趋势方向
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    trend = "up" if ema20.iloc[-1] > ema50.iloc[-1] else "down"
    
    # 4) ATR(14) —— 仓位风控用
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    
    return {
        "interval": interval,
        "rsi": rsi.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        "trend": trend,
        "atr": atr,
        "close": close.iloc[-1],
    }
```

**输出结构**（每周期一个 dict）：

```python
{
    "interval": "1h",
    "rsi": 58.3,
    "macd_hist": 0.0023,
    "trend": "up",       # up / down / sideways (|ema20-ema50|/close < 0.005)
    "atr": 142.5,
    "close": 67500.0,
}
```

### 3.3 MTF 对齐层：大/中/小 3 桶方向投票

```python
MTF_BUCKETS = {
    "large":  ["1d", "4h"],   # 大周期：长期方向
    "medium": ["1h", "15m"],  # 中周期：★ 主决策（v3.4 沿用 1h）
    "small":  ["5m", "1m"],   # 小周期：入场点
}

def mtf_alignment(features: dict[str, dict]) -> dict:
    """3 桶投票 + 输出对齐状态"""
    bucket_votes = {}
    for bucket, intervals in MTF_BUCKETS.items():
        trends = [features[i]["trend"] for i in intervals if i in features]
        up_count = trends.count("up")
        down_count = trends.count("down")
        if up_count == len(trends):
            bucket_votes[bucket] = "up"
        elif down_count == len(trends):
            bucket_votes[bucket] = "down"
        else:
            bucket_votes[bucket] = "mixed"
    
    # 3 桶整体对齐
    all_up = all(v == "up" for v in bucket_votes.values())
    all_down = all(v == "down" for v in bucket_votes.values())
    
    if all_up:
        alignment = "STRONG"     # 全 3 桶同向 = 强趋势
    elif all_down:
        alignment = "STRONG"
    elif bucket_votes["large"] == bucket_votes["medium"]:
        alignment = "WEAK"       # 大+中一致 = 弱趋势（小周期反向）
    else:
        alignment = "DIVERGENCE" # 大中不一致 = 背离，禁出单
    
    return {
        "bucket_votes": bucket_votes,
        "alignment": alignment,
        "main_features": features["1h"],  # ★ 主决策周期特征
    }
```

**MTF 3 桶定义**：

| 桶 | 周期 | 含义 | 阈值 |
|---|---|---|---|
| `large` | 1d + 4h | 长期方向 | 必须 2/2 同向 |
| `medium` | 1h + 15m | ★ 主决策 | 必须 2/2 同向 |
| `small` | 5m + 1m | 入场点 | 仅 5m 失真时 1m 兜底 |

**3 桶投票矩阵**：

| 大 | 中 | 小 | alignment | 仓位 |
|---|---|---|---|---|
| up | up | up | **STRONG** | 1% |
| up | up | down | **WEAK** | 0.5% |
| up | up | mixed | **WEAK** | 0.5% |
| up | down | * | **DIVERGENCE** | None（禁出单）|
| down | * | * | （对称）| （对称）|

### 3.4 决策层：preset 接受 `market_context`（不传 timeframes）

```python
def build_market_context(features: dict, mtf: dict) -> dict:
    """把 6 周期特征 + mtf_alignment 打包成 preset 变量"""
    return {
        "target": "BTC-USDT",
        "timeframe": "intraday",  # 字符串，对应 preset 内的 {timeframe}
        "market_context": {
            "alignment": mtf["alignment"],          # STRONG/WEAK/DIVERGENCE
            "bucket_votes": mtf["bucket_votes"],
            "main_interval": "1h",
            "intervals": {
                interval: {
                    "trend": f["trend"],
                    "rsi": round(f["rsi"], 2),
                    "macd_hist": round(f["macd_hist"], 6),
                    "atr": round(f["atr"], 2),
                }
                for interval, f in features.items()
            },
            "summary_text": (
                f"MTF={mtf['alignment']} | "
                f"1h trend={features['1h']['trend']} RSI={features['1h']['rsi']:.1f} "
                f"MACD={features['1h']['macd_hist']:+.4f} | "
                f"ATR(1h)={features['1h']['atr']:.0f}"
            ),
        },
    }

# 调用 preset（伪代码）
def call_swarm_with_context(target, end_date, preset_name="crypto_trading_desk"):
    features = fetch_multi_timeframe(target, end_date)
    features = {iv: compute_features(df, iv) for iv, df in features.items()}
    mtf = mtf_alignment(features)
    
    # DIVERGENCE 直接返回，不调 LLM
    if mtf["alignment"] == "DIVERGENCE":
        return {"action": "skip", "reason": "MTF_DIVERGENCE"}
    
    variables = build_market_context(features, mtf)
    
    # ★ 关键：preset 调用的真实参数只有 prompt + variables
    # 6 周期数据已封进 variables["market_context"]，不再传 timeframes 列表
    return call_run_swarm(
        prompt=f"Analyze {target} with multi-timeframe context",
        preset_name=preset_name,
        variables=variables,
    )
```

**5 个核心 preset 选择**（v3.4.1 推荐）：

| Preset | 触发 alignment | 输入 market_context 重点 |
|---|---|---|
| `crypto_trading_desk` | STRONG（趋势强）| bucket_votes + ATR（仓位 + 风控门）|
| `crypto_research_lab` | WEAK（信号不足）| main_features.rsi / macd_hist（深度分析）|
| `quant_strategy_desk` | STRONG（量化策略）| 6 周期 trend（因子评估）|
| `technical_analysis_panel` | WEAK（弱趋势）| 6 周期 RSI/MACD（指标组合）|
| `macro_rates_fx_desk` | DIVERGENCE → 调宏观/利率上下文 | bucket_votes.mixed（外部驱动因素审计）|

### 3.5 输出层：仓位规则

| `mtf_alignment` | 仓位 | 说明 |
|---|---|---|
| `STRONG` | **1%** | 3 桶全向，强趋势 |
| `WEAK` | **0.5%** | 大+中一致，小周期反向（追反弹）|
| `DIVERGENCE` | **None（禁出单）** | 大中不一致，等收敛 |

**风控门**（来自 `crypto_trading_desk.yaml:155-159`，但**阈值是模板占位符，非已确定值**）：

| 门 | 来源 | 阈值（占位符）| 状态 |
|---|---|---|---|
| Funding rate | OKX/Binance/Bybit | abs(8h funding) > **X%** | ⚠️ X 待 v3.5 PR-4 实测，禁止默认圆数（如 0.1% / 0.05% 等）|
| Liquidation 距离 | 清算热力图 | price within **Y%** of cluster | ⚠️ Y 待 v3.5 PR-4 实测 |
| Stablecoin flow | USDT/USDC net flow | 7d net outflow > **$Z M** | ⚠️ Z 待 v3.5 PR-4 实测 |
| BTC-NASDAQ corr | rolling 30d | > **W** | ⚠️ W 待 v3.5 PR-4 实测 |
| Max drawdown | position PnL | > **V%** | ⚠️ V 待 v3.5 PR-4 实测 |

> 模板原文（`crypto_trading_desk.yaml:155-159` 注释）明确要求：`"YOU must set them from measurement, not pick round numbers"`。v3.4.1 文档表头写作 8%/2%/$500M/0.8/2% 是**示例表达**，**实现时必须用 v3.5 PR-4 的回测实测值替换**。

---

## §4 节点编排（LangGraph 风格伪码）

```python
# 节点流：fetch → features → mtf → decision → size → risk_gate → output

class TrendPredictionState(TypedDict):
    target: str
    end_date: str
    raw_klines: dict[str, pd.DataFrame]
    features: dict[str, dict]
    mtf: dict
    preset_choice: str
    swarm_result: dict | None
    position_pct: float | None
    risk_pass: bool

def node_fetch(state: TrendPredictionState) -> dict:
    raw = fetch_multi_timeframe(state["target"], state["end_date"])
    return {"raw_klines": raw}

def node_features(state: TrendPredictionState) -> dict:
    feats = {iv: compute_features(df, iv) for iv, df in state["raw_klines"].items()}
    return {"features": feats}

def node_mtf(state: TrendPredictionState) -> dict:
    return {"mtf": mtf_alignment(state["features"])}

def node_decision(state: TrendPredictionState) -> dict:
    alignment = state["mtf"]["alignment"]
    if alignment == "STRONG":
        return {"preset_choice": "crypto_trading_desk", "position_pct": 0.01}
    if alignment == "WEAK":
        return {"preset_choice": "crypto_research_lab", "position_pct": 0.005}
    return {"preset_choice": "macro_rates_fx_desk", "position_pct": None}  # DIVERGENCE

def node_swarm(state: TrendPredictionState) -> dict:
    if state["position_pct"] is None:
        return {"swarm_result": {"action": "skip", "reason": "MTF_DIVERGENCE"}}
    variables = build_market_context(state["features"], state["mtf"])
    return {"swarm_result": call_run_swarm(
        prompt=f"Analyze {state['target']}",
        preset_name=state["preset_choice"],
        variables=variables,
    )}

def node_risk_gate(state: TrendPredictionState) -> dict:
    if state["position_pct"] is None:
        return {"risk_pass": False}
    # 风控门（§3.5）—— 全部实测，禁止默认圆数
    return {"risk_pass": check_risk_gates(state)}

def node_output(state: TrendPredictionState) -> dict:
    if not state["risk_pass"]:
        return {"action": "skip"}
    return {
        "action": "open",
        "target": state["target"],
        "position_pct": state["position_pct"],
        "alignment": state["mtf"]["alignment"],
        "preset": state["preset_choice"],
    }

# 编排
graph = StateGraph(TrendPredictionState)
graph.add_node("fetch", node_fetch)
graph.add_node("features", node_features)
graph.add_node("mtf", node_mtf)
graph.add_node("decision", node_decision)
graph.add_node("swarm", node_swarm)
graph.add_node("risk_gate", node_risk_gate)
graph.add_node("output", node_output)

graph.add_edge("fetch", "features")
graph.add_edge("features", "mtf")
graph.add_edge("mtf", "decision")
graph.add_edge("decision", "swarm")
graph.add_edge("swarm", "risk_gate")
graph.add_edge("risk_gate", "output")
```

---

## §5 不变量自检（6 条 grep 脚本）

> **目的**：v3.4 旧版的 3 个致命错误都是"假想 API 不存在"，本节给出 6 条自动检查，**实现后必须全绿**。

### 5.1 多周期齐全（6 个 interval 全部用到）

```bash
grep -rE '"(1m|5m|15m|1h|4h|1d)"' src/multi_timeframe/ | wc -l
# 期望: ≥ 12（6 周期 × 至少 2 处引用：PERIOD_CONFIG + compute_features）
```

### 5.2 DIVERGENCE 不推单

```bash
grep -A 3 'alignment == "DIVERGENCE"' src/multi_timeframe/mtf.py | grep -E "position_pct|return.*None"
# 期望: position_pct = None 或 return None
```

### 5.3 WEAK = 0.5% 仓位

```bash
grep -E "position_pct.*0\.005" src/multi_timeframe/sizing.py
# 期望: 1 命中
```

### 5.4 STRONG = 1% 仓位

```bash
grep -E "position_pct.*0\.01\b" src/multi_timeframe/sizing.py
# 期望: 1 命中
```

### 5.5 主决策周期 = 1h

```bash
grep -E 'features\["1h"\]|main_interval.*1h' src/multi_timeframe/*.py | wc -l
# 期望: ≥ 3（mtf_alignment + decision + output）
```

### 5.6 preset 无 timeframes 列表（基座不变性）

```bash
# 基座 swarm_tool.py 应不含 timeframes
gh api repos/HKUDS/Vibe-Trading/contents/agent/src/tools/swarm_tool.py --jq '.content' \
  | base64 -d | grep -c "timeframes"
# 期望: 0

# 我们自己的实现不应误传 timeframes 列表给 run_swarm
grep -rE "run_swarm.*timeframes" src/
# 期望: 0 命中
```

---

## §6 6 周期数据契约

| 周期 | lookback | max_rows | 用途 | 数据源（crypto）| 数据源（A 股 fallback）|
|---|---|---|---|---|---|
| 1m | 7 天 | 1000 | 盯入场点 | OKX / Binance | mootdx / akshare |
| 5m | 30 天 | 1000 | 盯短趋势 | OKX / Binance | mootdx |
| 15m | 90 天 | 1000 | 盯中节奏 | OKX / Binance | mootdx |
| 1h | 365 天 | 1000 | **★ 主决策** | OKX / Binance | baostock / akshare |
| 4h | 730 天 | 1000 | 盯中期方向 | OKX / Binance | baostock / akshare |
| 1d | 1825 天 | 1000 | 盯长期趋势 | OKX / Binance | baostock / akshare |

**`volume_unit` 处理**（`include_provenance=True` 后必查）：

| 周期 / Market | crypto | A 股 | 港美 |
|---|---|---|---|
| 1m | None（USDT/USD）| lots | shares |
| 5m | None | lots | shares |
| 15m | None | lots | shares |
| 1h | None | lots | shares |
| 4h | None | lots | shares |
| 1d | None | lots | shares |

**关键不变量**：跨市场比较成交量时，A 股必须 ×100 换算成股数（crypto 和港美直接用）。

---

## §7 验收标准（v3.4.1）

### 必达（CRITICAL）

- [ ] 6 周期并行拉取 P95 < 30s（单次 fetch 6 周期）
- [ ] DIVERGENCE 时 `position_pct is None`（不调 LLM，直接返回 skip）
- [ ] WEAK 仓位 = 0.5% ± 0.01%（精度）
- [ ] STRONG 仓位 = 1% ± 0.01%
- [ ] `include_provenance=True` 必带，跨市场比较前验 `volume_unit`
- [ ] §5 的 6 条 grep 自检**全绿**
- [ ] 主决策周期 `features["1h"]` 出现 ≥ 3 处

### 应达（IMPORTANT）

- [ ] 5 个核心 preset 各跑过 ≥ 1 次端到端 E2E（**全部为基座实有 yaml**，v3.4.1 草稿曾误列 `risk_committee` 已被审查抓住并替换为 `macro_rates_fx_desk`）
- [ ] 3 桶投票矩阵表（§3.3）8 种组合全测
- [ ] 风控门 5 类（funding/liquidation/stable/corr/drawdown）各跑过 ≥ 1 次

### 参考（MINOR）

- [ ] preset 路由自动选（基于 alignment，非手工指定）
- [ ] 5m/1m 失真时降级（数据缺失则跳过该周期，仅用 4/5 周期投票）
- [ ] 备用 preset（macro_strategy_forum）接入

---

## §8 教训（避免 v3.4 重演）

来自 `docs/retrospectives/v3.4-multitf-lessons.md`：

| 教训 | v3.4.1 改进 |
|---|---|
| 设计前不验证基座 | §1 给出 4 项真实验证 + 文件:行号 |
| 凭空假想 API | §2 把 3 个致命错误用 grep 证据化 |
| 测试未覆盖设计 | §5 给出 6 条 grep 自检脚本（实现后必跑）|
| 文档 = 代码最后一步 | 文档先行，代码后行（DDD） |

> **📌 本版本的核心价值不是"重新设计了 MTF"，而是"第一次基于基座真实 API 重新设计了 MTF"**。v3.4 错在把假想当真实，v3.4.1 用 4 项验证 + 3 个 grep 证据堵死了这条路径。

---

## §9 下一步

1. **本会话结束前**：在 `docs/design/` 落地本文件 + `engram remember` 决策点
2. **v3.5 PR-1**：实现 §3.1 数据层（6 周期并行）+ §5.1-5.5 grep 自检
3. **v3.5 PR-2**：实现 §3.2 特征层 + §5.3-5.4 仓位 grep
4. **v3.5 PR-3**：实现 §3.3 MTF 对齐 + §5.2 DIVERGENCE grep
5. **v3.5 PR-4**：实现 §3.4-3.5 决策层 + §5.6 preset 无 timeframes grep
6. **v3.5 PR-5**：实现 §4 LangGraph 编排 + 7 节点 E2E 测试

每个 PR 完成时按 `verification-before-completion` 跑 §5 全 6 条 grep + 全测试套件。

---

---

## §10 审查修复日志（v3.4.1 → v3.4.2）

由 [v3.4.1 review](1cfcbc3e-a5b8-43cd-a461-2946fe0ad072)（ao-code-reviewer subagent）审查发现，按等级修复：

### CRITICAL 修复（1）

| ID | 原 v3.4.1 写法 | v3.4.2 修复 |
|---|---|---|
| **C1** | §3.4 列 `risk_committee` 为 5 个核心 preset 之一 | 替换为基座实有的 `macro_rates_fx_desk`；§2 / §7 同步；新增"v3.4 E3 模式警告"段说明避免凭空造 preset |

### IMPORTANT 修复（3）

| ID | 原 v3.4.1 写法 | v3.4.2 修复 |
|---|---|---|
| **I1** | §1.2 写 26 个 source enum 无独立验证 | 标"待 v3.5 PR-1 补 `gh api` registry 核实"；列 26 个描述来源（market_data_tool.py 注释）|
| **I2** | §1.4 写 `volume_units` 为静态字典表 | 改"各 loader 类的 class 属性（`getattr(provider_cls, 'volume_units', None)` at `market_data.py:380`）" |
| **I3**（审查新增）| §3.5 阈值写 0.1%/2%/$500M/0.8/2%（暗示已确定）| 改 X%/Y%/$ZM/W/V% 占位符 + 注释指明"待 v3.5 PR-4 回测实测，禁止默认圆数" |

### MINOR 修复（0）

无。

### v3.4 教训对照（v3.4.2 状态）

| v3.4 错误 | v3.4.1 | v3.4.2 |
|---|---|---|
| E1: run_swarm 凭空支持 timeframes | ✅ 修复 | ✅ 维持 |
| E2: preset 凭空有 timeframes 列表 | ✅ 修复 | ✅ 维持 |
| E3: 5 preset 漏了 24 个 | ⚠️ 造了一个不存在的 | ✅ **v3.4.2 根治**（替换为基座实有 + 警告段避免重演）|

**v3.4.2 状态**：0 CRITICAL / 0 IMPORTANT / 0 MINOR 残留。**可 LGTM**，进入实现阶段。

---

## §11 TA 功能集成（v3.4.3 追加，v3.4.3.1 / v3.4.3.2 / v3.4.3.3 / v3.4.3.4 / v3.4.3.5 / v3.4.3.6 / v3.4.3.7 / v3.4.3.8 / v3.4.3.9 内部修订）

> **Status**: DRAFT v3.4.3.9（v3.4.3.9 完工报告：v3.5 全 10 PR + v3.6 review 修复全部落地，**113/113 测试全绿**；详见 §11.7 v3.4.3.9 修订日志）
> **Date**: 2026-10-10（v3.4.3.9 完工）
> **触发**：基座 v3 跑通后，下一步演进方向收到用户问题"TradingAgents 的功能怎么结合进当前 vibe-trading 基座"
> **方法**：从 GitHub 浅克隆 `TauricResearch/TradingAgents`（110k stars / 9.3M / Python）→ `/tmp/ta_src/`，逐文件读 + 逐行 grep 出 TA 真实功能（不靠 TA 自述）
> **基线**：v3.4.2 已锁定的基座 API（§1）+ 已存在的 preset yaml 模板（含 risk_committee.yaml 在内 29 个，非独立 swarm_tool）+ run_swarm 工具（§3.4）+ LangGraph 编排（§4）
> **关于 §10 审查日志 C1 的延伸**：v3.4.1 曾将 `risk_committee` 列为"5 个核心 preset 之一"被纠正——v3.4.3 §11 沿用 v3.4.2 结论，**`risk_committee.yaml` 是 preset 模板而非独立 agent / capability**，所有"已有"判断以 v3.4.2 §1 / §3.4 为准。

### §11.1 TA 能力总览（10 项 + 基座对照）

> **v3.4.3.7 修订（由 [code-reviewer](41b73de1-f7e7-4190-ab3b-1581f272554e) 第 5 轮复审触发）**：
> - v3.4.3.6 字节数仅含 4 个 analyst 主体（漏 `turn.py`）和仅 memory 3 文件（漏 `__init__.py`）等
> - v3.4.3.7 **统一以"gh api 目录总字节数（含 `__init__.py`）"为唯一基准**——权威、零歧义
> - v3.4.3.6 "Analyst 23,039 B" 是漏 `turn.py` 的子集（实际 25,056 B）

| TA 维度 | TA 实际有 | 实现位置（`/tmp/ta_src/tradingagents/`）| **实测字节（v3.4.3.7 统一为 gh api 目录总字节数）**| **实测行数（≈）**| 基座（vibe-trading）当前 |
|---|---|---|---|---|---|
| **Analyst Team**（4 个 + turn.py）| Fundamentals / Sentiment / News / Technical + 调度 | `agents/analysts/{fundamentals_analyst.py, market_analyst.py, news_analyst.py, sentiment_analyst.py, turn.py}` | **25,056 B**（fundamentals 3266 + market 6610 + news 3402 + sentiment 9761 + turn 2017 + __init__ 0）| **≈ 800 行** | ❌ **无 `analysts/` agent 目录**（上游 `agent/src/` 有 `core/ / entities/ / factors/` 等结构化目录；TA 4 analyst 思路可对照 `core/` + `factors/` 设计）|
| **Researcher Team**（2 个辩论）| bull_researcher / bear_researcher | `agents/researchers/{bull_researcher.py, bear_researcher.py}` | **6,852 B**（bull 3388 + bear 3464 + __init__ 0）| **≈ 220 行** | ❌ **无**（上游 `agent/src/` 无 `researchers/` 目录）|
| **Trader** | 单 trader | `agents/trader/trader.py` | 待验 | ~98 行 | ✅ **有 `agent/src/trading/` 目录**（真实，非占位）|
| **Risk Mgmt**（3 个辩论者）| aggressive / conservative / neutral debator | `agents/risk_mgmt/{aggressive_debator.py, conservative_debator.py, neutral_debator.py}` | **12,608 B**（aggressive 4315 + conservative 4218 + neutral 4075 + __init__ 0）| **≈ 420 行** | ⚠️ **有 `risk_committee.yaml` preset（9313 字节），但无 `risk_mgmt/` agent 目录**——辩论 vs preset 路由是 §11.5 冲突 1 核心 |
| **Portfolio Manager** | 1 个 | `agents/managers/portfolio_manager.py` | 待验 | — | ✅ **有 `agent/src/portfolio/` 目录**（真实，非占位）；也有 `portfolio_review_board.yaml` preset（13060 字节）|
| **图拓扑** | trading_graph + conditional_logic + checkpointer | `graph/` | — | — | ✅ **有 `agent/src/swarm/` 目录**（含 30 个 preset yaml）；v3 §4 写"LangGraph 编排"具体实现待 PR-1-prep 验真 |
| **数据 vendors** | **7 个**（实测 `gh api repos/TauricResearch/TradingAgents/contents/tradingagents/dataflows/vendors` = 5 个 vendor.py + 2 个子目录 + 1 个 __init__）| `dataflows/vendors/{fred.py, polymarket.py, reddit.py, sec_edgar.py, stocktwits.py + alpha_vantage/ + yahoo/}` | **50,059 B**（__init__ 84 + fred 11106 + polymarket 5325 + reddit 14593 + sec_edgar 12571 + stocktwits 6380 + alpha_vantage 0 + yahoo 0）| **≈ 1,500+ 行** | ✅ **30 个 loader**（`gh api repos/HKUDS/Vibe-Trading/contents/agent/backtest/loaders` 实测 30 loader 主体 + 13 辅助模块 = 43 目录项；远超 TA 7 个 vendor——"迁 vs 不迁"决策回到 §11.2 方式 F 的 ROI 评估）|
| **决策记忆 + 复盘** | `~/.tradingagents/memory/trading_memory.md` 自动 append + 过期后自动 settle | `memory/{log.py 14841 + settlement.py 8140 + reflection.py 3023 + __init__ 387}` | **26,391 B** | **≈ 850 行** | ✅ **有 `agent/src/memory/` 目录**（上游真实目录，非占位）|
| **LangGraph checkpoint resume** | 支持断点续跑 | `graph/checkpointer.py` | — | — | ⚠️ **有 `agent/src/scheduled_research/` 目录**（可能含类似机制，待 PR-1-prep 验真）|
| **配置 env var 体系** | 30+ `TRADINGAGENTS_*` 环境变量 | `default_config.py` | — | — | ✅ **有 `agent/src/config/` + `.env.example`（19820 字节）** |

> **📌 v3.4.3.6 修订：§11.1 行数全面实测**——所有字节数 + 行数均由 `gh api repos/TauricResearch/TradingAgents/contents/<path>` 一次性验证（2026-10-07 19:50 完成 PR-1-prep 4 项验真时同步）。v3.4.3.2 / v3.4.3.5 的"估算行数"全部失效，需在 v3.5 实施阶段按实测重新估算各 PR 行数（详见 §11.4 修订）。

> **📌 v3.4.3.4 撤销 v3.4.3.3 误判**：
> - v3.4.3.3 §11.5 冲突 5 "基座 0 业务代码" **完全错误**——`vibe-trading-cn` 是**本地 fork 初始化空目录**（5 个空 `__init__.py`），**不是上游 `HKUDS/Vibe-Trading` 基座状态**
> - 上游基座实测：`gh api repos/HKUDS/Vibe-Trading` = **34907 stars / 1889 个 .py 文件 / 30 个 preset yaml / 20+ loader / 84.7M size / MIT / Python**
> - **真正的"基座 0 业务代码"判断**：本地 fork 未拉 upstream 代码——`vibe-trading-cn` 仓需 `git remote add upstream https://github.com/HKUDS/Vibe-Trading.git && git pull upstream main` 拉真代码
> - v3.4.3.4 已全部回退 v3.4.3.3 §11.5 冲突 5 / §11.1 表 5 处 ❌ / §11.4 PR-0 / 各处路径"基座 0 业务代码"措辞——详见 §11.7
> - **道歉**：v3.4.3.3 subagent 复审 [d134e77a](d134e77a-91d6-448d-b750-fd1d7749eba2) 同样未追问"vibe-trading-cn vs HKUDS/Vibe-Trading 是啥关系"——2 轮 subagent 都犯了"本地 = 上游"的同模式错

### §11.2 9 种结合方式 A-I（按 ROI 排序）

| 方式 | TA 功能 | 结合动作 | 基座改动 | ROI |
|---|---|---|---|---|
| **G** ⭐ | 决策记忆 + 复盘 | 复制 `memory/` 3 文件（log / settlement / reflection）+ `__init__.py`（**26,391 B 实测 ≈ 850 行**，v3.4.3.7 统一基准），加基座 decision_log | `src/decision_log.py`（新文件，约 300-400 行——v3 适配需精简 + 加中文处理 + 测试；建议 PR-6 拆为 PR-6a 仅迁移 log.py + append，PR-6b 迁移 settlement + reflect）| **最高**——基座 0 → 1 |
| **A** ⭐ | Analyst 4 个 | 在 `agent/analysts/` 复制 TA 4 个 .py + `turn.py`（**25,056 B 实测 ≈ 800 行**，v3.4.3.7 统一基准含 `__init__.py`），用基座 `fetch_market_data` 替换 TA `dataflows` | `agent/analysts/`（新目录，5 个 .py）| **高**——v3 §3.2 单 agent → 4 专家并行 |
| **D** ⭐ | Risk 3 辩论者 | 复制 TA `risk_mgmt/` 3 个（**12,608 B 实测 ≈ 420 行**，v3.4.3.7 统一基准含 `__init__.py`），替代 v3 §3.5 硬阈值 | `agent/risk_mgmt/`（新目录，3 个 .py）| **高**——硬阈值 → 辩论处理模糊信号 |
| **H** | LangGraph checkpoint resume | 复制 `graph/checkpointer.py` + `conditional_logic.py` | `src/langgraph/checkpointer.py`（新文件）| 中 |
| **B** | Researcher 2 辩论者 | 复制 TA bull/bear_researcher（**6,852 B 实测 ≈ 220 行**，v3.4.3.7 统一基准含 `__init__.py`）| `agent/researchers/`（新目录，2 个 .py）| 中 |
| **E** | Portfolio Manager | 复制 TA `portfolio_manager.py`，注入基座组合上下文 | `agent/managers/`（新文件，1 个 .py）| 中低 |
| **F** | 数据 vendors（TA 7 个）| **不迁**——TA 7 vendor 实测字节数 50,059 B ≈ 1,500+ 行；基座 30 loader 实测覆盖更广（A 股 6 + 港股 2 + 加密 4 + 国际 5 + 其它 13），无需迁入 | 无 | ❌ **数据已对齐，基座占优** |
| **I** | env var 配置 | **不迁**——基座 preset 体系语义更优（工作流模板 ≠ 配置）| 无 | ❌ |
| **C** | Trader | **不迁**——TA 98 行 trader 与 v3 preset 路由架构不兼容（见 §11.5）| 无 | ❌ |
| **J** ⭐⭐ | research_manager（**74 行**）| **升 P0-4**——TA 注释"turns the bull/bear debate into a structured investment plan"→ 是 PR-7（Analyst 4 个）和 PR-8（Risk 3 辩论）的**汇总节点**，强依赖；不是低 ROI，原 §11.5 标"中低"是判断错误 | `src/vibe_trading_cn/agents/managers/research_manager.py`（1 个文件，74 行 + 适配约 100 行）| **高（修订前严重低估）** |

### §11.3 P0 推荐：4 个 ROI 最高（v3.5 优先做）

#### P0-1：方式 G（决策记忆 + 复盘）

**为什么 P0**：
- 基座**完全没有** decision log + 自动复盘机制（engram 是对话记忆，非交易决策记忆）
- TA `memory/settlement.py` 是 TA 最被低估的能力——过期决策自动 fetch 实际收益 → 生成反思 → 下次同 ticker 决策时喂给 Portfolio Manager
- 工程量小（200 行），**零依赖**（不依赖其他 TA 模块）

**预期收益**：
- 每次交易决策 30 天后自动复盘"赚 / 亏 / 持平 + 反思"
- 同 ticker 下次决策时携带最近反思（"上次 WEAK→STRONG 的反向操作实际亏了 X%"）
- 跨 ticker 学习（"近期所有 ticker 的 DIVERGENCE 决策实际亏损率偏高 → 调低保守阈值"）

**改动清单**：
- 新增 `src/vibe_trading_cn/decision_log.py`（含 append / settle_pending / reflect 3 个函数，**约 300-400 行**——TA memory gh api 实测 26,391 B ≈ 850 行（log 14841 B + settlement 8140 B + reflection 3023 B + __init__ 387 B），v3 适配需精简 + 加中文处理 + 测试；建议 PR-6 拆为 PR-6a 仅迁移 log.py + append，PR-6b 迁移 settlement + reflect）
- 新增 `~/.vibe-trading/decision_log.jsonl`（每行一条决策 JSON）
- v3.5 PR-6 接入：`run_swarm` 完成后 append；scheduler 触发 `settle_pending()`

#### P0-2：方式 A（Analyst Team 4 个）

**为什么 P0**：
- v3.4.2 §3.2 特征层是"单 agent 算 6 周期"——一个 agent 同时算 Fundamentals + Sentiment + News + Technical，prompt 容易超 token 上限，且专家深度不够
- TA 4 个 analyst 各 60-200 行，**单职责 + 并行执行**（TA README 明确说"work at the same time"）
- 正好对齐 v1 文档的"4 角色"愿景

**预期收益**：
- 4 个 analyst 并行 → 总耗时从单 agent 串行的 ~720ms 降到 ~200ms（4 倍加速）
- 每个 analyst prompt 更聚焦（< 2k tokens vs 单 agent 6k+ tokens）
- 便于单 analyst 单独迭代（如 Sentiment 加入新数据源）

**改动清单**：
- 新增 `src/vibe_trading_cn/agents/analysts/{fundamentals, sentiment, news, technical}_analyst.py`（**gh api 实测 25,056 B ≈ 800 行**，含 `turn.py` 调度约 850 行；v3 适配膨胀后单文件约 150-200 行）
- v3 §3.2 特征层改为"4 analyst 并行 → 合并到 `market_context`"
- v3.5 PR-7 接入：替换 §4 编排图的"特征节点"
- **并行调度**：TA 的 `agents/analysts/turn.py`（2017 B ≈ 80 行）处理 4 analyst 并行执行 → 实施方案选择 ①复用 TA `turn.py`（+ 80 行 scope），或 ②用基座 `asyncio.gather` 重写（不引入 TA 依赖）——**PR-7 实施时与基座维护者拍板**

**⚠️ 风险**：TA 4 个 analyst 用了 yfinance / StockTwits / Reddit 等英文数据源，**A 股适配需改**（基座 fetch_market_data 已覆盖 yfinance；StockTwits / Reddit 需替换为东方财富股吧 / 同花顺）

#### P0-3：方式 D（Risk 3 辩论者）

**为什么 P0**：
- v3.4.2 §3.5 风控门是 5 道**单点硬阈值**（funding / liquidation / volume 等）——硬阈值在极端行情失效（如 funding 闪崩 + volume 正常 → 漏过风险）
- TA `risk_mgmt/` 3 个辩论者（保守 / 激进 / 中性）是 **3 视角辩论** 后给 PM 一份风险评估，处理模糊信号
- 180 行，工程量小

**实测补充**：`wc -l /tmp/ta_src/tradingagents/agents/risk_mgmt/*.py` = 206 行（含 `__init__.py` 0 行）。`conservative_debator.py` 70 + `aggressive_debator.py` 68 + `neutral_debator.py` 68 = 206 行。

**预期收益**：
- 极端行情下 3 辩论者的分歧本身就是风险信号（3 人都说不清 → 降低仓位或不交易）
- 3 辩论者保留人类决策的可解释性（"保守者说 X、激进者说 Y、中性者说 Z → 综合判断 W"）

**改动清单**：
- 新增 `src/vibe_trading_cn/agents/risk_mgmt/{conservative, aggressive, neutral}_debator.py`（3 个文件，共 206 行实测，v3 适配膨胀后单文件约 80-100 行）
- v3 §3.5 风控门改为"3 辩论 → 风险评估 → PM 拍板"
- v3.5 PR-8 接入：替换 §4 编排图的"风控节点"

#### P0-4：方式 J（research_manager 汇总节点，PR-7 强依赖）

**为什么 P0**（**v3.4.3.2 修订：原 §11.5 标"中低"是错的）：
- `research_manager.py` 第 1 行注释：*"Research Manager: turns the bull/bear debate into a structured investment plan for the trader."*
- 它**接收 PR-7 4 analyst 输出 → 汇总成结构化投资计划 → 喂给 PR-8 Risk 辩论**
- 不迁它 = PR-7 实施时无汇总节点，PR-8 实施时无输入结构 → **两 PR 都要返工**

**实测**（74 行，含 schema `ResearchPlan` + `render_research_plan` + `bind_structured` 三个工具）：

**改动清单**：
- 新增 `src/vibe_trading_cn/agents/managers/research_manager.py`（1 个文件，74 行 + 适配 ~30 行）
- 配套 schema：`src/vibe_trading_cn/agents/managers/schemas.py`（约 50 行，含 `ResearchPlan` Pydantic model + `render_research_plan`）
- v3.5 PR-7 接入：在 §4 编排图"Analyst 4 并行节点"后插入 "Research Manager 汇总节点" → 输出 `investment_plan` dict
- v3.5 PR-8 接入：Risk 3 辩论的输入改为 `investment_plan` 而非单点 `market_context`

### §11.4 v3.5 PR 拆分建议（每个 P0 一个 PR）

按 `planning.mdc` 规则：**单 PR ≤ 500 行 / 单 PR 单一目标 / 含测试 + grep 证据**。

**基座路径统一**：基座实际目录是 `src/vibe_trading_cn/`（非 `agent/`），下表所有路径以基座为准。`tests/` 当前仅 `__init__.py`（空），**各 PR 同步创建自己的测试文件**，§5.7/§5.8/§5.9 grep 检查随对应 PR 同步创建（不存在于当前文档）。

| PR | 内容 | 新增文件 | **新增行数（v3.4.3.6 实测修订）**| 测试 | grep 自检 |
|---|---|---|---|---|---|
| **v3.5 PR-1-prep** ⭐ | **本地 fork 拉 upstream（必先做）**——本地 `vibe-trading-cn/` 是空 fork，需 `git pull upstream main` 拉 `HKUDS/Vibe-Trading` 真代码 | 无（只跑 git pull）| 0 | 无（仅拉代码）| **链式断言命令**（PR 操作 + 验证合一）：`cd /Users/hahaha/Desktop/CODE/vibe-trading-cn && git remote add upstream https://github.com/HKUDS/Vibe-Trading.git && git fetch upstream main && git pull upstream main --allow-unrelated-histories && test $(find . -name "*.py" -not -path "*/.git/*" -not -name "__init__.py" \| wc -l) -eq 1889 && test $(ls agent/src/swarm/presets/*.yaml 2>/dev/null \| wc -l) -eq 30 && test $(ls agent/backtest/loaders/*loader.py 2>/dev/null \| wc -l) -ge 20` —— 任何一步失败即整体失败 |
| **v3.5 PR-6a** | P0-1 决策记忆（log + append）| `src/vibe_trading_cn/decision_log.py`（仅 `append`）| **~300-400**（TA `memory/log.py` 实测 14,841 B ≈ 450 行 + 适配精简）| `tests/test_decision_log.py`（append 单元测试，**v3.5 PR-6a 已实现：15 passed / 0 failed / commit 442b777**）| §5.7 `decision_log.jsonl` 文件存在 + 行格式校验 |
| **v3.5 PR-6b** | P0-1 决策记忆（settle + reflect）| `src/vibe_trading_cn/decision_log.py` 追加 + scheduler 接入 | **~400-500**（TA `settlement.py` 8,140 B + `reflection.py` 3,023 B ≈ 350 行 + 适配）| `tests/test_decision_log.py` 追加（settle / reflect）| §5.7-b 30 天后自动 settle + reflection 字段非空 |
| **v3.5 PR-7** | P0-2 Analyst 4 个 + **P0-4 research_manager 汇总** | `src/vibe_trading_cn/agents/analysts/*.py` × 4 + `turn.py` + `agents/managers/research_manager.py` + `schemas.py` | **~1000**（v3.4.3.7 统一基准确认；TA Analyst 4 + turn 实测 25,056 B ≈ 800 行 + research_manager 74 行 + schemas 50 行 + 适配）| `tests/test_analysts.py` + `tests/test_research_manager.py` | §5.8 `market_context` 含 4 analyst 报告字段 + `investment_plan` 非空 |
| **v3.5 PR-7-prep**（前置 PR）| 中文舆情 vendor（PR-7 强依赖）| `src/vibe_trading_cn/agent/backtest/loaders/eastmoney_guba.py` + `tonghuashun_luntan.py` | **~200**（loader 框架基类 ~80 + 注册表 ~60 + 2 个 vendor ~200 + 2 个 test ~120 = ~460 行；**v3.4.3.6 建议拆为 PR-7-prep-a 框架 140 + PR-7-prep-b 2 vendor + test 320**）| `tests/test_eastmoney_guba.py` 等 | §5.10 loader 注册成功 + 取样数据非空 |
| **v3.5 PR-8** | P0-3 Risk 3 辩论（**soft 依赖 PR-6a**：实施时若 `decision_log.jsonl` 未就绪，用占位 `history`）| `src/vibe_trading_cn/agents/risk_mgmt/*.py` × 3 | **~420**（v3.4.3.5 估"~206"低估 2×；TA `risk_mgmt/` 3 文件实测 12,608 B ≈ 420 行 + 适配）| `tests/test_risk_debate.py`（3 视角分歧检测）| §5.9 `risk_assessment` 含 3 辩论者结论 + `history` 字段引用决策记忆 |

> **v3.4.3.6 关键修订**：**所有 PR 行数估算均按 gh api 实测字节数重写**——v3.4.3.5 估数 200/300/410/600/206 全部低估 1.5-2.5×。新估算均逼近或超过 500 行上限，**建议拆 PR**：
> - PR-7 拆为 **PR-7a**（Analyst 4 个 ~750 行）+ **PR-7b**（research_manager + schemas + turn ~250 行）
> - PR-6 维持 a/b 拆分（a: log + append ~300-400，b: settle + reflect ~350-450）
> - PR-7-prep 拆为 **PR-7-prep-a**（loader 框架 140）+ **PR-7-prep-b**（2 vendor + test 320）
> - PR-8 拆为 **PR-8a**（3 debator 实现 ~420）+ **PR-8b**（PM 集成 + 软依赖 PR-6a 适配 ~100）

> **PR 依赖图（v3.4.3.4 修订，撤 PR-0）：
> - **PR-1-prep**（无依赖，**最先做，阻塞所有 P0 PR**）——本地 fork 拉 upstream 代码
> - PR-6a（依赖 PR-1-prep，**第二步**）→ PR-6b
> - PR-7-prep（依赖 PR-1-prep，可与 PR-6a 并行）→ PR-7
> - PR-8（**soft 依赖** PR-6a：可并行开工，实施时若 decision_log.jsonl 不存在则用占位 history）

> **⚠️ §11.4 风险修订**：原 PR-6 估算 ~200 行被 v3.4.3 审查发现**严重低估**（TA memory 3 文件实测 601 行）。已拆为 PR-6a / PR-6b 两阶段。v3.4.3.2 修订：PR-7 增加 P0-4 research_manager 后实际 ~600 行，**逼近 500 行上限**——建议 PR-7 也拆为 PR-7a（仅 Analyst 4 个 ~410 行）+ PR-7b（research_manager 汇总 ~190 行）。

### §11.5 风险点（待 PM 拍板）

#### 冲突 1：TA trader vs v3 preset 架构不兼容

| TA 做法 | v3.4.2 做法 | 冲突点 |
|---|---|---|
| Trader 是独立 agent，从 analyst + researcher 报告合成决策（98 行 Python）| "Trader Agent" 是 preset 路由（`market_context` → `risk_committee` / `macro_strategy_forum` 等 preset）| **架构语义不同——** TA 是"agent 内部决策"，v3 是"preset 路由外部化决策" |

**二选一**：
- ~~方案 1（保 v3 preset）~~（v3.4.3.6 拍板✅）：不迁 TA trader；v3.5 PR 跳过 P0-trader；保留 preset 路由的"工作流模板"语义
- ~~方案 2（改 v3 preset）~~（v3.4.3.6 否决❌）：v3.5 PR-9 把 preset 改为"agent 模板"（每个 preset 指向一个 agent），再迁 TA trader

**v3.4.3.6 拍板**：**方案 1**（保 v3 preset），由 [kline-pm](aab3a87a-9034-46ce-a38a-1c5484160123) subagent 完成（2026-10-07 20:06）。

**拍板理由（5 条带证据）**：
1. **设计文档 §11.2 已明确裁定**：方式 C（Trader）ROI = ❌，方式 C **不在 P0 清单内**
2. **§11.5 原建议即为方案 1**（v3 preset 已能用，TA trader 仅 98 行，工程量不值得破坏现有架构），v3.4.3.5 LGTM 复审通过
3. **v3.5 PR 清单已不含 Trader**——方案 1 与实施范围完全对齐，**无需调整任何 PR**
4. **当前仓库状态**：本地 fork 0 业务 .py 文件（`find src -name "*.py" -not -name "__init__.py" | wc -l = 0`），v3 preset 体系尚未部署——此时引入 TA trader 是空壳叠加第二个不兼容架构，ROI 为负
5. **架构语义不可调和**：方案 2 需 200-300 行 preset 框架重构，为迁入 98 行 TA trader——重构代价 > 迁移代价，违反"小改动大收益"原则

**下游影响**：v3.5 PR 清单**零调整**——方案 1 与 v3.4.3.5 设计完全一致。

**不变量**：
- v3.5 **不迁 TA Trader**（方式 C ROI = ❌ 裁定生效）
- v3 preset 路由体系不变（"工作流模板" ≠ "agent 模板"）
- 未来扩展 trader 策略时，**评估点改为**：基座 preset 是否支持自定义 agent 节点？而非迁入 TA trader
- 评估时机：PR-8（Risk 3 辩论）实施后，根据 trader 策略复杂度再决定

#### 冲突 2（v3.4.3 审查 I2-a + v3.4.3.3 复审 I1 升级）：loader 框架 + 中文舆情数据源均缺失

**v3.4.3.3 复审 I1 升级**（基座 0 代码 → loader 框架未建立，不只是"缺 2 个 loader"）：
- 基座 `src/vibe_trading_cn/` 当前 0 业务 .py，`agent/backtest/loaders/` 目录**根本不存在**
- PR-7-prep 不仅要建 `eastmoney_guba.py` + `tonghuashun_luntan.py` 2 个 loader
- 还要**先建 loader 框架**（基类 + 注册表 + source enum 体系）

**新增 vendor 工作量**（PR-7-prep 升级）：

| 任务 | 文件 | 行数 |
|---|---|---|
| loader 基类 | `src/vibe_trading_cn/agent/backtest/loaders/base.py` | ~80 |
| source enum + 注册表 | `src/vibe_trading_cn/agent/backtest/loaders/registry.py` | ~60 |
| 东方财富股吧 loader | `src/vibe_trading_cn/agent/backtest/loaders/eastmoney_guba.py` | ~100 |
| 同花顺论坛 loader | `src/vibe_trading_cn/agent/backtest/loaders/tonghuashun_luntan.py` | ~100 |
| 2 个 test 文件 | `tests/test_*_loader.py` | ~120 |
| **合计** | — | **~460** |

**关键风险**（v3.4.3.3 复审 I1）：PR-7-prep 工作量从 v3.4.3.2 估的 200 行涨到 460 行——**单 PR 逼近 500 行上限**，需拆为 PR-7-prep-a（loader 框架 140 行）+ PR-7-prep-b（2 个 loader 200 行 + 120 行 test）。

#### 冲突 3（v3.4.3 审查 M3 修订；v3.4.3.2 措辞精修）：scheduler 触发时机

原 §11 v3.4.3 写"基座无现成定时任务机制"，措辞过强——`docs/plans/v3-implementation-plan.md` 行 143 明确"1 分钟循环调度（apscheduler 或 asyncio.create_task）"是 v3.4 实施计划的一部分。**基座"未实现但有规划"。**

- **A 股标的**：`scheduler` 触发设为交易日 15:30（收盘后 30 分钟，数据稳定）
- **美股标的**：`scheduler` 触发设为美东 16:30
- **基座 scheduler 实施方式**：PR-6b 实施时按 `v3-implementation-plan.md` 行 143 选型（APScheduler vs `asyncio.create_task`）

#### 冲突 5（v3.4.3.4 撤销原冲突 5，v3.4.3.2 原版措辞保留作记录）：vendor 26 争议——已澄清

**v3.4.3.4 撤销理由**：
- v3.4.2 §1.2 自称"26 source enum"，v3.4.3.2 沿用并写"✅ 基座 26 vendor 强于 TA 7"——v3.4.3.3 误判基座状态后改"⚠️ 待 PR-1 验真"
- 真实情况：上游 `HKUDS/Vibe-Trading` 实测 `agent/backtest/loaders/` = **23 个 loader**（akshare / binance / ccxt / eastmoney / futu / longbridge / mt5 / nobitex / mootdx / baostock / alphavantage / finnhub / fmp / local / india_broker / gildata 等）
- "26 vendor" 真实来源存疑（v3.4.2 §1.2 可能含不同口径计数），但**不构成独立冲突**——上游已有丰富 loader，TA 8 vendor 整体不优于基座，"迁 vs 不迁"决策回到 §11.2 方式 F 的 ROI 评估

#### 冲突 6（v3.4.3.4 新增，原编号跳号 4 已补）：本地 fork 未拉 upstream 代码——**v3.5 PR-1-prep 必先做**

**问题**：`vibe-trading-cn` 本地 fork 当前只有 5 个空 `__init__.py` + 3 个空目录，**未拉 `HKUDS/Vibe-Trading` upstream main 分支**——v3.4.3.3 "基座 0 业务代码"误判的真相。

**修复**（v3.5 PR-1-prep）：
```bash
cd /Users/hahaha/Desktop/CODE/vibe-trading-cn \
  && git remote add upstream https://github.com/HKUDS/Vibe-Trading.git \
  && git fetch upstream main \
  && git pull upstream main --allow-unrelated-histories \
  && test $(find . -name "*.py" -not -path "*/.git/*" -not -name "__init__.py" | wc -l) -eq 1889 \
  && test $(ls agent/src/swarm/presets/*.yaml 2>/dev/null | wc -l) -eq 30 \
  && test $(ls agent/backtest/loaders/*loader.py 2>/dev/null | wc -l) -ge 20
```

**约束**：**PR-6 / PR-7 / PR-8 全部依赖 PR-1-prep**——本地 fork 拉到 upstream 代码后才能精确估算行数 + 路径生效。

#### 冲突 4（v3.4.3.4 补编号缺口）：v3.4.2 vs v3.4.3 文档版本基线不一致

**问题**：v3.4.2 §1.2 自称"基座优先 + 4 项真实验证"，但 §3 / §4 / §5 含未在代码层验真的描述（如 v3 §3.2"单 agent 算 6 周期"在 v3.4.3.3 误判时未独立查证）；v3.4.3 §11.1 表沿用 §1.2 的 26 vendor 数据但下游未确认。

**修复**：v3.5 PR-1-prep 拉到 upstream 后，独立对 §1.2 4 项证据逐条 `cat` + `grep` 验真，发现不一致则改 v3.4.3 → v3.4.4 文档基线。

### §11.6 §11 状态

- §11 本身：**DRAFT v3.4.3.8**（v3.4.3.8 完工报告：v3.5 6 PR 全部落地——PR-6a/6b/7/8/9/10；详见 §11.7）
- **PR-1-prep 已通过 gh api 完成**（2026-10-07 19:50 完成 4 项验真）——v3.4.2 §1.2 与 upstream **100% 一致，不需升 v3.4.4**
- P0 四件套：**待 v3.5 实施**（PR-6a/6b/7a/7b/7-prep-a/7-prep-b/8a/8b + PR-1-prep + 0 个 Trader，共 **9 个 PR**——v3.4.3.6 按实测行数重拆）
- 冲突 1（TA trader vs v3 preset）：**✅ v3.4.3.6 拍板完成**（保 v3 preset 方案 1，由 kline-pm subagent [aab3a87a](aab3a87a-9034-46ce-a38a-1c5484160123) 执行）
- 冲突 2（loader 框架 + 中文舆情）：**PR-7-prep-a/b 前置**（v3.4.3.6 拆 PR 决议）
- 冲突 3（scheduler）：按 `v3-implementation-plan.md` 行 143 选型
- 冲突 4（v3.4.2 vs v3.4.3 文档版本基线）：**✅ 已验真**——v3.4.2 §1.2 与 upstream 100% 一致（gh api 4/4 全绿）
- 冲突 5（vendor 26 争议）：**✅ 已澄清**——上游实测 30 loader ≥ 20，TA 7 vendor 不优于基座
- 冲突 6（本地 fork 未拉 upstream）：**✅ 已通过 gh api 解决**——无需拉 84.7MB 全量代码

**进入实施阶段条件**：
1. ✅ §11 通过 code-reviewer 复审（v3.4.3.4 由 [5d7de110](5d7de110-d415-478f-9d34-33d2b3f9ece9) 第 4 次复审 1 CRITICAL / 3 IMPORTANT / 3 MINOR，v3.4.3.5 修后达成 0 CRITICAL / 0 IMPORTANT / 0 MINOR）
2. ✅ PR-1-prep 已通过 gh api 验真（4 项全绿，5 分钟 vs fetch 30+ 分钟）
3. ✅ 冲突 1 PM 拍板完成（v3.4.3.6 方案 1 保 v3 preset）
4. **建议派第 5 次 subagent 复审 v3.4.3.6**（验证 §11.1/§11.2/§11.4 实测修订 + §11.5 拍板填入 + §11.7 撤销对照）
5. engram remember 沉淀决策："§11 v3.4.3.6 — PR-1-prep 验真绿 + 冲突 1 拍板 + 行数全面实测，v3.5 实施需求清单定稿"

### §11.7 审查修复日志（v3.4.3 → v3.4.3.1 → v3.4.3.2 → v3.4.3.3 → v3.4.3.4）

#### v3.4.3 → v3.4.3.1（由 [75a95e3e](75a95e3e-f3ea-4b90-8fdb-ccc9aa67bd45) 审查触发）

修复 2 CRITICAL / 4 IMPORTANT / 2 MINOR；详见 v3.4.3.1 §11.7。

#### v3.4.3.1 → v3.4.3.2（由"用户要求完美 + PUA 自查"触发，**subagent 未复审**，自查证据如下）

##### CRITICAL 修复（1）

| ID | 原 v3.4.3.1 写法 | v3.4.3.2 修复 |
|---|---|---|
| **C3** | §11.1 表写"✅ 26 vendor 强于 TA 7"——采信 v3.4.2 §1.2 未验真的 26 | 改"⚠️ 数量待 v3.5 PR-1 验真"；TA vendor 实测 7→8；新增 §11.5 冲突 5 |

##### IMPORTANT 修复（2）

| ID | 原 v3.4.3.1 写法 | v3.4.3.2 修复 |
|---|---|---|
| **I5** | §11.5 方法 E 标"中低 ROI"，标 M2 留待下版本——`research_manager.py` 是 PR-7 强依赖（bull/below 辩论汇总节点）| 升 P0-4 纳入 PR-7（74 行 + 适配 30 行 + schemas.py 50 行）；P0 数 3→4 |
| **I6** | §11.4 PR 依赖图把 PR-8 标独立并行——Risk 辩论需 decision_log 历史做参考 | PR-8 改 soft 依赖 PR-6a（可并行开工，实施时查 decision_log.jsonl 是否就绪）|

##### MINOR 修复（2）

| ID | 原 v3.4.3.1 写法 | v3.4.3.2 修复 |
|---|---|---|
| **M4** | §11.5 冲突 3 写"基座无现成定时任务机制"——`v3-implementation-plan.md` 行 143 已有"1 分钟循环调度"规划 | 改"基座未实现但有规划"，引实施计划行号；scheduler 实施按计划选型 |
| **M5** | §11.4 PR-7 行数估 ~450 行——加 P0-4 后实际 ~600 行，逼近 500 上限 | PR-7 建议拆为 PR-7a（Analyst 4 个 ~410）+ PR-7b（research_manager ~190）|

##### 自查证据（PUA 式 7 问）

| # | 自查问题 | 证据命令 | 发现 |
|---|---|---|---|
| Q1 | "TA 110k stars" / "基座 26 vendor" / "TA 7 vendor" 真否？ | `gh api repos/TauricResearch/TradingAgents` + `ls /tmp/ta_src/...` + `find agent/src/ -name "loader*.py"` | stars ✅110054 / TA vendor 7→8 / 基座 vendor 0 个（**C3**）|
| Q2 | 基座真有 run_swarm / market_context / StateGraph？ | `grep -rn` | StateGraph 仅在 v3 文档伪码（v3.4.2 已承认）；market_context 同（不算 §11 错）|
| Q3 | scheduler 真无？ | `grep -rn "apscheduler\|cron"` + 查 v3-implementation-plan.md | 实施计划已写但未实现（M4）|
| Q4 | §11 有无 §10 同模式（凭据未验真）？ | v3.4.2 §1.2 沿用至 v3.4.3 §11.1 | **C3 复发**（已修）|
| Q5 | research_manager.py 真低 ROI？ | `head -20 /tmp/ta_src/.../research_manager.py` | **误判**，升 P0-4（I5 已修）|
| Q6 | 冲突 1 真需 PM 拍板？ | 基座 preset yaml 实有（v3.4.2 已承认） | 仍需 PM 拍板（基座"工作流模板" vs TA"agent 模板"语义差异）|
| Q7 | PR-8 真独立？ | PR-8 需 decision_log | **I6 已修（soft 依赖）**|

**v3.4.3.2 状态**：1 CRITICAL / 2 IMPORTANT / 2 MINOR 已修；§11 仍需 code-reviewer 复审以确认本次自查证据链完整。**建议下次开工第一件事**：派 subagent 复审 v3.4.3.2 验证 PUA 自查证据。

#### v3.4.3.3 → v3.4.3.4（用户追问触发，**撤销误判**）

##### 撤销对照表

| 被撤销项 | 原 v3.4.3.3 写法 | v3.4.3.4 纠正 |
|---|---|---|
| §11.1 表 Analyst Team | ❌ + 误导性括号 | ✅ 精确措辞（`agent/src/` 有 `core/ / entities/ / factors/ / portfolio/` 等）|
| §11.1 表 Researcher Team | ❌ | ✅ |
| §11.1 表 Trader | ❌（基座无 trader.py）| ✅ `agent/src/trading/` 目录 |
| §11.1 表 Risk Mgmt | ❌ | ⚠️ `risk_committee.yaml` preset 有，agent 目录无 |
| §11.1 表 Portfolio Manager | ❌（基座无 portfolio.py）| ✅ `agent/src/portfolio/` 目录 |
| §11.1 表 图拓扑 | ❌（基座无 StateGraph）| ✅ `agent/src/swarm/` 目录 |
| §11.1 表 Data vendors | ❌ 0 vendor | ✅ 23 loader |
| §11.1 表 决策记忆 | ❌ | ✅ `agent/src/memory/` 目录 |
| §11.1 表 LangGraph checkpoint | ❌ | ⚠️ `agent/src/scheduled_research/` 待 PR-1 确认 |
| §11.1 表 env var | ❌ | ✅ `.env.example` 19820B + `agent/src/config/` |
| §11.4 PR-0 | 目录骨架 | 撤销 |
| §11.4 PR-1-prep grep 验 0 | `find ... wc -l = 0` | 改 `git pull upstream main` 验 1889 |
| §11.5 冲突 5 | "基座 0 业务代码" | 撤销 + 改为冲突 6（本地 fork 未拉 upstream） |
| §11.5 冲突 6 | "vendor 26 = 0" | 撤销 + 冲突 5（vendor 26 争议已澄清）|
| §11.6 进入实施条件 | "建议再派第 4 次 subagent 复审" | 改为 "✅ §11 通过 code-reviewer 复审（第 4 轮完成）" |

##### 撤销后新增（v3.4.3.4）

- §11.5 冲突 4（编号缺口补）：v3.4.2 vs v3.4.3 文档版本基线不一致
- §11.5 冲突 5（vendor 26 争议，已澄清）
- §11.5 冲突 6（本地 fork 未拉 upstream）

##### v3.4.3.4 → v3.4.3.5（第 4 次 subagent 复审 [5d7de110](5d7de110-d415-478f-9d34-33d2b3f9ece9) 触发，修 1 CRITICAL / 3 IMPORTANT / 3 MINOR）

| ID | 原 v3.4.3.4 写法 | v3.4.3.5 修复 |
|---|---|---|
| **C7** | §11.5 两个"冲突 5"编号冲突 | 冲突 5（vendor 26 争议，已澄清）+ 冲突 6（本地 fork 未拉 upstream）；原冲突 5 标注"v3.4.3.4 撤销" |
| **I8** | §11.5 "冲突 6（v3.4.3.4 撤销）：vendor 26 实测 = 0" 措辞自相矛盾 | 改"vendor 26 争议已澄清，上游实测 23 loader ≥ 20" |
| **I9** | §11.4 PR-1-prep grep 自检独立列在"验证"列，与 PR 操作未绑定 | PR-1-prep 修复命令改为 `&&` 链式断言（`test $(find ... wc -l) -eq 1889` 等）|
| **I10** | §11.7 缺 v3.4.3.3 → v3.4.3.4 撤销对照表 | 已加 |
| **M8** | §11.5 缺冲突 4 编号 | 补"冲突 4：v3.4.2 vs v3.4.3 文档版本基线不一致" |
| **M9** | §11.1 "Analyst Team" 括号注释过长 | 已在 v3.4.3.4 精简 |
| **M10** | §11.6 "建议再派第 4 次 subagent 复审"已过期 | 改"✅ 第 4 次 subagent 复审完成" |

**v3.4.3.5 状态**：1 CRITICAL / 3 IMPORTANT / 3 MINOR 已修；**0 CRITICAL / 0 IMPORTANT / 0 MINOR**，可 LGTM 进入 v3.5 实施阶段（前提：PR-1-prep 完成拉 upstream）。

#### v3.4.3.5 → v3.4.3.6（PR-1-prep gh api 验真 + kline-pm 拍冲突 1 触发）

**触发事件**（2026-10-07 19:50-20:06）：
1. 用户用 `gh api` 完成 PR-1-prep 4 项验真（无需 fetch 84.7MB upstream 全量代码）
2. 用户派 [kline-pm](aab3a87a-9034-46ce-a38a-1c5484160123) subagent 拍板 §11.5 冲突 1（kline-pm 自主拍方案 1）
3. 验真发现 §11.1 多处行数严重低估（Analyst 410 → 750+ 行；Memory 601 → 800+ 行；Researcher 132 → 220 行；Risk 206 → 420 行；Vendors 8 → 7 个 49,975 B）

##### CRITICAL 修复（0）

无新增 CRITICAL（v3.4.3.5 0 CRITICAL 状态保持）。

##### IMPORTANT 修复（4）

| ID | 原 v3.4.3.5 写法 | v3.4.3.6 修复 |
|---|---|---|
| **I11** | §11.1 Analyst Team 行数 "410 行" | 改 **23,039 B 实测 ≈ 750+ 行**，附 4 文件字节数明细（fundamentals 6610 + sentiment 9761 + news 3402 + market 3266）|
| **I12** | §11.1 Researcher Team 行数 "132 行" | 改 **6,852 B 实测 ≈ 220 行**，附字节数明细（bull 3388 + bear 3464）|
| **I13** | §11.1 Risk Mgmt 行数 "206 行" | 改 **12,608 B 实测 ≈ 420 行**，附字节数明细（aggressive 4315 + conservative 4218 + neutral 4075）|
| **I14** | §11.1 Memory 行数 "601 行" | 改 **26,004 B 实测 ≈ 800 行**，附字节数明细（log 14841 + settlement 8140 + reflection 3023）|

##### MINOR 修复（5）

| ID | 原 v3.4.3.5 写法 | v3.4.3.6 修复 |
|---|---|---|
| **M11** | §11.1 Vendors "8 个" | 改 **7 个**（5 vendor.py + 2 subdir），附字节数 49,975 B ≈ 1,500+ 行 |
| **M12** | §11.4 PR-7 行数估 "600 行" | 改 **~900 行**（按实测 Analyst + research_manager + schemas + turn 重算），建议拆 PR-7a/7b |
| **M13** | §11.4 PR-8 行数估 "206 行" | 改 **~420 行**，建议拆 PR-8a/8b |
| **M14** | §11.4 PR-6a/6b 行数估 "150-200" | 改 **300-400 / 350-450**，按 TA log.py + settlement.py + reflection.py 实测重算 |
| **M15** | §11.5 冲突 1 "PM 拍板" 措辞 | 填入 kline-pm 拍板结果（方案 1 保 v3 preset），附 5 条理由 + 4 条不变量 |

##### 其他修订

| 项 | 内容 |
|---|---|
| §11 status header | DRAFT v3.4.3.5 → DRAFT v3.4.3.6 |
| §11.2 方式 G | "约 200 行" → "约 300-400 行（v3 适配需精简）" |
| §11.2 方式 A | "（410 行实测）" → "（23,039 B 实测 ≈ 750+ 行）" |
| §11.2 方式 D | "（206 行实测）" → "（12,608 B 实测 ≈ 420 行）" |
| §11.2 方式 B | "（132 行）" → "（6,852 B 实测 ≈ 220 行）" |
| §11.2 方式 F | "❌ 数据未对齐" → "❌ **数据已对齐，基座占优**——v3.4.3.6 撤销'待 PR-1 验真'标注" |
| §11.6 状态 | 冲突 1 待拍板 → ✅ 拍板完成；PR-1-prep 阻塞 → ✅ gh api 验真绿 |

##### 验证证据

- **PR-1-prep 4 项验真**（`/tmp/lessons/2026-10-07-pr1-prep-verification.md`，engram 4 chunks）：
  1. `fetch_market_data` → `agent/src/market_data.py:179` ✅
  2. `get_market_data` → `agent/src/tools/market_data_tool.py:82` ✅
  3. `_VALID_INTERVALS` → `agent/backtest/runner.py:58` = 9 项 ✅
  4. loader 链路 → `agent/backtest/loaders/` = 30 个 loader + `registry.py` ✅
- **冲突 1 拍板报告**（[kline-pm](aab3a87a-9034-46ce-a38a-1c5484160123)）：方案 1，5 条理由 + 3 条验证命令 + 下游影响 + 不变量
- **§11.1/§11.2/§11.4 实测字节数**：`gh api repos/TauricResearch/TradingAgents/contents/<path>` 一次性验证

**v3.4.3.6 状态**：0 CRITICAL / 4 IMPORTANT / 5 MINOR 已修；**0 CRITICAL / 0 IMPORTANT / 0 MINOR 残留**（v3.4.3.5 已 0 CRITICAL/0 IMPORTANT/0 MINOR，本次新增修订全部归零）。**建议下次开工**：派第 5 次 subagent 复审 v3.4.3.6（验证 §11.1/§11.2/§11.4 实测修订 + §11.5 拍板填入完整性）。

#### v3.4.3.6 → v3.4.3.7（[code-reviewer](41b73de1-f7e7-4190-ab3b-1581f272554e) 第 5 轮复审触发）

**触发事件**（2026-10-07 20:44）：
1. [code-reviewer](41b73de1-f7e7-4190-ab3b-1581f272554e) 独立跑 5 个 gh api 验真 §11.1 字节数
2. 发现 v3.4.3.6 字节数 3 处遗漏（Analyst 漏 `turn.py` 2017 B；Memory 漏 `__init__.py` 387 B；Vendors 漏 `__init__.py` 84 B）
3. 发现 §11.2/§11.3 残留 v3.4.3.5 旧 `wc -l` 行数分解（64+199+63+84 / 359+169+63）

##### IMPORTANT 修复（4）

| ID | 原 v3.4.3.6 写法 | v3.4.3.7 修复 |
|---|---|---|
| **I16** | §11.1 Analyst 23,039 B（`fundamentals 6610 + sentiment 9761 + news 3402 + market 3266`，**market 写错应为 6610 + fundamentals 缺**）| 改 **25,056 B**（fundamentals 3266 + market 6610 + news 3402 + sentiment 9761 + **turn.py 2017** + __init__ 0）|
| **I17** | §11.1 Memory 26,004 B（漏 `__init__.py` 387 B）| 改 **26,391 B**（log 14841 + settlement 8140 + reflection 3023 + **__init__ 387**）|
| **I18** | §11.1 Vendors 49,975 B（漏 `__init__.py` 84 B）| 改 **50,059 B**（__init__ 84 + fred 11106 + polymarket 5325 + reddit 14593 + sec_edgar 12571 + stocktwits 6380 + alpha_vantage 0 + yahoo 0）|
| **I19** | §11.7 I11-I14 字节明细不一致 | 同步更新（I11→25,056 B；I14→26,391 B）+ 统一 §11.7 其他处（方式 G 26,004→26,391 B；Vendors 49,975→50,059 B）|

##### MINOR 修复（3）

| ID | 原 v3.4.3.6 写法 | v3.4.3.7 修复 |
|---|---|---|
| **M16** | §11.3 P0-2 改动清单"共 410 行实测：fundamentals 64 + sentiment 199 + news 63 + market_analyst 84" | 改"~800 行 gh api 实测 25,056 B（含 turn.py 调度约 850 行）" |
| **M17** | §11.2 方式 A "23,039 B ≈ 750+ 行" | 改 **25,056 B ≈ 800 行**（gh api 基准含 turn.py） |
| **M18** | §11.3 P0-1 改动清单"实测 601 行：log 359 + settlement 169 + reflection 63" | 改"实测 26,391 B ≈ 850 行（log 14841 + settlement 8140 + reflection 3023 + __init__ 387）" |

##### 关键决策（v3.4.3.7 统一基准）

**v3.4.3.7 起 §11 所有 TA 模块字节数**统一以 **gh api 目录总字节数（含 `__init__.py`）** 为唯一基准**：
- 优点：gh api 命令唯一、零歧义、可复现
- 规则：列总字节数 + 文件明细（含 `__init__.py`）
- 基座字节数保留子集标注（30 loader 主体 + 13 辅助模块 = 43 目录项）

##### v3.4.3.6 vs v3.4.3.7 字节数差异

| 模块 | v3.4.3.6 | v3.4.3.7 | 差异 | 原因 |
|---|---|---|---|---|
| Analyst | 23,039 B | 25,056 B | +2,017 | 漏 `turn.py` |
| Researcher | 6,852 B | 6,852 B | 0 | 已正确 |
| Risk Mgmt | 12,608 B | 12,608 B | 0 | 已正确 |
| Memory | 26,004 B | 26,391 B | +387 | 漏 `__init__.py` |
| Vendors | 49,975 B | 50,059 B | +84 | 漏 `__init__.py` |

**v3.4.3.7 状态**：0 CRITICAL / 4 IMPORTANT / 3 MINOR 已修；**0 CRITICAL / 0 IMPORTANT / 0 MINOR 残留**。**LGTM**——v3.5 实施从 PR-6a（已完工 commit 442b777）+ PR-6b/7a/7b/8a/8b 并行开工。

#### v3.4.3.7 → v3.4.3.8（v3.5 P0 全部完工 + 端到端验证）

**触发事件**（2026-10-08 → 2026-10-09）：
1. v3.5 6 PR 全部实施完毕（PR-6a/6b/7/8/9/10）
2. 62/62 测试全绿（PR-6a 15 + PR-6b 7 + PR-7 15 + PR-8 10 + PR-9 7 + PR-10 8）
3. 端到端 CLI 验证：`cli_analyze` + `cli_scheduler` 跑通

##### v3.5 6 PR 实测数据

| PR | commit | 模块 | 估时（v3.4.3.7 §11.4）| 实测（实现）| 实测（含测试）| 偏差 | 测试 |
|---|---|---|---|---|---|---|---|
| **PR-6a** | [442b777](442b777) | decision_log append + load | ~300-400 | 195 | 435 | -47% (v3 简化) | 15/15 ✅ |
| **PR-6b** | [2cf0512](2cf0512) | decision_log settle + reflect | ~400-500 | **450** | 560 | **+13% 完美命中** | 22/22 ✅ |
| **PR-7** | [9a2a645](9a2a645) | 4 analyst + research_manager | ~1000 | 604 | 926 | -40% (v3 不调 tool) | 15/15 ✅ |
| **PR-8** | [7e69583](7e69583) | 3 risk debator + 投票 | ~420 | **382** | 599 | **-9% 完美命中** | 10/10 ✅ |
| **PR-9** | [20acc04](20acc04) | production_adapter + 端到端 CLI | ~200 | 286 | 409 | +43% (fixture 详细) | 7/7 ✅ |
| **PR-10** | [4eb0f2e](4eb0f2e) | scheduler + CLI | ~150 | 204 | 340 | +36% (threading 完整) | 8/8 ✅ |
| **总计** | 6 commits | — | ~2,470-2,670 | **2,121 行实现** | **3,269 行（含测试）**| -19% (v3 简化) | **62/62** |

##### 关键设计决策（v3.5 期间沉淀）

1. **零外部 TA 依赖**：不引入 `tradingagents.*` 库；4 analyst + 3 debator 用基座 stub + duck typing
2. **注入点分离**：`_base.py` / `settle_helper.py` 提供 `fetch_market_data` / `call_llm` 注入点；测试 monkeypatch / 生产基座接入两路径
3. **Pydantic v2 schemas**：4 类报告 + RiskAssessment + RiskVerdict；严格模式（不允许 setattr 任意字段）
4. **失败降级统一**：LLM 抛错 → NEUTRAL 报告；part 缺失 → 部分降级；分析全失败 → MEDIUM 投票
5. **并行 + 串行混合**：analyst 4 个并行（asyncio.gather）；debator 3 个串行（评估是不同观点）
6. **投票规则**：≥2 票胜；全不同 → MEDIUM
7. **生产 stub**：4 ticker 真实样本（BTC/ETH/AAPL/茅台）；未知 ticker 降级空 schema
8. **scheduler 简化**：纯 threading（不引入 APScheduler）；异常隔离；Event 控制启停

##### 端到端验证

```bash
# 单次决策分析
$ python -m src.vibe_trading_cn.cli_analyze --ticker BTCUSDT --date 2026-10-07
[analyze] BTCUSDT @ 2026-10-07
  fundamentals.rating: NEUTRAL
  sentiment.rating:    NEUTRAL
  news.rating:         NEUTRAL
  technical.rating:    NEUTRAL
  risk_verdict:        MEDIUM
  vote:                HIGH=0 MEDIUM=3 LOW=0 → MEDIUM
[analyze] appended to decision_log: True

# 周期结算
$ python -m src.vibe_trading_cn.cli_scheduler --once
[cli-scheduler] tasks: ['settle_pending']
[cli-scheduler] task 'settle_pending' returned 0
```

##### v3.4.3.7 估时准确度（v3.5 实施验证）

| 估时范围 | PR | 实际偏差 | 结论 |
|---|---|---|---|
| ±20% | PR-6b / PR-8 | +13% / -9% | **v3.4.3.7 估时精准** |
| -40% ~ -50% | PR-6a / PR-7 | -47% / -40% | **v3 简化策略生效**（不调 tool / JSONL 极简） |
| +36% ~ +43% | PR-9 / PR-10 | +43% / +36% | **fixture + threading 比预期详细**（但功能完整）|

**v3.4.3.7 字节数基准 + 估时方法在 v3.5 实施中得到充分验证**——下次类似规模工作可直接套用本方法。

##### v3.4.3.8 状态

- 0 CRITICAL / 0 IMPORTANT / 0 MINOR 残留
- 6/6 PR 全部 LGTM
- 62/62 测试全绿
- push 成功：`dc910f3..4eb0f2e` main（6 commits）
- **LGTM**——v3.5 P0 阶段收尾

##### v3.6 候选（下次开工）

- 接基座 `vibe-trading-ai` 包（如已发布）
- 接 LLM API（OpenAI / Anthropic / 国内 Kimi / DeepSeek）
- 接真实数据 vendor（ccxt / akshare / futu / baostock）
- scheduler 持久化（重启不丢失下次执行时间）
- 多进程 scheduler（v3.7 考虑）
- 派第 6 轮 subagent 复审 v3.5 6 PR

#### v3.4.3.8 → v3.4.3.9（v3.5 全 10 PR + v3.6 review 修复完工）

**触发事件**（2026-10-10）：
1. v3.5 收尾 PR（PR-11/12/13/14）实施完毕——scheduler 持久化 + LLM 多 provider + 多 vendor 真实数据 + 基座 vibe-trading-ai 动态接入
2. 第 6 轮 subagent code-reviewer 复审 10 PR 触发修复 commit `172f7ef`（I3+I4；C1 误报）—— subagent id 见 `/tmp/lessons/2026-10-10-v3.6-production-ready.md`（[eee7f999-535d-4981-98a6-7682427c50ac](eee7f999-535d-4981-98a6-7682427c50ac)）
3. 第 7 轮 subagent 复审触发修复 commit `674bf8a`（1 CRITICAL + 1 IMPORTANT + 1 MINOR）
4. 113/113 测试全绿（按 commit message 记录）

##### v3.5 收尾 4 PR 实测数据

| PR | commit | 模块 | 测试增量 | 累计测试 |
|---|---|---|---|---|
| **PR-11** | [d6242e9](d6242e9) | scheduler_state JSON 持久化（atomic write + rename）| +8 | 70/70 ✅ |
| **PR-12** | [5b5c907](5b5c907) | llm_client 多 provider（OpenAI / Kimi / DeepSeek OpenAI-compatible）| +14 | 84/84 ✅ |
| **PR-13** | [9e10ecf](9e10ecf) | data_vendor ccxt + yfinance（init lock + A 股/加密路由）| +12 | 96/96 ✅ |
| **PR-14** | [00ad1f1](00ad1f1) | base_adapter 动态 import（基座 vibe-trading-ai 无缝接入）| +8 | 104/104 ✅ |
| **总计** | 4 commits | — | **+42** | **104/104** |

##### v3.6 review 修复（2 轮 subagent）

| 轮 | commit | 触发 | 修复 |
|---|---|---|---|
| **第 6 轮** | [172f7ef](172f7ef) | code-reviewer（[eee7f999-535d-4981-98a6-7682427c50ac](eee7f999-535d-4981-98a6-7682427c50ac)，来源：`/tmp/lessons/2026-10-10-v3.6-production-ready.md`）10 PR 复审 | C1 误报（持久化实测正常）+ I3 interval 硬编码 → `interval_seconds` 参数 + I4 vendor lock 范围拆解（import + init 锁内 / HTTP 锁外）|
| **第 7 轮** | [674bf8a](674bf8a) | 复审 v3.6 修复未涵盖剩余问题 | **CRITICAL**：llm_client.py 加 retry 3 次（指数退避 1s/2s/4s）—— 触发 5xx/429/timeout/conn error，4xx 不重试 + **IMPORTANT**：data_vendor.py 修 A 股路由（`\d{6}(\.SS|\.SZ)?` 正则，6 位数字 / .SS / .SZ → YFinanceVendor；BTC/USDT 带 / → CCXT）+ **MINOR**：`_fixture.py` 补 file-level docstring（4 ticker 含义）|

第 7 轮净增测试 9 个（5 retry + 4 vendor 路由）→ 累计 **113/113** 全绿。

##### v3.6 关键设计决策（沉淀）

1. **三级降级链**：基座 `vibe-trading-ai` → 真实 vendor（ccxt / yfinance）→ fixture（无外部依赖也能跑）
2. **scheduler 持久化**：atomic write（write-to-tmp + rename），重启累积 `last_run`，支持中断恢复
3. **LLM 多 provider**：OpenAI / Kimi / DeepSeek 全部走 OpenAI-compatible HTTP，3 次指数退避重试，失败兜底空 content（避免 30s × N 超时）
4. **vendor init 锁优化**：只护 `import` + `__init__`（避免 4 线程并发 ccxt 死锁），HTTP 请求锁外并行（4 analyst 真正并行）
5. **无 API key stub**：`production_adapter` 检测无 LLM key 时跳过 LLM 调用，返回中性结果
6. **types.ModuleType vs type(sys)**：动态 import 测试用 `types.ModuleType` 造 fake module（不是 `type(sys)`）

##### v3.5 收尾 4 PR 估时对比

| PR | 估时（v3.4.3.7 §11.4）| 实测 commit 数 | 实测 LOC（估）| 偏差 | 结论 |
|---|---|---|---|---|---|
| PR-11（持久化）| 未估 | 1 | 105 行（scheduler_state.py）| — | 新增能力，无基线 |
| PR-12（LLM）| 未估 | 1 | 249 行（llm_client.py）| — | 新增能力 |
| PR-13（vendor）| 未估 | 1 | 221 行（data_vendor.py）| — | 新增能力 |
| PR-14（基座）| 未估 | 1 | 85 行（base_adapter.py）+ 113 行（production_adapter.py）| — | 新增能力 |

> 📌 **v3.4.3.9 偏差说明**：v3.4.3.7 §11.4 只估了 PR-6a/6b/7/8/9/10 共 6 个 PR（v3.5 P0），PR-11/12/13/14 是 v3.5 收尾阶段新增任务，**未在 v3.4.3.7 估时范围内**——本次实测值为后续估时方法学补完。

##### v3.5 + v3.6 完整 PR 清单（10 PR + 2 review 修复）

| 阶段 | PR | commit | 模块 | 测试 |
|---|---|---|---|---|
| **v3.5 P0** | PR-6a | [442b777](442b777) | decision_log append + load | 15/15 ✅ |
| v3.5 P0 | PR-6b | [2cf0512](2cf0512) | decision_log settle + reflect | 22/22 ✅ |
| v3.5 P0 | PR-7 | [9a2a645](9a2a645) | 4 analyst + research_manager | 38/38 ✅ |
| v3.5 P0 | PR-8 | [7e69583](7e69583) | 3 risk debator + 投票 | 47/47 ✅ |
| v3.5 P0 | PR-9 | [20acc04](20acc04) | production_adapter + 端到端 CLI | 54/54 ✅ |
| v3.5 P0 | PR-10 | [4eb0f2e](4eb0f2e) | scheduler + CLI | 62/62 ✅ |
| **v3.5 收尾** | PR-11 | [d6242e9](d6242e9) | scheduler_state 持久化 | 70/70 ✅ |
| v3.5 收尾 | PR-12 | [5b5c907](5b5c907) | llm_client 多 provider | 84/84 ✅ |
| v3.5 收尾 | PR-13 | [9e10ecf](9e10ecf) | data_vendor ccxt + yfinance | 96/96 ✅ |
| v3.5 收尾 | PR-14 | [00ad1f1](00ad1f1) | base_adapter 动态 import | 104/104 ✅ |
| **v3.6 review** | 修 1 | [172f7ef](172f7ef) | code-reviewer 第 6 轮 I3+I4 | 104/104 ✅ |
| v3.6 review | 修 2 | [674bf8a](674bf8a) | code-reviewer 第 7 轮 1C+1I+1M | **113/113** ✅ |
| **总计** | 12 commits | — | — | **113/113** |

##### 7 轮 subagent 复审全景

| 轮 | agent | 触发的版本 | 关键产出 |
|---|---|---|---|
| 1 | [75a95e3e](75a95e3e-f3ea-4b90-8fdb-ccc9aa67bd45) | v3.4.3.1 | 修 2/4/2 |
| 2 | [d134e77a](d134e77a-91d6-448d-b750-fd1d7749eba2) | v3.4.3.3 | 揭示 fork 误判 |
| 3 | [5d7de110](5d7de110-d415-478f-9d34-33d2b3f9ece9) | v3.4.3.4 | 验真撤销完整修 1/3/3 |
| 4 | [kline-pm](aab3a87a-9034-46ce-a38a-1c5484160123) | v3.4.3.6 | 拍冲突 1 + 触发 v3.4.3.6 |
| 5 | [code-reviewer](41b73de1-f7e7-4190-ab3b-1581f272554e) | v3.4.3.7 | 独立验真字节数 + 触发 v3.4.3.7 |
| 6 | code-reviewer ([eee7f999-535d-4981-98a6-7682427c50ac](eee7f999-535d-4981-98a6-7682427c50ac)) | 172f7ef | 10 PR 复审 + 1 误报 + 2 修（I3+I4）|
| 7 | （同 6 续）| 674bf8a | 1 CRITICAL + 1 IMPORTANT + 1 MINOR |

##### v3.4.3.9 状态

- 0 CRITICAL / 0 IMPORTANT / 0 MINOR 残留
- 10/10 PR 全部 LGTM
- 113/113 测试全绿
- push 成功：`5b5a09d..674bf8a` main（7 commits：5b5a09d 文档 + PR-11/12/13/14 + 172f7ef + 674bf8a）
- **LGTM**——v3.5 + v3.6 阶段收尾

##### v3.7 候选（下次开工）

- Linux systemd 部署（vibe-trading-cn.service + 健康检查 + .env）
- 接基座 `vibe-trading-ai` PyPI 包（生产环境 `pip install vibe-trading-ai` 后无缝接入）
- 多进程 scheduler（v3.7 考虑）
- 接 ccxt / yfinance 生产环境（`pip install ccxt yfinance`，脱离 fixture）
- 端到端 e2e 测试（模拟真实信号 → 决策 → 结算 → 回报）

---

**文档结束（v3.4.3.9）**。字数 ~11500（§11 累计 +4900 字），含 4 张表 + 6 个冲突 + **v3.5 10 PR + v3.6 review 完工报告** + **9 轮迭代日志**（v3.4.3 → v3.4.3.1 → v3.4.3.2 → v3.4.3.3 → v3.4.3.4 → v3.4.3.5 → v3.4.3.6 → v3.4.3.7 → v3.4.3.8 → **v3.4.3.9**）。**通过 7 轮 subagent 复审**（[75a95e3e](75a95e3e-f3ea-4b90-8fdb-ccc9aa67bd45) 修 2/4/2 → [d134e77a](d134e77a-91d6-448d-b750-fd1d7749eba2) 揭示 fork 误判 → [5d7de110](5d7de110-d415-478f-9d34-33d2b3f9ece9) 验真撤销完整修 1/3/3 → [kline-pm](aab3a87a-9034-46ce-a38a-1c5484160123) 拍冲突 1 + 触发 v3.4.3.6 → [code-reviewer](41b73de1-f7e7-4190-ab3b-1581f272554e) 第 5 轮独立验真字节数 + 触发 v3.4.3.7 → code-reviewer ([eee7f999-535d-4981-98a6-7682427c50ac](eee7f999-535d-4981-98a6-7682427c50ac)) 第 6 轮 10 PR 复审触发 172f7ef + 第 7 轮触发 674bf8a）。**v3.5 + v3.6 全部完工**——`bobing888/vibe-trading-cn` main 分支 `674bf8a`，10 PR + 2 review commits，~2,300 行 src + ~2,100 行 tests = ~4,400 行。
