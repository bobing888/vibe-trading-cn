"""v3.5 PR-13 真实数据 vendor 接入 测试

设计目标：
- 多 vendor 抽象：BaseDataVendor
- v3.5 P0：ccxt（加密）+ yfinance（美股/A股都支持）
- 统一返回 v3 schema（ticker / ohlcv / fundamentals / news / social）
- 失败降级：vendor 错误 → 返回空 schema（fixture 兜底）
- 不接所有 vendor（akshare / futu / baostock → v3.6 候选）

v3 简化（对照基座 vibe-trading 30 loader）：
- 选最通用 2 vendor（ccxt + yfinance）覆盖 ~80% 用例
- 现货数据为主，衍生品/外汇留 v3.6+
- 不做实时 streaming（一次性拉取）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# ====================================================================
# BaseDataVendor 抽象测试
# ====================================================================

class TestBaseDataVendor:
    """BaseDataVendor 抽象类"""

    def test_base_vendor_is_abstract(self) -> None:
        from src.vibe_trading_cn.data_vendor import BaseDataVendor
        with pytest.raises(TypeError):
            BaseDataVendor()  # type: ignore[abstract]

    def test_base_vendor_subclass_works(self) -> None:
        from src.vibe_trading_cn.data_vendor import BaseDataVendor

        class FakeVendor(BaseDataVendor):
            name = "fake"

            def fetch(self, ticker, trade_date):
                return {"ticker": ticker, "trade_date": trade_date, "ohlcv": {"x": 1}}

        v = FakeVendor()
        r = v.fetch("AAPL", "2026-10-07")
        assert r["ticker"] == "AAPL"

    def test_base_vendor_handles_errors_gracefully(self) -> None:
        """fetch 抛错 → 返回空 schema，不抛"""
        from src.vibe_trading_cn.data_vendor import BaseDataVendor

        class BrokenVendor(BaseDataVendor):
            name = "broken"

            def fetch(self, ticker, trade_date):
                raise RuntimeError("vendor down")

        v = BrokenVendor()
        # __call__ 走 _safe_fetch 兜底
        r = v("AAPL", "2026-10-07")
        assert r["ticker"] == "AAPL"  # 即使失败也返回基础 schema
        assert r["ohlcv"] == {}  # 空 ohlcv

    def test_base_vendor_normalize_schema(self) -> None:
        """fetch 返回值必须含 v3 标准 schema 字段"""
        from src.vibe_trading_cn.data_vendor import BaseDataVendor

        class PartialVendor(BaseDataVendor):
            name = "partial"

            def fetch(self, ticker, trade_date):
                return {"ticker": ticker, "trade_date": trade_date}  # 缺 ohlcv/fundamentals

        v = PartialVendor()
        r = v("AAPL", "2026-10-07")
        # normalize 后必有所有字段
        for key in ("ticker", "trade_date", "ohlcv", "fundamentals", "news", "social"):
            assert key in r


# ====================================================================
# CCXT 单元测试（mock HTTP）
# ====================================================================

class TestCCXTVendor:
    """ccxt 加密 vendor（用 fixture mock）"""

    def test_ccxt_vendor_constructs(self) -> None:
        from src.vibe_trading_cn.data_vendor import CCXTVendor
        v = CCXTVendor(exchange="binance")
        assert v.exchange == "binance"
        assert v.name == "ccxt:binance"

    def test_ccxt_vendor_parses_ohlcv(self, monkeypatch) -> None:
        """parse ccxt OHLCV 格式"""
        from src.vibe_trading_cn.data_vendor import CCXTVendor
        v = CCXTVendor(exchange="binance")

        # ccxt OHLCV: [[ts, open, high, low, close, volume], ...]
        raw = [
            [1727750400000, 63000, 64500, 62800, 64200, 28000],  # 2024-10-01
            [1727836800000, 64200, 65800, 64100, 65500, 31000],  # 2024-10-02
        ]
        parsed = v._parse_ohlcv(raw)
        assert "2024-10-01" in parsed
        assert parsed["2024-10-01"]["close"] == 64200
        assert parsed["2024-10-01"]["volume"] == 28000


class TestYFinanceVendor:
    """yfinance 美股/A 股 vendor"""

    def test_yfinance_vendor_constructs(self) -> None:
        from src.vibe_trading_cn.data_vendor import YFinanceVendor
        v = YFinanceVendor()
        assert v.name == "yfinance"


# ====================================================================
# Factory + 优先级测试
# ====================================================================

class TestDataVendorFactory:
    """get_vendor() factory"""

    def test_factory_crypto_returns_ccxt(self) -> None:
        """加密 ticker（USDT 后缀）→ CCXTVendor"""
        from src.vibe_trading_cn.data_vendor import get_vendor
        v = get_vendor("BTCUSDT")
        assert "ccxt" in v.name

    def test_factory_stock_returns_yfinance(self) -> None:
        """股票 ticker（无 USDT）→ YFinanceVendor"""
        from src.vibe_trading_cn.data_vendor import get_vendor
        v = get_vendor("AAPL")
        assert v.name == "yfinance"

    def test_factory_a_share_returns_yfinance(self) -> None:
        """A 股代码（数字）→ YFinanceVendor（A 股代码需加 .SS / .SZ 后缀）"""
        from src.vibe_trading_cn.data_vendor import get_vendor
        v = get_vendor("600519.SS")
        assert v.name == "yfinance"


# ====================================================================
# 端到端：production_adapter.fetch_market_data
# ====================================================================

class TestProductionAdapterIntegration:
    """production_adapter 接入真实 vendor"""

    def test_production_adapter_uses_ccxt_for_crypto(self, monkeypatch) -> None:
        from src.vibe_trading_cn import production_adapter, data_vendor

        class FakeCCXT:
            name = "ccxt:binance"
            def fetch(self, ticker, trade_date):
                return {
                    "ticker": ticker, "trade_date": trade_date,
                    "ohlcv": {"2026-10-01": {"open": 100, "high": 105, "low": 99, "close": 103, "volume": 1000}},
                    "fundamentals": {}, "news": [], "social": {},
                }

        monkeypatch.setattr(data_vendor, "get_vendor", lambda t: FakeCCXT())
        result = production_adapter.fetch_market_data("BTCUSDT", "2026-10-07")
        assert result["ticker"] == "BTCUSDT"
        assert "2026-10-01" in result["ohlcv"]

    def test_production_adapter_falls_back_to_fixture_on_error(self, monkeypatch) -> None:
        """vendor 抛错 → fixture 兜底"""
        from src.vibe_trading_cn import production_adapter, data_vendor

        class BrokenVendor:
            name = "broken"
            def fetch(self, ticker, trade_date):
                raise RuntimeError("API 限流")

        monkeypatch.setattr(data_vendor, "get_vendor", lambda t: BrokenVendor())
        result = production_adapter.fetch_market_data("BTCUSDT", "2026-10-07")
        # 降级到 fixture（BTCUSDT 有真实样本）
        assert "ohlcv" in result
        assert "2026-10-01" in result["ohlcv"]
