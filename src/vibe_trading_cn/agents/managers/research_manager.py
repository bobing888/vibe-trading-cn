"""v3.5 PR-7 research_manager — 汇总 4 analyst 报告 → investment_plan

v3 简化（对照 TA research_manager.py 74 行）：
- 不调 langchain + human message 循环
- 单次 LLM 调用 + 注入 4 报告文本
- 部分报告缺失（None）也能 summarize
- 不引入 tradingagents.* 库
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..analysts import _base
from ..analysts._base import call_llm
from ..analysts.schemas import MarketContext


SYSTEM_PROMPT = (
    "You are a research manager synthesizing 4 analyst reports "
    "(fundamentals, sentiment, news, technical) into an investment plan. "
    "Write a concise investment_plan (3-5 sentences) and end with a clear "
    "recommendation: STRONG_BUY / WEAK_BUY / NEUTRAL with target upside %."
)


def _format_context(ctx: MarketContext) -> str:
    """4 报告 → 文本（缺则标 N/A）"""
    parts = [f"Ticker: {ctx.ticker}\nTrade date: {ctx.trade_date}\n"]

    if ctx.fundamentals:
        f = ctx.fundamentals
        parts.append(
            f"--- Fundamentals (rating: {f.rating}) ---\n"
            f"PE: {f.pe_ratio}\nPB: {f.pb_ratio}\nROE: {f.roe}\n"
            f"Revenue YoY: {f.revenue_yoy}\n{f.summary}\n"
        )
    else:
        parts.append("--- Fundamentals: N/A ---\n")

    if ctx.sentiment:
        s = ctx.sentiment
        parts.append(
            f"--- Sentiment (rating: {s.rating}) ---\n"
            f"Reddit: {s.reddit_sentiment}\n"
            f"StockTwits bull ratio: {s.stocktwits_bull_ratio}\n{s.summary}\n"
        )
    else:
        parts.append("--- Sentiment: N/A ---\n")

    if ctx.news:
        n = ctx.news
        parts.append(
            f"--- News (rating: {n.rating}) ---\n"
            f"Headlines: {n.top_headlines}\n{n.summary}\n"
        )
    else:
        parts.append("--- News: N/A ---\n")

    if ctx.technical:
        t = ctx.technical
        parts.append(
            f"--- Technical (rating: {t.rating}) ---\n"
            f"MACD: {t.macd_signal}\nTrend: {t.trend}\n{t.summary}\n"
        )
    else:
        parts.append("--- Technical: N/A ---\n")

    return "\n".join(parts)


def summarize(
    ctx: MarketContext,
    llm_client: Optional[Callable[[list[tuple[str, str]]], Any]] = None,
) -> str:
    """汇总 4 报告 → investment_plan 文本

    Args:
        ctx: MarketContext（部分字段可为 None）
        llm_client: duck typing — call(messages) → obj.content；默认 stub

    Returns:
        investment_plan 字符串（含 STRONG_BUY / WEAK_BUY / NEUTRAL 评级）
    """
    if llm_client is None:
        llm_client = call_llm

    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", _format_context(ctx)),
    ]

    try:
        result = llm_client(messages)
        return result.content
    except Exception as e:
        # 降级：用 4 报告 rating 投票
        ratings = [
            r.rating for r in [ctx.fundamentals, ctx.sentiment, ctx.news, ctx.technical] if r
        ]
        if ratings.count("STRONG") >= 2:
            verdict = "STRONG_BUY"
        elif ratings.count("WEAK") >= 2:
            verdict = "WEAK_BUY"
        else:
            verdict = "NEUTRAL"
        return (
            f"INVESTMENT_PLAN: research_manager LLM 失败（{type(e).__name__}），"
            f"按 4 报告 rating 投票降级为 {verdict}"
        )


__all__ = ["summarize", "SYSTEM_PROMPT"]
