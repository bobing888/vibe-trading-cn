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

    def test_factory_a_share_6digit_returns_yfinance(self) -> None:
        """A 股代码（6 位纯数字）→ YFinanceVendor（第 6 轮 review IMPORTANT 修复）"""
        from src.vibe_trading_cn.data_vendor import get_vendor
        v = get_vendor("600519")
        assert v.name == "yfinance"

    def test_factory_a_share_sz_returns_yfinance(self) -> None:
        """A 股深证代码 → YFinanceVendor"""
        from src.vibe_trading_cn.data_vendor import get_vendor
        v = get_vendor("000001.SZ")
        assert v.name == "yfinance"

    def test_factory_crypto_with_slash_returns_ccxt(self) -> None:
        """加密 ticker 已带 / → CCXTVendor（第 6 轮 review MINOR 修复）"""
        from src.vibe_trading_cn.data_vendor import get_vendor
        v = get_vendor("BTC/USDT")
        assert "ccxt" in v.name

    def test_factory_lowercase_usdt_returns_ccxt(self) -> None:
        """小写 usdt 后缀也走 CCXT"""
        from src.vibe_trading_cn.data_vendor import get_vendor
        v = get_vendor("btcusdt")
        assert "ccxt" in v.name


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


# ====================================================================
# v3.7 真实 vendor 可用性（ccxt + yfinance 装好后）
# ====================================================================

class TestRealVendorAvailability:
    """v3.7 真实 vendor 可用性：pip install ccxt yfinance 后

    这些测试验证装好后 vendor 能真构造 + 路由 + 调用 ccxt/yfinance 库。
    实际 HTTP 调用可能因网络限流失败，所以用 monkeypatch mock 掉 fetch
    验证集成链路通，HTTP 失败降级也通。
    """

    def test_ccxt_library_importable(self) -> None:
        """ccxt 库可 import（pip install ccxt>=4.0）"""
        try:
            import ccxt
        except ImportError as e:
            pytest.skip(f"ccxt not installed: {e}")
        assert hasattr(ccxt, "binance")
        assert hasattr(ccxt, "okx")
        assert hasattr(ccxt, "bybit")

    def test_yfinance_library_importable(self) -> None:
        """yfinance 库可 import（pip install yfinance）"""
        try:
            import yfinance as yf
        except ImportError as e:
            pytest.skip(f"yfinance not installed: {e}")
        assert hasattr(yf, "Ticker")

    def test_ccxt_vendor_lazy_loads_exchange(self, monkeypatch) -> None:
        """v3.7 CCXTVendor lazy load ccxt exchange（避免未装时 import 失败）"""
        from src.vibe_trading_cn.data_vendor import CCXTVendor

        # 替换 _get_exchange 模拟 lazy load
        v = CCXTVendor(exchange="binance")
        assert v._exchange is None  # 还没初始化

        # 模拟 ccxt exchange
        class FakeExchange:
            name = "Binance"
            def fetch_ohlcv(self, symbol, timeframe, since, limit):
                return [[1727750400000, 63000, 64500, 62800, 64200, 28000]]

        monkeypatch.setattr(v, "_get_exchange", lambda: FakeExchange())
        data = v("BTCUSDT", "2024-10-01")
        assert data["ticker"] == "BTCUSDT"
        assert "2024-10-01" in data["ohlcv"]
        assert data["ohlcv"]["2024-10-01"]["close"] == 64200

    def test_yfinance_vendor_real_call_fails_gracefully(self, monkeypatch) -> None:
        """v3.7 YFinanceVendor 真调用 yfinance（限流时降级到空 schema）"""
        from src.vibe_trading_cn.data_vendor import YFinanceVendor

        # 模拟 yfinance.Ticker 限流
        class FakeTicker:
            def history(self, **kwargs):
                import yfinance as yf
                raise RuntimeError("YFRateLimitError: Too Many Requests")

        def fake_ticker(symbol):
            return FakeTicker()

        monkeypatch.setattr(
            "yfinance.Ticker", fake_ticker
        )
        v = YFinanceVendor()
        data = v("AAPL", "2026-10-01")
        # 限流时降级到空 schema（fixture 兜底由 production_adapter 处理）
        assert data["ticker"] == "AAPL"
        assert data["ohlcv"] == {}  # 空
        assert data["fundamentals"] == {}

    def test_production_adapter_full_chain_with_real_vendors(self, monkeypatch) -> None:
        """v3.7 production_adapter 完整链路：vendor 限流 → fixture 兜底"""
        from src.vibe_trading_cn import production_adapter, data_vendor

        # 模拟 vendor 限流
        class RateLimitedVendor:
            name = "rate-limited"
            def __call__(self, ticker, trade_date):
                raise RuntimeError("Rate limited")

        monkeypatch.setattr(data_vendor, "get_vendor", lambda t: RateLimitedVendor())
        result = production_adapter.fetch_market_data("BTCUSDT", "2026-10-07")
        # 三级降级：基座失败 → vendor 失败 → fixture 兜底
        assert "ohlcv" in result
        assert "2026-10-01" in result["ohlcv"]
