"""v3.5 PR-7 analyst 共享模块（fetch_market_data / call_llm 注入点）

设计要点：
- 纯 stub 默认实现（生产由基座 loader 适配）
- 与 settle_helper 同模式（PR-6b 既有）
- 不引入 tradingagents.* 库
"""

from __future__ import annotations

from typing import Any


def fetch_market_data(ticker: str, trade_date: str) -> dict[str, Any]:
    """拉 ticker 在 trade_date 的市场数据

    返回结构：
        {
            "ticker": str,
            "ohlcv": {date: {open, high, low, close, volume}, ...},
            "fundamentals": {pe, pb, roe, revenue_yoy},
            "news": [{"title", "sentiment", "date"}, ...],
            "social": {reddit_sentiment, stocktwits_bull_ratio},
        }

    默认 stub：测试 monkeypatch；生产由基座 loader 适配。
    """
    return {
        "ticker": ticker,
        "ohlcv": {},
        "fundamentals": {},
        "news": [],
        "social": {},
    }


def call_llm(messages: list[tuple[str, str]]) -> Any:
    """调 LLM（duck typing：call(messages) → obj with .content）

    默认 stub：返回空内容（测试用 monkeypatch；生产注入基座 LLMClient）。
    """
    class _Stub:
        content = ""
    return _Stub()


def _parse_rating(content: str) -> str:
    """从 LLM 文本中提取 STRONG / WEAK / NEUTRAL

    简化（对照 TA 严格 prompt）：用关键词匹配。
    """
    c = content.upper()
    # 优先：STRONG_BUY / STRONG_SELL
    if "STRONG_BUY" in c:
        return "STRONG"
    if "STRONG_SELL" in c or "WEAK_BUY" in c or "WEAK_SELL" in c:
        return "WEAK"
    # 简单：含 STRONG → STRONG；含 WEAK → WEAK
    if "STRONG" in c:
        return "STRONG"
    if "WEAK" in c:
        return "WEAK"
    return "NEUTRAL"


__all__ = ["fetch_market_data", "call_llm", "_parse_rating"]
