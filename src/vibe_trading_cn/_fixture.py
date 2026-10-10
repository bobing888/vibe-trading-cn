"""v3.5 PR-9 _fixture — 真实样本数据（按 ticker 查表）

设计：
- 少量已知 ticker 预填真实样本（BTC / ETH / AAPL / 600519）
- 未知 ticker 降级为通用空 schema（让 4 analyst 走"无数据" 路径）
- 数据不连真实 API（PR-9 简化 — v3.6+ 接基座）

已知 ticker 说明（第 6 轮 review IMPORTANT 修复）：
- BTCUSDT：BTC/USDT 加密币（参考市值 ~1.3T USD）
- ETHUSDT：ETH/USDT 加密币（参考市值 ~400B USD）
- AAPL：苹果公司（美股，参考 PE 28.5）
- 600519：贵州茅台（A 股，参考 PE 25.8 — 需 yfinance 加 .SS 后缀）
"""

from __future__ import annotations

from typing import Any


_KNOWN_TICKERS = {
    "BTCUSDT": {
        "fundamentals": {
            "pe": None,  # 币类无 PE
            "pb": None,
            "roe": None,
            "revenue_yoy": 0.15,  # 链上活跃度增长
            "market_cap": 1_300_000_000_000,  # 1.3T USD
        },
        "news": [
            {"title": "BTC 突破 65000 美元，创年内新高", "sentiment": "positive",
             "date": "2026-10-05"},
            {"title": "机构投资者持续增持 BTC ETF", "sentiment": "positive",
             "date": "2026-10-06"},
        ],
        "social": {
            "reddit_sentiment": 0.72,
            "stocktwits_bull_ratio": 0.68,
        },
        "ohlcv": {
            "2026-10-01": {"open": 63000, "high": 64500, "low": 62800, "close": 64200, "volume": 28000},
            "2026-10-02": {"open": 64200, "high": 65800, "low": 64100, "close": 65500, "volume": 31000},
            "2026-10-03": {"open": 65500, "high": 66200, "low": 65100, "close": 65900, "volume": 29500},
            "2026-10-04": {"open": 65900, "high": 66500, "low": 65500, "close": 66200, "volume": 27000},
            "2026-10-05": {"open": 66200, "high": 66800, "low": 66000, "close": 66500, "volume": 25000},
            "2026-10-06": {"open": 66500, "high": 67200, "low": 66400, "close": 67000, "volume": 23000},
        },
    },
    "ETHUSDT": {
        "fundamentals": {
            "pe": None, "pb": None, "roe": None,
            "revenue_yoy": 0.12,
            "market_cap": 400_000_000_000,
        },
        "news": [
            {"title": "ETH Dencun 升级后 L2 费用大降", "sentiment": "positive",
             "date": "2026-10-04"},
        ],
        "social": {
            "reddit_sentiment": 0.65,
            "stocktwits_bull_ratio": 0.62,
        },
        "ohlcv": {
            "2026-10-01": {"open": 3400, "high": 3500, "low": 3380, "close": 3480, "volume": 150000},
            "2026-10-02": {"open": 3480, "high": 3550, "low": 3470, "close": 3530, "volume": 165000},
        },
    },
    "AAPL": {
        "fundamentals": {
            "pe": 28.5, "pb": 47.2, "roe": 1.5,
            "revenue_yoy": 0.06, "market_cap": 3_400_000_000_000,
        },
        "news": [
            {"title": "Apple 发布新款 iPhone，预订量超预期", "sentiment": "positive",
             "date": "2026-10-06"},
        ],
        "social": {
            "reddit_sentiment": 0.55,
            "stocktwits_bull_ratio": 0.60,
        },
        "ohlcv": {
            "2026-10-01": {"open": 225, "high": 228, "low": 224, "close": 227, "volume": 55000000},
            "2026-10-02": {"open": 227, "high": 230, "low": 226, "close": 229, "volume": 60000000},
        },
    },
    "600519": {  # 贵州茅台
        "fundamentals": {
            "pe": 25.8, "pb": 9.5, "roe": 0.32,
            "revenue_yoy": 0.15, "market_cap": 2_000_000_000_000,
        },
        "news": [
            {"title": "茅台 Q3 业绩超预期", "sentiment": "positive", "date": "2026-10-05"},
        ],
        "social": {
            "reddit_sentiment": 0.50,  # A 股无 reddit
            "stocktwits_bull_ratio": 0.65,
        },
        "ohlcv": {
            "2026-10-01": {"open": 1600, "high": 1620, "low": 1595, "close": 1615, "volume": 2000000},
            "2026-10-02": {"open": 1615, "high": 1635, "low": 1610, "close": 1630, "volume": 2200000},
        },
    },
}


def get_market_data(ticker: str, trade_date: str) -> dict[str, Any]:
    """按 ticker 查表返回真实样本结构

    未知 ticker：返回通用空 schema
    """
    base = _KNOWN_TICKERS.get(ticker.upper())
    if base is None:
        return {
            "ticker": ticker,
            "trade_date": trade_date,
            "fundamentals": {},
            "news": [],
            "social": {},
            "ohlcv": {},
        }
    return {
        "ticker": ticker,
        "trade_date": trade_date,
        **base,
    }


__all__ = ["get_market_data"]
