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
        """动态 import 基座入口（v3.7 修复：基座实际是 src.market_data）"""
        from src.vibe_trading_cn import base_adapter

        # v3.7 修复：基座实际入口是 src.market_data
        # 同时保留对 vibe_trading_ai 的向后兼容（如果有老基座包）
        fake_pkg = types.ModuleType("src.market_data")

        def fake_fetch_market_data(ticker, trade_date):
            return {"ticker": ticker, "trade_date": trade_date, "ohlcv": {"x": 1}, "fundamentals": {}, "news": [], "social": {}}

        fake_pkg.fetch_market_data = fake_fetch_market_data
        monkeypatch.setitem(sys.modules, "src.market_data", fake_pkg)

        a = base_adapter.VibeTradingAIAdapter()
        data = a.fetch_market_data("AAPL", "2026-10-07")
        assert data["ticker"] == "AAPL"
        assert "x" in data["ohlcv"]

    def test_adapter_handles_missing_base_package(self, monkeypatch) -> None:
        """基座未装 → 抛 ImportError（让 caller 降级）"""
        from src.vibe_trading_cn import base_adapter

        # v3.7 修复：基座入口是 src.market_data；确保其不在 sys.modules
        monkeypatch.delitem(sys.modules, "src.market_data", raising=False)
        a = base_adapter.VibeTradingAIAdapter()
        with pytest.raises((ImportError, AttributeError)):
            a.fetch_market_data("AAPL", "2026-10-07")

    def test_adapter_llm_call(self, monkeypatch) -> None:
        """v3.7: call_llm 优先 src.agent.LLM（如果有），否则空 stub"""
        from src.vibe_trading_cn import base_adapter

        # 模拟 src.agent 含 LLM 类
        fake_agent = types.ModuleType("src.agent")

        class FakeLLM:
            def __call__(self, messages):
                return type("R", (), {"content": "BASE_LLM_OK"})()

        fake_agent.LLM = FakeLLM
        monkeypatch.setitem(sys.modules, "src.agent", fake_agent)

        a = base_adapter.VibeTradingAIAdapter()
        result = a.call_llm([("system", "x")])
        assert result.content == "BASE_LLM_OK"

    def test_adapter_llm_stub_when_no_src_agent(self, monkeypatch) -> None:
        """v3.7: src.agent 未装 → call_llm 兜底空 stub"""
        from src.vibe_trading_cn import base_adapter

        # 确保 src.agent 不在 sys.modules
        monkeypatch.delitem(sys.modules, "src.agent", raising=False)

        a = base_adapter.VibeTradingAIAdapter()
        result = a.call_llm([("system", "x")])
        # 兜底：空 content
        assert result.content == ""


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
