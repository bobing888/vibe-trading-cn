"""v3.5 PR-7 Analyst 4 个 + research_manager 汇总 测试（红绿重构循环）

设计目标：
- 4 个 analyst 各司其职：fundamentals / sentiment / news / technical
- 并行调度（asyncio.gather）— 不引入 TA turn.py 多轮 tool
- research_manager 汇总 4 报告 → investment_plan
- 数据源：基座 fetch_market_data（当前 mock 注入；后续 PR 接 loader）

v3 简化（对照 TA）：
- 不调 langchain tool calling → 单次 LLM 调用 + 注入数据
- 不强制 4 报告必齐全（部分降级）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


# ====================================================================
# 测试用 mock 数据 / mock LLM
# ====================================================================

def _mock_market_data(ticker: str, trade_date: str = "") -> dict:
    """mock 基座 fetch_market_data 返回结构"""
    return {
        "ticker": ticker,
        "ohlcv": {
            "2026-10-01": {"open": 100, "high": 105, "low": 99, "close": 103, "volume": 1000},
            "2026-10-02": {"open": 103, "high": 108, "low": 102, "close": 107, "volume": 1200},
        },
        "fundamentals": {"pe": 15.2, "pb": 1.8, "roe": 0.18, "revenue_yoy": 0.12},
        "news": [{"title": "Q3 业绩超预期", "sentiment": "positive", "date": "2026-10-02"}],
        "social": {"reddit_sentiment": 0.65, "stocktwits_bull_ratio": 0.72},
    }


def _mock_llm(messages: list[tuple[str, str]]) -> object:
    """mock 基座 LLMClient（duck typing：call(messages) → obj.content）"""
    system = messages[0][1] if messages else ""
    s = system.lower()
    # 简化：根据 system 关键词判断返回哪类报告
    if "research manager" in s or "summariz" in s or "汇总" in s:
        content = "INVESTMENT_PLAN: 综合 4 报告看多，建议 STRONG_BUY，目标价 +15%"
    elif "fundamental" in s and "analyst" in s:
        content = "FUNDAMENTAL_REPORT: PE 15.2 偏低，ROE 18% 优秀，建议 STRONG"
    elif "sentiment" in s and "analyst" in s:
        content = "SENTIMENT_REPORT: 社交媒体情绪看多 65%，无明显反转"
    elif "news" in s and "analyst" in s:
        content = "NEWS_REPORT: Q3 业绩超预期，正面新闻主导"
    elif "technical" in s and "analyst" in s:
        content = "TECHNICAL_REPORT: 突破 20 日均线，MACD 金叉，趋势看多"
    else:
        content = "GENERIC_REPORT"
    return type("R", (), {"content": content})()


# ====================================================================
# Schema 测试
# ====================================================================

class TestAnalystSchemas:
    """4 类报告 Pydantic v2 模型"""

    def test_fundamentals_report_required_fields(self) -> None:
        from src.vibe_trading_cn.agents.analysts.schemas import FundamentalsReport
        r = FundamentalsReport(
            ticker="BTCUSDT", trade_date="2026-10-07",
            pe_ratio=15.2, roe=0.18, summary="低估", rating="STRONG"
        )
        assert r.ticker == "BTCUSDT"
        assert r.pe_ratio == 15.2

    def test_sentiment_report_required_fields(self) -> None:
        from src.vibe_trading_cn.agents.analysts.schemas import SentimentReport
        r = SentimentReport(
            ticker="ETHUSDT", trade_date="2026-10-07",
            reddit_sentiment=0.65, summary="看多", rating="WEAK"
        )
        assert r.reddit_sentiment == 0.65

    def test_news_report_required_fields(self) -> None:
        from src.vibe_trading_cn.agents.analysts.schemas import NewsReport
        r = NewsReport(
            ticker="AAPL", trade_date="2026-10-07",
            top_headlines=["Q3 超预期"], summary="正面", rating="STRONG"
        )
        assert "Q3 超预期" in r.top_headlines

    def test_technical_report_required_fields(self) -> None:
        from src.vibe_trading_cn.agents.analysts.schemas import TechnicalReport
        r = TechnicalReport(
            ticker="BTCUSDT", trade_date="2026-10-07",
            macd_signal="金叉", trend="看多", summary="突破", rating="STRONG"
        )
        assert r.macd_signal == "金叉"

    def test_market_context_aggregates_4_reports(self) -> None:
        from src.vibe_trading_cn.agents.analysts.schemas import (
            FundamentalsReport, MarketContext, NewsReport,
            SentimentReport, TechnicalReport,
        )
        ctx = MarketContext(
            ticker="BTCUSDT", trade_date="2026-10-07",
            fundamentals=FundamentalsReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                pe_ratio=15.2, roe=0.18, summary="低估", rating="STRONG",
            ),
            sentiment=SentimentReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                reddit_sentiment=0.65, summary="看多", rating="WEAK",
            ),
            news=NewsReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                top_headlines=["Q3 超预期"], summary="正面", rating="STRONG",
            ),
            technical=TechnicalReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                macd_signal="金叉", trend="看多", summary="突破", rating="STRONG",
            ),
        )
        assert ctx.fundamentals.rating == "STRONG"
        assert ctx.technical.trend == "看多"


# ====================================================================
# Analyst 单元测试
# ====================================================================

class TestAnalysts:
    """4 个 analyst 各自行为"""

    def test_fundamentals_analyst_returns_report(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.analysts import fundamentals_analyst
        monkeypatch.setattr(fundamentals_analyst, "fetch_market_data", _mock_market_data)
        monkeypatch.setattr(fundamentals_analyst, "call_llm", _mock_llm)
        r = fundamentals_analyst.run(
            ticker="BTCUSDT", trade_date="2026-10-07"
        )
        assert r.rating == "STRONG"
        assert "FUNDAMENTAL" in r.summary

    def test_sentiment_analyst_returns_report(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.analysts import sentiment_analyst
        monkeypatch.setattr(sentiment_analyst, "fetch_market_data", _mock_market_data)
        monkeypatch.setattr(sentiment_analyst, "call_llm", _mock_llm)
        r = sentiment_analyst.run(
            ticker="BTCUSDT", trade_date="2026-10-07"
        )
        assert "SENTIMENT" in r.summary

    def test_news_analyst_returns_report(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.analysts import news_analyst
        monkeypatch.setattr(news_analyst, "fetch_market_data", _mock_market_data)
        monkeypatch.setattr(news_analyst, "call_llm", _mock_llm)
        r = news_analyst.run(
            ticker="BTCUSDT", trade_date="2026-10-07"
        )
        assert r.top_headlines  # 非空

    def test_technical_analyst_returns_report(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.analysts import technical_analyst
        monkeypatch.setattr(technical_analyst, "fetch_market_data", _mock_market_data)
        monkeypatch.setattr(technical_analyst, "call_llm", _mock_llm)
        r = technical_analyst.run(
            ticker="BTCUSDT", trade_date="2026-10-07"
        )
        assert r.macd_signal  # 非空

    def test_analyst_handles_llm_failure_gracefully(self, monkeypatch) -> None:
        """LLM 抛错 → 返回 NEUTRAL 降级报告，不抛异常"""
        from src.vibe_trading_cn.agents.analysts import fundamentals_analyst
        monkeypatch.setattr(fundamentals_analyst, "fetch_market_data", _mock_market_data)
        def boom(*a, **k):
            raise RuntimeError("LLM 限流")
        monkeypatch.setattr(fundamentals_analyst, "call_llm", boom)
        r = fundamentals_analyst.run(
            ticker="BTCUSDT", trade_date="2026-10-07"
        )
        assert r.rating == "NEUTRAL"
        assert "失败" in r.summary or "降级" in r.summary or "fallback" in r.summary.lower()


# ====================================================================
# 并行调度测试
# ====================================================================

class TestParallelTurn:
    """turn.py: asyncio.gather 并行调 4 analyst"""

    def test_run_all_parallel_returns_market_context(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.analysts import (
            fundamentals_analyst, news_analyst, sentiment_analyst, technical_analyst, turn,
        )
        for m in [fundamentals_analyst, sentiment_analyst, news_analyst, technical_analyst]:
            monkeypatch.setattr(m, "fetch_market_data", _mock_market_data)
            monkeypatch.setattr(m, "call_llm", _mock_llm)
        ctx = asyncio.run(turn.run_all(
            ticker="BTCUSDT", trade_date="2026-10-07"
        ))
        assert ctx.fundamentals is not None
        assert ctx.sentiment is not None
        assert ctx.news is not None
        assert ctx.technical is not None
        assert ctx.fundamentals.rating == "STRONG"

    def test_run_all_partial_failure_returns_available(self, monkeypatch) -> None:
        """1 个 analyst 失败 → 其它 3 个仍返回，部分降级"""
        from src.vibe_trading_cn.agents.analysts import (
            fundamentals_analyst, news_analyst, sentiment_analyst, technical_analyst, turn,
        )
        for m in [fundamentals_analyst, sentiment_analyst, news_analyst, technical_analyst]:
            monkeypatch.setattr(m, "fetch_market_data", _mock_market_data)
        # news 失败
        monkeypatch.setattr(news_analyst, "call_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        for m in [fundamentals_analyst, sentiment_analyst, technical_analyst]:
            monkeypatch.setattr(m, "call_llm", _mock_llm)
        ctx = asyncio.run(turn.run_all(
            ticker="BTCUSDT", trade_date="2026-10-07"
        ))
        assert ctx.fundamentals is not None
        assert ctx.news is not None
        assert ctx.news.rating == "NEUTRAL"  # 降级


# ====================================================================
# research_manager 汇总测试
# ====================================================================

class TestResearchManager:
    """汇总 4 报告 → investment_plan"""

    def test_summarize_returns_investment_plan(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.analysts.schemas import (
            FundamentalsReport, MarketContext, NewsReport,
            SentimentReport, TechnicalReport,
        )
        from src.vibe_trading_cn.agents.managers import research_manager
        monkeypatch.setattr(research_manager, "call_llm", _mock_llm)
        ctx = MarketContext(
            ticker="BTCUSDT", trade_date="2026-10-07",
            fundamentals=FundamentalsReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                pe_ratio=15.2, roe=0.18, summary="低估", rating="STRONG",
            ),
            sentiment=SentimentReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                reddit_sentiment=0.65, summary="看多", rating="WEAK",
            ),
            news=NewsReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                top_headlines=["Q3 超预期"], summary="正面", rating="STRONG",
            ),
            technical=TechnicalReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                macd_signal="金叉", trend="看多", summary="突破", rating="STRONG",
            ),
        )
        plan = research_manager.summarize(ctx, llm_client=_mock_llm)
        assert "INVESTMENT_PLAN" in plan
        assert "STRONG" in plan

    def test_summarize_handles_missing_reports(self, monkeypatch) -> None:
        """部分 report 缺失 → 仍能 summarize（不抛）"""
        from src.vibe_trading_cn.agents.analysts.schemas import (
            FundamentalsReport, MarketContext, TechnicalReport,
        )
        from src.vibe_trading_cn.agents.managers import research_manager
        ctx = MarketContext(
            ticker="BTCUSDT", trade_date="2026-10-07",
            fundamentals=FundamentalsReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                pe_ratio=15.2, roe=0.18, summary="低估", rating="STRONG",
            ),
            technical=TechnicalReport(
                ticker="BTCUSDT", trade_date="2026-10-07",
                macd_signal="金叉", trend="看多", summary="突破", rating="STRONG",
            ),
        )
        plan = research_manager.summarize(ctx, llm_client=_mock_llm)
        assert "INVESTMENT_PLAN" in plan


# ====================================================================
# 端到端：PR-6b 决策记忆注入 + PR-7 analyst
# ====================================================================

class TestEndToEnd:
    """turn.run_all → research_manager.summarize → PR-6b decision_log.append"""

    def test_full_pipeline(self, monkeypatch, tmp_path: Path) -> None:
        from src.vibe_trading_cn.agents.analysts import (
            fundamentals_analyst, news_analyst, sentiment_analyst, technical_analyst, turn,
        )
        from src.vibe_trading_cn.agents.managers import research_manager
        from src.vibe_trading_cn.decision_log import DecisionLog

        log_path = tmp_path / "decision_log.jsonl"
        monkeypatch.setenv("VIBE_DECISION_LOG_PATH", str(log_path))

        for m in [fundamentals_analyst, sentiment_analyst, news_analyst, technical_analyst]:
            monkeypatch.setattr(m, "fetch_market_data", _mock_market_data)
            monkeypatch.setattr(m, "call_llm", _mock_llm)
        monkeypatch.setattr(research_manager, "call_llm", _mock_llm)

        ctx = asyncio.run(turn.run_all(
            ticker="BTCUSDT", trade_date="2026-10-07"
        ))
        plan = research_manager.summarize(ctx, llm_client=_mock_llm)

        log = DecisionLog()
        appended = log.append(
            ticker="BTCUSDT", trade_date="2026-10-07",
            decision=plan, rating="STRONG",
        )
        assert appended is True
        entries = log.load_entries()
        assert len(entries) == 1
        assert "INVESTMENT_PLAN" in entries[0]["decision"]
