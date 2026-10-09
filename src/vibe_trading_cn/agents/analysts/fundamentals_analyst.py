"""v3.5 PR-7 Fundamentals Analyst — 基本面分析

v3 简化（对照 TA fundamentals_analyst.py 3266 B ≈ 110 行）：
- 不调 TA 的 5 个 tools（get_fundamentals / balance_sheet / cashflow / income / insider）
- 不引入 langchain ChatPromptTemplate + bind_tools
- 单次 LLM 调用 + 注入基座 fetch_market_data.fundamentals
"""

from __future__ import annotations

from . import _base
from ._base import call_llm, fetch_market_data, _parse_rating
from .schemas import FundamentalsReport


SYSTEM_PROMPT = (
    "You are a fundamental analyst reviewing company financials. "
    "Given fundamentals (PE, PB, ROE, revenue growth) for a company, "
    "write a concise fundamental report (2-4 sentences) and end with "
    "a recommendation: STRONG_BUY / WEAK_BUY / NEUTRAL."
)


def run(ticker: str, trade_date: str) -> FundamentalsReport:
    """基本面分析 — 拉数据 + 调 LLM + 解析 rating

    失败降级：LLM 抛错 → 返回 NEUTRAL 报告，不抛异常
    """
    data = fetch_market_data(ticker, trade_date)
    fundamentals = data.get("fundamentals", {})

    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", (
            f"Ticker: {ticker}\n"
            f"Trade date: {trade_date}\n"
            f"PE: {fundamentals.get('pe', 'n/a')}\n"
            f"PB: {fundamentals.get('pb', 'n/a')}\n"
            f"ROE: {fundamentals.get('roe', 'n/a')}\n"
            f"Revenue YoY: {fundamentals.get('revenue_yoy', 'n/a')}"
        )),
    ]

    try:
        result = call_llm(messages)
        content = result.content
        rating = _parse_rating(content)
    except Exception as e:
        # 降级：返回 NEUTRAL 报告
        return FundamentalsReport(
            ticker=ticker, trade_date=trade_date,
            pe_ratio=fundamentals.get("pe"),
            pb_ratio=fundamentals.get("pb"),
            roe=fundamentals.get("roe"),
            revenue_yoy=fundamentals.get("revenue_yoy"),
            summary=f"基本面分析失败（{type(e).__name__}），已降级为 NEUTRAL",
            rating="NEUTRAL",
        )

    return FundamentalsReport(
        ticker=ticker, trade_date=trade_date,
        pe_ratio=fundamentals.get("pe"),
        pb_ratio=fundamentals.get("pb"),
        roe=fundamentals.get("roe"),
        revenue_yoy=fundamentals.get("revenue_yoy"),
        summary=content,
        rating=rating,
    )


__all__ = ["run"]
