"""v3.5 PR-7 News Analyst — 新闻分析

v3 简化（对照 TA news_analyst.py 3402 B ≈ 110 行）：
- 不调 langchain + news tools
- 单次 LLM 调用 + 注入基座 fetch_market_data.news
"""

from __future__ import annotations

from . import _base
from ._base import call_llm, fetch_market_data, _parse_rating
from .schemas import NewsReport


SYSTEM_PROMPT = (
    "You are a news analyst. Given recent news headlines for a ticker, "
    "write a 2-4 sentence summary of the key themes and end with "
    "STRONG_BUY / WEAK_BUY / NEUTRAL."
)


def run(ticker: str, trade_date: str) -> NewsReport:
    """新闻分析 — 拉数据 + 调 LLM + 解析 rating"""
    data = fetch_market_data(ticker, trade_date)
    news = data.get("news", [])

    headlines = [n.get("title", "") for n in news if n.get("title")]

    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", (
            f"Ticker: {ticker}\n"
            f"Trade date: {trade_date}\n"
            f"Headlines:\n" + "\n".join(f"- {h}" for h in headlines)
        )),
    ]

    try:
        result = call_llm(messages)
        content = result.content
        rating = _parse_rating(content)
    except Exception as e:
        return NewsReport(
            ticker=ticker, trade_date=trade_date,
            top_headlines=headlines,
            summary=f"新闻分析失败（{type(e).__name__}），已降级为 NEUTRAL",
            rating="NEUTRAL",
        )

    return NewsReport(
        ticker=ticker, trade_date=trade_date,
        top_headlines=headlines,
        summary=content,
        rating=rating,
    )


__all__ = ["run"]
