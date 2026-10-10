"""v3.5 PR-14 base_adapter — 基座 vibe-trading-ai 接入层

设计：
- BaseAdapter 抽象：fetch_market_data + call_llm
- VibeTradingAIAdapter：动态 import vibe_trading_ai（不强依赖）
- 失败降级：基座未装 / 异常 → caller 走 vendor → fixture

v3 简化：
- 不强求 pip install vibe-trading-ai（生产环境再装）
- 基座接口靠 duck typing 适配（无需基座实际实现）
- 动态 import（运行时检查 sys.modules + importlib）
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

    基座装好后，本类会调到：
    - vibe_trading_ai.get_market_data(ticker, trade_date) → dict
    - vibe_trading_ai.LLM().call(messages) → obj.content
    """

    name = "vibe-trading-ai"

    def fetch_market_data(self, ticker: str, trade_date: str) -> dict[str, Any]:
        pkg = self._import_base()
        return pkg.get_market_data(ticker, trade_date)

    def call_llm(self, messages: list[tuple[str, str]]) -> Any:
        pkg = self._import_base()
        client = pkg.LLM()
        return client.call(messages)

    def _import_base(self):
        """动态 import vibe_trading_ai（基座未装 → ImportError）"""
        try:
            return importlib.import_module("vibe_trading_ai")
        except ImportError as e:
            logger.info(f"[base_adapter] vibe_trading_ai not installed: {e}")
            raise


def get_base_adapter() -> Optional[BaseAdapter]:
    """Factory: 返回 VibeTradingAIAdapter 实例

    不做"基座是否装"的预检查 —— 构造便宜（不 import），实际调用时再 fail
    """
    return VibeTradingAIAdapter()


__all__ = ["BaseAdapter", "VibeTradingAIAdapter", "get_base_adapter"]
