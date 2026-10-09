"""v3.5 PR-7 Sentiment Analyst — 社交情绪分析

v3 简化（对照 TA sentiment_analyst.py 9761 B ≈ 320 行，TA 最大的 analyst）：
- 不调 langchain ChatPromptTemplate + bind_tools
- 单次 LLM 调用 + 注入基座 fetch_market_data.social
"""

from __future__ import annotations

from . import _base
from ._base import call_llm, fetch_market_data, _parse_rating
from .schemas import SentimentReport


SYSTEM_PROMPT = (
    "You are a sentiment analyst reviewing social media signals. "
    "Given Reddit sentiment (-1 to +1) and StockTwits bull ratio (0 to 1) "
    "for a ticker, write a 2-4 sentence summary and end with "
    "STRONG_BUY / WEAK_BUY / NEUTRAL."
)


def run(ticker: str, trade_date: str) -> SentimentReport:
    """情绪分析 — 拉数据 + 调 LLM + 解析 rating"""
    data = fetch_market_data(ticker, trade_date)
    social = data.get("social", {})

    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", (
            f"Ticker: {ticker}\n"
            f"Trade date: {trade_date}\n"
            f"Reddit sentiment: {social.get('reddit_sentiment', 'n/a')}\n"
            f"StockTwits bull ratio: {social.get('stocktwits_bull_ratio', 'n/a')}"
        )),
    ]

    try:
        result = call_llm(messages)
        content = result.content
        rating = _parse_rating(content)
    except Exception as e:
        return SentimentReport(
            ticker=ticker, trade_date=trade_date,
            summary=f"情绪分析失败（{type(e).__name__}），已降级为 NEUTRAL",
            rating="NEUTRAL",
        )

    return SentimentReport(
        ticker=ticker, trade_date=trade_date,
        reddit_sentiment=social.get("reddit_sentiment"),
        stocktwits_bull_ratio=social.get("stocktwits_bull_ratio"),
        summary=content,
        rating=rating,
    )


__all__ = ["run"]
