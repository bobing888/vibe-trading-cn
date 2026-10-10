"""v3.5 PR-14 基座 vibe-trading-ai 接入 测试

设计目标：
- 写 base_adapter 抽象基座接口（fetch_market_data / call_llm / load_history）
- 写 fallback 链：基座 → 真实 vendor → fixture
- 写文档说明如何装 vibe-trading-ai
- 不强依赖基座（基座未装时走 vendor / fixture 兜底）

v3 简化：
- 不强求 pip install vibe-trading-ai（生产环境再装）
- 基座接口靠 duck typing 适配（无需基座实际实现）
- 失败降级：基座异常 → vendor → fixture 三级降级
"""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path
from typing import Any, Optional

import pytest


# ====================================================================
# BaseAdapter 抽象测试
# ====================================================================

class TestBaseAdapter:
    """BaseAdapter 抽象基座接口"""

    def test_base_adapter_is_abstract(self) -> None:
        from src.vibe_trading_cn.base_adapter import BaseAdapter
        with pytest.raises(TypeError):
            BaseAdapter()  # type: ignore[abstract]

    def test_base_adapter_subclass_works(self) -> None:
        from src.vibe_trading_cn.base_adapter import BaseAdapter

        class FakeAdapter(BaseAdapter):
            name = "fake"

            def fetch_market_data(self, ticker, trade_date):
                return {"ticker": ticker, "trade_date": trade_date, "ohlcv": {}}

            def call_llm(self, messages):
                return type("R", (), {"content": "fake"})()

        a = FakeAdapter()
        data = a.fetch_market_data("AAPL", "2026-10-07")
        assert data["ticker"] == "AAPL"
        result = a.call_llm([("system", "x")])
        assert result.content == "fake"

    def test_base_adapter_name_attribute(self) -> None:
        from src.vibe_trading_cn.base_adapter import BaseAdapter

        class NamedAdapter(BaseAdapter):
            name = "vibe-trading-ai-fork"

            def fetch_market_data(self, ticker, trade_date):
                return {}

            def call_llm(self, messages):
                return type("R", (), {"content": ""})()

        a = NamedAdapter()
        assert a.name == "vibe-trading-ai-fork"


# ====================================================================
# VibeTradingAIAdapter 接入测试（基座未装时 mock）
# ====================================================================

class TestVibeTradingAIAdapter:
    """vibe-trading-ai 包接入（mock 包，未真装）"""

    def test_adapter_imports_base_package_dynamically(self, monkeypatch) -> None:
        """动态 import vibe_trading_ai（避免硬依赖）"""
        from src.vibe_trading_cn import base_adapter

        # 模拟基座包：用 types.ModuleType 构造真 module
        fake_pkg = types.ModuleType("vibe_trading_ai")

        def fake_get_market_data(ticker, trade_date):
            return {"ticker": ticker, "trade_date": trade_date, "ohlcv": {"x": 1}, "fundamentals": {}, "news": [], "social": {}}

        class FakeLLM:
            def call(self, messages):
                return type("R", (), {"content": "BASE_OK"})()

        fake_pkg.get_market_data = fake_get_market_data
        fake_pkg.LLM = FakeLLM
        monkeypatch.setitem(sys.modules, "vibe_trading_ai", fake_pkg)

        a = base_adapter.VibeTradingAIAdapter()
        data = a.fetch_market_data("AAPL", "2026-10-07")
        assert data["ticker"] == "AAPL"
        assert "x" in data["ohlcv"]

    def test_adapter_handles_missing_base_package(self) -> None:
        """基座未装 → 抛 ImportError（让 caller 降级）"""
        from src.vibe_trading_cn import base_adapter
        import sys

        # 确保基座不在 sys.modules
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.delitem(sys.modules, "vibe_trading_ai", raising=False)
        try:
            a = base_adapter.VibeTradingAIAdapter()
            with pytest.raises((ImportError, AttributeError)):
                a.fetch_market_data("AAPL", "2026-10-07")
        finally:
            monkeypatch.undo()

    def test_adapter_llm_call(self, monkeypatch) -> None:
        from src.vibe_trading_cn import base_adapter
        import types

        fake_pkg = types.ModuleType("vibe_trading_ai")

        class FakeLLM:
            def call(self, messages):
                return type("R", (), {"content": "BASE_LLM_OK"})()

        fake_pkg.LLM = FakeLLM
        monkeypatch.setitem(sys.modules, "vibe_trading_ai", fake_pkg)

        a = base_adapter.VibeTradingAIAdapter()
        result = a.call_llm([("system", "x")])
        assert result.content == "BASE_LLM_OK"


# ====================================================================
# production_adapter 集成 — 三级降级链
# ====================================================================

class TestProductionAdapterFallback:
    """production_adapter 三级降级：基座 → vendor → fixture"""

    def test_base_priority_over_vendor(self, monkeypatch) -> None:
        from src.vibe_trading_cn import production_adapter, base_adapter

        class FakeBase(base_adapter.BaseAdapter):
            name = "fake-base"
            def fetch_market_data(self, ticker, trade_date):
                return {"ticker": ticker, "ohlcv": {"2026-10-01": {"close": 999}}, "fundamentals": {}, "news": [], "social": {}}
            def call_llm(self, messages):
                return type("R", (), {"content": ""})()

        monkeypatch.setattr(base_adapter, "get_base_adapter", lambda: FakeBase())
        data = production_adapter.fetch_market_data("AAPL", "2026-10-07")
        # 基座优先
        assert data["ohlcv"]["2026-10-01"]["close"] == 999

    def test_vendor_when_base_fails(self, monkeypatch) -> None:
        from src.vibe_trading_cn import production_adapter, base_adapter, data_vendor

        class BrokenBase(base_adapter.BaseAdapter):
            name = "broken-base"
            def fetch_market_data(self, ticker, trade_date):
                raise RuntimeError("base down")
            def call_llm(self, messages):
                return type("R", (), {"content": ""})()

        # vendor 也失败 → fixture 兜底
        class FakeCCXT:
            name = "ccxt:binance"
            def __call__(self, ticker, trade_date):
                raise RuntimeError("vendor down")

        monkeypatch.setattr(base_adapter, "get_base_adapter", lambda: BrokenBase())
        monkeypatch.setattr(data_vendor, "get_vendor", lambda t: FakeCCXT())
        data = production_adapter.fetch_market_data("BTCUSDT", "2026-10-07")
        # fixture 兜底
        assert "2026-10-01" in data["ohlcv"]
