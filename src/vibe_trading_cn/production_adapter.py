"""v3.5 PR-9 production_adapter — 基座适配层

设计目标：
- 提供"生产 stub" — 数据用 fixture 真实样本结构，LLM 接受 duck typing
- 不接 vibe-trading-ai（基座包未装，留到 v3.6+）
- 不接 LLM API（避免外部依赖，留到 v3.6+）
- 测试时仍 monkeypatch 注入

v3 简化：
- fetch_market_data 返回标准 v3 schema（fundamentals / news / social / ohlcv）
- call_llm 默认 stub（生产需注入基座 LLMClient）
- 数据来自 _fixture.py（按 ticker 查表；fallback 通用 stub）
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import _fixture


def fetch_market_data(ticker: str, trade_date: str) -> dict[str, Any]:
    """生产 stub：按 ticker 查表返回真实样本结构

    优先：_fixture.KNOWN_TICKERS（btc / eth / aapl / 600519 等）
    降级：通用空 schema
    """
    return _fixture.get_market_data(ticker, trade_date)


def call_llm(
    messages: list[tuple[str, str]],
    llm_client: Optional[Callable[[list[tuple[str, str]]], Any]] = None,
) -> Any:
    """生产 stub：默认返回空内容；接受 duck typing llm_client

    生产用法：基座 LLMClient 类只要实现 __call__(messages) → obj.content
    即可注入。基座包未装时返回 stub。
    """
    if llm_client is None:
        class _Stub:
            content = ""
        return _Stub()
    return llm_client(messages)


__all__ = ["fetch_market_data", "call_llm"]
