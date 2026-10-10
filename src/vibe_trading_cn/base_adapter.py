"""v3.5 PR-14 + v3.7 修复 base_adapter — 基座 vibe-trading-ai 接入层

设计：
- BaseAdapter 抽象：fetch_market_data + call_llm
- VibeTradingAIAdapter：动态 import 基座（不强依赖）
- 失败降级：基座未装 / 异常 → caller 走 vendor → fixture

v3 简化：
- 不强求 pip install vibe-trading-ai（生产环境再装）
- 基座接口靠 duck typing 适配（无需基座实际实现）
- 动态 import（运行时检查 sys.modules + importlib）

v3.7 修复（生产接入）：
- PyPI 包 vibe-trading-ai==0.1.16 实际暴露的模块是 `src.market_data` / `src.memory` /
  `src.agent` 等，不是 `vibe_trading_ai`（top_level.txt 不含 `vibe_trading_ai`）
- VibeTradingAIAdapter._import_base 改为 import `src.market_data.fetch_market_data`；
  基座未装仍走 ImportError 降级链
- v3.4.3.3 subagent 复审 [d134e77a](d134e77a-91d6-448d-b750-fd1d7749eba2) 揭示
  "本地 fork vibe-trading-cn ≠ 上游 HKUDS/Vibe-Trading"，本修复是同一误判
  的延续——之前的 `vibe_trading_ai` 名字是 v3.4 早期凭印象取的。
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """基座 adapter 抽象类

    v3.5 PR-14：基座 vibe-trading-ai 通过本接口适配
    - fetch_market_data(ticker, trade_date) → v3 schema dict
    - call_llm(messages) → obj.content
    """

    name: str = "base"

    @abstractmethod
    def fetch_market_data(self, ticker: str, trade_date: str) -> dict[str, Any]:
        """基座数据获取接口"""
        raise NotImplementedError

    @abstractmethod
    def call_llm(self, messages: list[tuple[str, str]]) -> Any:
        """基座 LLM 接口"""
        raise NotImplementedError


class VibeTradingAIAdapter(BaseAdapter):
    """vibe-trading-ai 基座 adapter

    动态 import：基座未装时 fetch_market_data / call_llm 抛 ImportError
    由 caller（production_adapter）走降级链：基座 → vendor → fixture

    v3.7 修复：基座实际入口是 `src.market_data`（PyPI vibe-trading-ai==0.1.16
    通过 `top_level.txt` 暴露），不是 `vibe_trading_ai`。
    """

    name = "vibe-trading-ai"

    def fetch_market_data(self, ticker: str, trade_date: str) -> dict[str, Any]:
        pkg = self._import_base()
        # 基座 src.market_data.fetch_market_data(ticker, trade_date) → dict
        fetch = getattr(pkg, "fetch_market_data", None)
        if fetch is None:
            raise ImportError(
                f"{pkg.__name__} has no fetch_market_data; "
                f"available: {[n for n in dir(pkg) if not n.startswith('_')][:10]}"
            )
        return fetch(ticker, trade_date)

    def call_llm(self, messages: list[tuple[str, str]]) -> Any:
        # v3.7：基座 src.agent 可能含 LLM 入口；当前 production_adapter 默认走 llm_client，
        # 本方法保留扩展点。如基座暴露 src.agent.LLM，则：
        try:
            agent = importlib.import_module("src.agent")
            llm_cls = getattr(agent, "LLM", None)
            if llm_cls is not None:
                return llm_cls()(messages)
        except ImportError:
            pass
        # 兜底：返回空 stub（production_adapter 会再用 llm_client 兜底）
        class _Stub:
            content = ""
        return _Stub()

    def _import_base(self):
        """动态 import 基座 src.market_data（基座未装 → ImportError）

        v3.7 之前尝试 `vibe_trading_ai`，但 PyPI vibe-trading-ai==0.1.16 实际
        暴露的顶级模块是 src/ backtest/ cli/ evals/ api_server/ mcp_server/，
        没有 `vibe_trading_ai`（v3.4.3.3 复审揭示）。
        """
        try:
            return importlib.import_module("src.market_data")
        except ImportError as e:
            logger.info(f"[base_adapter] src.market_data not installed: {e}")
            raise


def get_base_adapter() -> Optional[BaseAdapter]:
    """Factory: 返回 VibeTradingAIAdapter 实例

    不做"基座是否装"的预检查 —— 构造便宜（不 import），实际调用时再 fail
    """
    return VibeTradingAIAdapter()


__all__ = ["BaseAdapter", "VibeTradingAIAdapter", "get_base_adapter"]
