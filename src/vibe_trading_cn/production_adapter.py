"""v3.5 PR-9 + PR-13 production_adapter — 基座适配层

设计目标：
- v3.5 PR-9：提供"生产 stub" — 数据用 fixture 真实样本，LLM 接受 duck typing
- v3.5 PR-13：接真实数据 vendor（ccxt / yfinance），失败降级到 fixture
- LLM：v3.5 PR-12 接入多 provider（OpenAI / Kimi / DeepSeek）

v3 简化：
- fetch_market_data 优先 vendor，失败降级 fixture
- call_llm 默认 stub（生产需注入基座 LLMClient）
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from . import _fixture


logger = logging.getLogger(__name__)

# v3.5 PR-13: vendor init 串行化（asyncio.to_thread 跑线程时避免 ccxt import 死锁）
_vendor_init_lock = threading.Lock()


def _try_base_first(ticker: str, trade_date: str) -> Optional[dict[str, Any]]:
    """v3.5 PR-14：基座 vibe-trading-ai 优先（duck typing）

    Returns: 基座返回的 dict（已含 v3 schema）；基座未装/异常 → None
    """
    try:
        from .base_adapter import get_base_adapter
        a = get_base_adapter()
        return a.fetch_market_data(ticker, trade_date)
    except ImportError:
        # 基座未装（最常见 — 跳过 logging）
        return None
    except Exception as e:
        logger.warning(f"[adapter] base fetch failed: {e}; falling back to vendor")
        return None


def _try_vendor_first(ticker: str, trade_date: str) -> Optional[dict[str, Any]]:
    """v3.5 PR-13：尝试真实 vendor，失败返回 None（让 caller 降级 fixture）

    threading.Lock 只护 import + __init__（避免 ccxt 死锁），
    HTTP 请求在锁外并行（4 analyst 并发不被串行化）
    """
    try:
        # 仅锁 init
        with _vendor_init_lock:
            from .data_vendor import get_vendor
            v = get_vendor(ticker)
        # HTTP 请求在锁外（4 analyst 并行）
        data = v(ticker, trade_date)
        # 校验 schema 完整性
        if not data.get("ohlcv"):
            logger.warning(f"[adapter] vendor {v.name} returned empty ohlcv for {ticker}")
            return None
        return data
    except Exception as e:
        logger.warning(f"[adapter] vendor failed for {ticker}: {e}; falling back to fixture")
        return None


def fetch_market_data(ticker: str, trade_date: str) -> dict[str, Any]:
    """三级降级：基座 → 真实 vendor → fixture

    优先：vibe-trading-ai 基座（如果装好）
    次优：真实 vendor（ccxt / yfinance）
    兜底：_fixture（4 ticker 真实样本；未知 ticker 空 schema）
    """
    base_data = _try_base_first(ticker, trade_date)
    if base_data is not None:
        return base_data
    vendor_data = _try_vendor_first(ticker, trade_date)
    if vendor_data is not None:
        return vendor_data
    return _fixture.get_market_data(ticker, trade_date)


def call_llm(
    messages: list[tuple[str, str]],
    llm_client: Optional[Callable[[list[tuple[str, str]]], Any]] = None,
) -> Any:
    """LLM 调用：v3.5 PR-12 多 provider 接入

    优先：env VIBE_LLM_PROVIDER 配置的 LLMClient
    降级：stub（生产无 key 时返回空）
    """
    if llm_client is None:
        try:
            from .llm_client import get_client, ProviderConfig
            config = ProviderConfig.from_env()
            # v3.5 PR-13 修复：无 API key → stub（不发 HTTP 避免超时）
            if not config.api_key:
                logger.debug("[adapter] no VIBE_LLM_API_KEY; using stub")
                class _Stub:
                    content = ""
                return _Stub()
            client = get_client(config)
            return client(messages)
        except Exception as e:
            logger.warning(f"[adapter] LLM client init failed: {e}; using stub")
            class _Stub:
                content = ""
            return _Stub()
    return llm_client(messages)


__all__ = ["fetch_market_data", "call_llm"]
