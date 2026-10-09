"""v3.5 PR-9 cli_analyze — 端到端：ticker + date → analyst + risk + decision_log

设计：
- 串联 PR-7 (4 analyst) + research_manager + PR-8 (3 debator) + PR-6a (decision_log)
- 注入 production_adapter（真实样本数据 / duck typing LLM）
- 测试时用 monkeypatch 替换各模块的 call_llm / fetch_market_data

用法：
    python -m src.vibe_trading_cn.cli_analyze --ticker BTCUSDT --date 2026-10-07
    python -m src.vibe_trading_cn.cli_analyze --ticker BTCUSDT --date 2026-10-07 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .decision_log import DecisionLog
from .production_adapter import call_llm as prod_call_llm, fetch_market_data as prod_fetch_market_data


def _wire_production_adapters() -> None:
    """把 production_adapter 接入 PR-7/8 注入点

    不替换测试时已 monkeypatch 的接口（monkeypatch 在 production_adapter 之后运行）
    """
    from .agents.analysts import _base as analysts_base
    from .agents.risk_mgmt import _base as risk_base
    from .agents.analysts.schemas import (
        FundamentalsReport, MarketContext, NewsReport,
        SentimentReport, TechnicalReport,
    )
    from .agents.analysts import (
        fundamentals_analyst, news_analyst, sentiment_analyst, technical_analyst, turn,
    )
    from .agents.managers import research_manager
    from .agents.risk_mgmt import (
        aggressive_debator, conservative_debator, neutral_debator,
    )
    from .agents.risk_mgmt import turn as risk_turn
    from .agents.risk_mgmt.schemas import RiskVerdict

    # 注入：fetch_market_data / call_llm
    analysts_base.fetch_market_data = prod_fetch_market_data
    analysts_base.call_llm = prod_call_llm
    risk_base.call_llm = prod_call_llm
    # 注：analyst 模块顶层 from ._base import 的引用也需更新
    for m in (fundamentals_analyst, news_analyst, sentiment_analyst, technical_analyst):
        m.fetch_market_data = prod_fetch_market_data
        m.call_llm = prod_call_llm
    research_manager.call_llm = prod_call_llm
    for m in (aggressive_debator, conservative_debator, neutral_debator):
        m.call_llm = prod_call_llm


def analyze(ticker: str, trade_date: str) -> tuple[MarketContext, str, RiskVerdict]:
    """跑 PR-7 4 analyst 并行 → research_manager → PR-8 3 debator

    Returns:
        (MarketContext, investment_plan, RiskVerdict)
    """
    from .agents.analysts.turn import run_all as analysts_run_all
    from .agents.managers.research_manager import summarize
    from .agents.risk_mgmt.turn import run_all as risk_run_all

    # 1. PR-7: 4 analyst 并行
    ctx = asyncio.run(analysts_run_all(ticker, trade_date))
    # 2. research_manager 汇总 → investment_plan
    investment_plan = summarize(ctx, llm_client=prod_call_llm)
    # 3. PR-8: 3 debator 串行
    verdict = risk_run_all(investment_plan)
    return ctx, investment_plan, verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v3.5 end-to-end analyze")
    parser.add_argument("--ticker", required=True, help="Ticker symbol (e.g. BTCUSDT)")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Don't write decision_log")
    args = parser.parse_args(argv)

    _wire_production_adapters()
    ctx, raw_plan, verdict = analyze(args.ticker, args.date)

    # 包装：确保始终含 INVESTMENT_PLAN 标志
    investment_plan = raw_plan or "INVESTMENT_PLAN: (LLM 未生成，pending 人工 review)"
    risk_summary = (
        f"RISK_VERDICT: {verdict.final_risk_rating} | "
        f"position={verdict.final_position_size:.0%} | "
        f"stop_loss={verdict.final_stop_loss:.0%} | "
        f"target={verdict.final_target_upside:.0%}"
    )
    full_decision = f"{investment_plan}\n\n{risk_summary}"

    print(f"[analyze] {args.ticker} @ {args.date}")
    print(f"  fundamentals.rating: {ctx.fundamentals.rating if ctx.fundamentals else 'N/A'}")
    print(f"  sentiment.rating:    {ctx.sentiment.rating if ctx.sentiment else 'N/A'}")
    print(f"  news.rating:         {ctx.news.rating if ctx.news else 'N/A'}")
    print(f"  technical.rating:    {ctx.technical.rating if ctx.technical else 'N/A'}")
    print(f"  risk_verdict:        {verdict.final_risk_rating}")
    print(f"  vote:                {verdict.vote_summary}")

    if args.dry_run:
        print("[analyze] dry-run: decision NOT written to log")
        return 0

    log = DecisionLog()
    appended = log.append(
        ticker=args.ticker, trade_date=args.date,
        decision=full_decision, rating=ctx.fundamentals.rating if ctx.fundamentals else "NEUTRAL",
    )
    print(f"[analyze] appended to decision_log: {appended}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
