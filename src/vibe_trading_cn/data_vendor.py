"""v3.5 PR-13 data_vendor — 多 vendor 真实数据接入

设计：
- BaseDataVendor 抽象类：fetch(ticker, trade_date) → dict
- 失败降级：vendor 错误 → 返回空 schema（fixture 兜底）
- v3.5 P0 实现：CCXTVendor（加密）+ YFinanceVendor（美股/A股）
- normalize_schema 统一 v3 schema

v3 简化：
- 不接 akshare / futu / baostock（v3.6 候选）
- 现货数据为主，衍生品/外汇留 v3.6+
- 不做实时 streaming
- 不强依赖 ccxt / yfinance 库（lazy import，缺库时仍能 fixture 兜底）
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional


logger = logging.getLogger(__name__)


def _empty_schema(ticker: str, trade_date: str) -> dict[str, Any]:
    """统一 v3 schema（空数据）"""
    return {
        "ticker": ticker,
        "trade_date": trade_date,
        "ohlcv": {},
        "fundamentals": {},
        "news": [],
        "social": {},
    }


def _normalize_schema(ticker: str, trade_date: str, raw: dict) -> dict[str, Any]:
    """统一 v3 schema（缺字段补空）"""
    base = _empty_schema(ticker, trade_date)
    base.update(raw or {})
    for key in ("ticker", "trade_date", "ohlcv", "fundamentals", "news", "social"):
        base.setdefault(key, _empty_schema(ticker, trade_date)[key])
    return base


class BaseDataVendor(ABC):
    """数据 vendor 抽象类"""

    name: str = "base"

    @abstractmethod
    def fetch(self, ticker: str, trade_date: str) -> dict[str, Any]:
        """拉 ticker 在 trade_date 的数据

        Returns: v3 schema dict
        """
        raise NotImplementedError

    def _safe_fetch(self, ticker: str, trade_date: str) -> dict[str, Any]:
        """统一入口：fetch + 异常兜底 + normalize"""
        try:
            raw = self.fetch(ticker, trade_date)
        except Exception as e:
            logger.error(f"[{self.name}] fetch {ticker!r} failed: {e}")
            raw = {}
        return _normalize_schema(ticker, trade_date, raw)

    def __call__(self, ticker: str, trade_date: str) -> dict[str, Any]:
        return self._safe_fetch(ticker, trade_date)


class CCXTVendor(BaseDataVendor):
    """ccxt 加密 vendor

    支持 ticker: BTC/USDT, ETH/USDT 等（自动转 ccxt 格式）
    """

    def __init__(self, exchange: str = "binance") -> None:
        self.exchange = exchange
        self.name = f"ccxt:{exchange}"
        self._exchange = None  # lazy load

    def _get_exchange(self):
        """lazy load ccxt exchange（避免未装 ccxt 时 import 失败）"""
        if self._exchange is None:
            try:
                import ccxt  # type: ignore
            except ImportError as e:
                raise ImportError(
                    "ccxt not installed; pip install ccxt"
                ) from e
            self._exchange = getattr(ccxt, self.exchange)({"enableRateLimit": True})
        return self._exchange

    def fetch(self, ticker: str, trade_date: str) -> dict[str, Any]:
        ex = self._get_exchange()
        # ticker 格式：BTCUSDT → BTC/USDT
        symbol = self._normalize_symbol(ticker)
        # 拉最近 30 天 OHLCV（trade_date 为结束日）
        end_ts = int(
            datetime.fromisoformat(trade_date).replace(tzinfo=timezone.utc).timestamp() * 1000
        )
        start_ts = end_ts - 30 * 24 * 60 * 60 * 1000  # 30 天前
        ohlcv = ex.fetch_ohlcv(symbol, "1d", since=start_ts, limit=30)
        return {
            "ticker": ticker,
            "trade_date": trade_date,
            "ohlcv": self._parse_ohlcv(ohlcv),
        }

    def _parse_ohlcv(self, raw: list[list[float]]) -> dict[str, dict[str, float]]:
        """ccxt OHLCV 格式 [[ts, open, high, low, close, volume], ...] → {date: {o,h,l,c,v}}"""
        result = {}
        for row in raw:
            ts_ms, o, h, l, c, v = row
            date_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            result[date_str] = {
                "open": float(o), "high": float(h),
                "low": float(l), "close": float(c),
                "volume": float(v),
            }
        return result

    @staticmethod
    def _normalize_symbol(ticker: str) -> str:
        """BTCUSDT → BTC/USDT；ETHUSDT → ETH/USDT"""
        if "/" in ticker:
            return ticker
        if ticker.endswith("USDT"):
            base = ticker[:-4]
            return f"{base}/USDT"
        return ticker


class YFinanceVendor(BaseDataVendor):
    """yfinance 美股/A 股 vendor

    A 股代码需加 .SS（上证）或 .SZ（深证）后缀
    """

    def __init__(self) -> None:
        self.name = "yfinance"

    def _get_ticker(self, symbol: str):
        try:
            import yfinance as yf  # type: ignore
        except ImportError as e:
            raise ImportError(
                "yfinance not installed; pip install yfinance"
            ) from e
        return yf.Ticker(symbol)

    def fetch(self, ticker: str, trade_date: str) -> dict[str, Any]:
        t = self._get_ticker(ticker)
        # 拉 30 天 OHLCV
        end_date = datetime.fromisoformat(trade_date)
        start_date = end_date.replace(day=max(1, end_date.day - 30))
        hist = t.history(start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"))
        ohlcv = {}
        for date_idx, row in hist.iterrows():
            date_str = date_idx.strftime("%Y-%m-%d")
            ohlcv[date_str] = {
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            }
        # fundamentals（yfinance .info）
        info = {}
        try:
            raw_info = t.info
            info = {
                "pe": raw_info.get("trailingPE"),
                "pb": raw_info.get("priceToBook"),
                "roe": raw_info.get("returnOnEquity"),
                "revenue_yoy": raw_info.get("revenueGrowth"),
                "market_cap": raw_info.get("marketCap"),
            }
        except Exception:
            pass
        return {
            "ticker": ticker,
            "trade_date": trade_date,
            "ohlcv": ohlcv,
            "fundamentals": info,
        }


def get_vendor(ticker: str) -> BaseDataVendor:
    """Factory: 按 ticker 类型选 vendor

    路由规则（第 6 轮 review IMPORTANT 修复）：
    - 含 "/" → 已带 ccxt 格式（CCXTVendor）
    - 6 位纯数字 → A 股（YFinanceVendor，调用方需加 .SS / .SZ）
    - 6 位数字 + .SS / .SZ → A 股（YFinanceVendor）
    - 含 "USDT" 后缀（不区分大小写）→ CCXTVendor
    - 其它美股 / ETF → YFinanceVendor

    优先级：精确匹配 > 正则 > 后缀 > 默认
    """
    upper = ticker.upper()
    # A 股：6 位数字（含 .SS / .SZ 后缀）
    if re.match(r"^\d{6}(\.SS|\.SZ)?$", upper):
        return YFinanceVendor()
    # 加密：USDT 后缀或已带 /
    if "USDT" in upper or "/" in ticker:
        return CCXTVendor()
    # 默认美股 / ETF
    return YFinanceVendor()


__all__ = [
    "BaseDataVendor",
    "CCXTVendor",
    "YFinanceVendor",
    "get_vendor",
]
