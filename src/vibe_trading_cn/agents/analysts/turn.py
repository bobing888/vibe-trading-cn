"""v3.5 PR-7 turn.py — 4 analyst 并行调度

v3 简化（对照 TA turn.py 2017 B ≈ 70 行，TA 是"tool 调用循环"）：
- v3 不调 tool（基座数据已预加载）→ 单次 asyncio.gather
- 4 个 analyst 并行执行（asyncio.to_thread 包装同步函数）
- 任一失败 → 该 analyst 降级为 NEUTRAL，其它不受影响
- 不引入 langgraph
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from . import fundamentals_analyst, news_analyst, sentiment_analyst, technical_analyst
from .schemas import (
    FundamentalsReport, MarketContext, NewsReport,
    SentimentReport, TechnicalReport,
)


T = TypeVar("T")


async def _gather_with_fallback(
    *coros: Callable[[], Awaitable[T]],
) -> list[T | None]:
    """并行执行多个 coros，任一失败返回 None，不影响其它

    简化：v3 不复杂错误处理 — 失败降级即可
    """
    tasks = [asyncio.create_task(c()) for c in coros]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for r in results:
        if isinstance(r, Exception):
            out.append(None)
        else:
            out.append(r)
    return out


async def run_all(ticker: str, trade_date: str) -> MarketContext:
    """并行调 4 analyst → 汇总到 MarketContext

    部分失败：返回的 report 字段为 None（research_manager 负责降级）
    """
    fund, sent, news, tech = await _gather_with_fallback(
        lambda: asyncio.to_thread(fundamentals_analyst.run, ticker, trade_date),
        lambda: asyncio.to_thread(sentiment_analyst.run, ticker, trade_date),
        lambda: asyncio.to_thread(news_analyst.run, ticker, trade_date),
        lambda: asyncio.to_thread(technical_analyst.run, ticker, trade_date),
    )

    # 失败转 NEUTRAL
    fund = fund or FundamentalsReport(
        ticker=ticker, trade_date=trade_date, rating="NEUTRAL",
        summary="fundamentals 调度失败，已降级"
    )
    sent = sent or SentimentReport(
        ticker=ticker, trade_date=trade_date, rating="NEUTRAL",
        summary="sentiment 调度失败，已降级"
    )
    news = news or NewsReport(
        ticker=ticker, trade_date=trade_date, rating="NEUTRAL",
        summary="news 调度失败，已降级"
    )
    tech = tech or TechnicalReport(
        ticker=ticker, trade_date=trade_date, rating="NEUTRAL",
        summary="technical 调度失败，已降级"
    )

    return MarketContext(
        ticker=ticker, trade_date=trade_date,
        fundamentals=fund, sentiment=sent, news=news, technical=tech,
    )


__all__ = ["run_all", "_gather_with_fallback"]
