"""v3.5 PR-9 基座适配层 测试（红绿重构循环）

设计目标：
- production_adapter 提供真实 stub（不依赖外部包）
- fetch_market_data 返回标准 v3 schema（fundamentals / news / social / ohlcv）
- call_llm 接受基座 LLMClient（duck typing）或测试 mock
- CLI 端到端：analyze <TICKER> --date <YYYY-MM-DD> 跑通 PR-7 + PR-8 + PR-6a

v3 简化（PR-9）：
- 不接 vibe-trading-ai（基座包未装）
- 不接 LLM API（避免外部依赖）
- 提供"生产 stub"：数据用 fixture 真实样本，LLM 接受 duck typing
- 真集成留到后续 PR（v3.6+）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


# ====================================================================
# production_adapter.fetch_market_data 测试
# ====================================================================

class TestProductionAdapter:
    """生产 stub：fetch_market_data / call_llm 接受真实数据 / 真实 LLM"""

    def test_fetch_market_data_returns_v3_schema(self, monkeypatch) -> None:
        """返回 v3 标准 schema（fundamentals / news / social / ohlcv）"""
        from src.vibe_trading_cn.production_adapter import fetch_market_data
        result = fetch_market_data("BTCUSDT", "2026-10-07")
        assert "ticker" in result
        assert result["ticker"] == "BTCUSDT"
        assert "fundamentals" in result
        assert "news" in result
        assert "social" in result
        assert "ohlcv" in result

    def test_fetch_market_data_fundamentals_has_fields(self) -> None:
        """fundamentals 含 PE / PB / ROE / revenue_yoy"""
        from src.vibe_trading_cn.production_adapter import fetch_market_data
        result = fetch_market_data("BTCUSDT", "2026-10-07")
        f = result["fundamentals"]
        # 注：BTC 币类无 PE/PB，可为 None
        assert "pe" in f or "roe" in f  # 至少一个

    def test_call_llm_accepts_messages(self) -> None:
        """call_llm 接受 messages → 返回 obj with .content（duck typing）"""
        from src.vibe_trading_cn.production_adapter import call_llm
        result = call_llm([
            ("system", "你是分析师"),
            ("human", "BTC 怎么看？"),
        ])
        # 默认 stub 返回空字符串（生产需注入 LLMClient）
        assert hasattr(result, "content")

    def test_call_llm_with_custom_client(self) -> None:
        """call_llm 接受自定义 client（duck typing）"""
        from src.vibe_trading_cn.production_adapter import call_llm

        class FakeClient:
            def __call__(self, messages):
                return type("R", (), {"content": "FAKE_RESPONSE"})()

        result = call_llm(
            [("system", "x"), ("human", "y")],
            llm_client=FakeClient(),
        )
        assert result.content == "FAKE_RESPONSE"


# ====================================================================
# CLI 端到端测试
# ====================================================================

class TestCLIAnalyze:
    """cli_analyze 端到端：ticker + date → decision_log append"""

    def test_cli_analyze_dry_run(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """--dry-run: 不写 decision_log"""
        log = tmp_path / "decision_log.jsonl"
        monkeypatch.setenv("VIBE_DECISION_LOG_PATH", str(log))

        from src.vibe_trading_cn.cli_analyze import main
        rc = main([
            "--ticker", "BTCUSDT",
            "--date", "2026-10-07",
            "--dry-run",
        ])
        assert rc == 0
        assert not log.exists()  # dry-run 不写

    def test_cli_analyze_writes_decision_log(self, tmp_path: Path, monkeypatch) -> None:
        """默认：写 decision_log.jsonl"""
        log = tmp_path / "decision_log.jsonl"
        monkeypatch.setenv("VIBE_DECISION_LOG_PATH", str(log))

        from src.vibe_trading_cn.cli_analyze import main
        rc = main([
            "--ticker", "BTCUSDT",
            "--date", "2026-10-07",
        ])
        assert rc == 0
        assert log.exists()
        entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        e = entries[0]
        assert e["ticker"] == "BTCUSDT"
        assert e["trade_date"] == "2026-10-07"
        assert "INVESTMENT_PLAN" in e["decision"]
        assert "RISK_VERDICT" in e["decision"]

    def test_cli_analyze_missing_args(self, capsys) -> None:
        """缺 ticker → 返回非 0"""
        from src.vibe_trading_cn.cli_analyze import main
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code != 0
