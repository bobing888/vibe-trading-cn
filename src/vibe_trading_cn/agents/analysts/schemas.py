"""v3.5 PR-7 Analyst 4 报告 Pydantic v2 schemas

设计要点：
- 4 类报告各自独立 Pydantic 模型（fundamentals / sentiment / news / technical）
- MarketContext 聚合 4 报告
- rating 统一：STRONG / WEAK / NEUTRAL（与 PR-6a DecisionEntry.rating 一致）
- 简化（对照 TA）：不引入 langchain BaseModel；用 Pydantic v2
- 不依赖 tradingagents.*（v3 零 TA 依赖）
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


_RATING = Field(default="NEUTRAL", pattern=r"^(STRONG|WEAK|NEUTRAL)$")


class FundamentalsReport(BaseModel):
    """基本面分析报告"""

    ticker: str
    trade_date: str
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    roe: Optional[float] = None
    revenue_yoy: Optional[float] = None
    summary: str = ""
    rating: str = "NEUTRAL"


class SentimentReport(BaseModel):
    """社交情绪报告"""

    ticker: str
    trade_date: str
    reddit_sentiment: Optional[float] = None  # -1 ~ +1
    stocktwits_bull_ratio: Optional[float] = None
    summary: str = ""
    rating: str = "NEUTRAL"


class NewsReport(BaseModel):
    """新闻分析报告"""

    ticker: str
    trade_date: str
    top_headlines: list[str] = Field(default_factory=list)
    summary: str = ""
    rating: str = "NEUTRAL"


class TechnicalReport(BaseModel):
    """技术分析报告"""

    ticker: str
    trade_date: str
    macd_signal: str = ""  # 金叉 / 死叉 / 中性
    trend: str = ""  # 看多 / 看空 / 震荡
    summary: str = ""
    rating: str = "NEUTRAL"


class MarketContext(BaseModel):
    """4 报告聚合（PR-7 端到端输出）"""

    ticker: str
    trade_date: str
    fundamentals: Optional[FundamentalsReport] = None
    sentiment: Optional[SentimentReport] = None
    news: Optional[NewsReport] = None
    technical: Optional[TechnicalReport] = None


__all__ = [
    "FundamentalsReport",
    "SentimentReport",
    "NewsReport",
    "TechnicalReport",
    "MarketContext",
]
