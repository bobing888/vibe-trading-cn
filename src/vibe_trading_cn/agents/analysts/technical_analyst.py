"""v3.5 PR-7 Technical Analyst — 技术面分析

v3 简化（对照 TA market_analyst.py 6610 B ≈ 220 行，TA 第 2 大）：
- 不调 TA 的 8 个 tools（get_stock_data / indicators / patterns）
- 不引入 langchain ChatPromptTemplate + bind_tools
- 单次 LLM 调用 + 注入基座 fetch_market_data.ohlcv
- v3 简化：MACD/趋势/支撑压力 由 LLM 算（基座数据已含 OHLCV）
"""

from __future__ import annotations

from . import _base
from ._base import call_llm, fetch_market_data, _parse_rating
from .schemas import TechnicalReport


SYSTEM_PROMPT = (
    "You are a technical analyst. Given OHLCV data for a ticker over recent days, "
    "compute MACD signal (金叉/死叉/中性), trend (看多/看空/震荡), and write a "
    "2-4 sentence technical report. End with STRONG_BUY / WEAK_BUY / NEUTRAL."
)


def run(ticker: str, trade_date: str) -> TechnicalReport:
    """技术面分析 — 拉数据 + 调 LLM + 解析 rating"""
    data = fetch_market_data(ticker, trade_date)
    ohlcv = data.get("ohlcv", {})

    # OHLCV 转成紧凑字符串
    ohlcv_str = "\n".join(
        f"{d}: O={r.get('open')} H={r.get('high')} L={r.get('low')} C={r.get('close')} V={r.get('volume')}"
        for d, r in sorted(ohlcv.items())
    )

    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", (
            f"Ticker: {ticker}\n"
            f"Trade date: {trade_date}\n"
            f"OHLCV (last {len(ohlcv)} days):\n{ohlcv_str}"
        )),
    ]

    try:
        result = call_llm(messages)
        content = result.content
        rating = _parse_rating(content)
    except Exception as e:
        return TechnicalReport(
            ticker=ticker, trade_date=trade_date,
            summary=f"技术分析失败（{type(e).__name__}），已降级为 NEUTRAL",
            rating="NEUTRAL",
        )

    # 简化：尝试从 content 提取 macd_signal / trend（测试不强依赖）
    macd = ""
    if "金叉" in content:
        macd = "金叉"
    elif "死叉" in content:
        macd = "死叉"
    trend = ""
    if "看多" in content or "上升" in content:
        trend = "看多"
    elif "看空" in content or "下跌" in content:
        trend = "看空"

    return TechnicalReport(
        ticker=ticker, trade_date=trade_date,
        macd_signal=macd,
        trend=trend,
        summary=content,
        rating=rating,
    )


__all__ = ["run"]
