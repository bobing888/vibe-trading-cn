"""v3.5 PR-6a 决策记忆 append + load 测试（红绿重构循环）

设计目标：
- 把 TA `TradingMemoryLog.store_decision` 适配为 v3 JSONL 格式
- 存储路径：~/.vibe-trading/decision_log.jsonl
- 不依赖 TA tradingagents.* 库（纯 v3 实现）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.vibe_trading_cn.decision_log import DecisionEntry, DecisionLog


@pytest.fixture
def tmp_log_path(tmp_path: Path, monkeypatch) -> Path:
    """重定向到 tmp_path，避免污染 ~/.vibe-trading/"""
    log = tmp_path / "decision_log.jsonl"
    monkeypatch.setenv("VIBE_DECISION_LOG_PATH", str(log))
    return log


class TestStoreDecision:
    """红：写期望行为，测试最初必失败（function not yet implemented）"""

    def test_first_append_creates_file(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        log.append(
            ticker="BTCUSDT",
            trade_date="2026-10-07",
            decision="STRONG_BUY at 65000, target 70000",
            rating="STRONG",
        )
        assert tmp_log_path.exists()

    def test_append_writes_valid_jsonl(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        log.append(
            ticker="ETHUSDT",
            trade_date="2026-10-07",
            decision="BUY at 3500, target 4000",
            rating="WEAK",
        )
        lines = tmp_log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["ticker"] == "ETHUSDT"
        assert entry["trade_date"] == "2026-10-07"
        assert entry["decision"] == "BUY at 3500, target 4000"
        assert entry["rating"] == "WEAK"
        assert entry["status"] == "pending"

    def test_idempotency_same_ticker_date(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        log.append("BTCUSDT", "2026-10-07", "BUY", "STRONG")
        log.append("BTCUSDT", "2026-10-07", "SELL", "WEAK")  # 应被拒
        lines = tmp_log_path.read_text().strip().split("\n")
        assert len(lines) == 1  # 第二条不写入

    def test_different_ticker_appends(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        log.append("BTCUSDT", "2026-10-07", "BUY", "STRONG")
        log.append("ETHUSDT", "2026-10-07", "BUY", "STRONG")
        lines = tmp_log_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_different_date_appends(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        log.append("BTCUSDT", "2026-10-07", "BUY", "STRONG")
        log.append("BTCUSDT", "2026-10-08", "BUY", "STRONG")
        lines = tmp_log_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_append_thread_safe(self, tmp_log_path: Path) -> None:
        """并发 10 个 append 不丢数据"""
        import threading
        log = DecisionLog()
        threads = [
            threading.Thread(
                target=log.append,
                args=(f"T{i}", "2026-10-07", "BUY", "STRONG"),
            )
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = tmp_log_path.read_text().strip().split("\n")
        assert len(lines) == 10


class TestLoadEntries:
    """读路径：load_entries 应解析 JSONL"""

    def test_empty_log_returns_empty_list(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        assert log.load_entries() == []

    def test_load_parses_jsonl(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        log.append("BTCUSDT", "2026-10-07", "BUY", "STRONG")
        log.append("ETHUSDT", "2026-10-07", "SELL", "WEAK")
        entries = log.load_entries()
        assert len(entries) == 2
        assert entries[0]["ticker"] == "BTCUSDT"
        assert entries[1]["ticker"] == "ETHUSDT"

    def test_load_handles_malformed_line_gracefully(self, tmp_log_path: Path) -> None:
        tmp_log_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_log_path.write_text(
            '{"ticker":"BTCUSDT","trade_date":"2026-10-07","decision":"BUY","rating":"STRONG","status":"pending"}\n'
            "this is not json\n"
            '{"ticker":"ETHUSDT","trade_date":"2026-10-07","decision":"SELL","rating":"WEAK","status":"pending"}\n',
            encoding="utf-8",
        )
        log = DecisionLog()
        entries = log.load_entries()
        assert len(entries) == 2  # 跳过坏行


class TestGetPastContext:
    """get_past_context 给 agent prompt 喂历史反思"""

    def test_no_history_returns_empty(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        assert log.get_past_context("BTCUSDT") == ""

    def test_returns_only_settled(self, tmp_log_path: Path) -> None:
        """只返回已 settle 的，不返回 pending（避免学习未发生的未来）"""
        # 直接写一条 settled 记录
        tmp_log_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_log_path.write_text(
            json.dumps({
                "ticker": "BTCUSDT",
                "trade_date": "2026-10-01",
                "decision": "BUY",
                "rating": "STRONG",
                "status": "settled",
                "raw_return": 0.05,
                "reflection": "STRONG 决策在震荡市有效",
            }) + "\n"
            + json.dumps({
                "ticker": "BTCUSDT",
                "trade_date": "2026-10-07",
                "decision": "BUY",
                "rating": "STRONG",
                "status": "pending",
            }) + "\n",
            encoding="utf-8",
        )
        log = DecisionLog()
        ctx = log.get_past_context("BTCUSDT")
        assert "STRONG 决策在震荡市有效" in ctx
        assert "pending" not in ctx

    def test_n_same_and_n_cross(self, tmp_log_path: Path) -> None:
        """同 ticker 最多 n_same 条 + 跨 ticker 最多 n_cross 条"""
        log = DecisionLog()
        # 5 条 BTC（取 3 同 ticker）
        for i in range(5):
            log.append_internal({
                "ticker": "BTCUSDT",
                "trade_date": f"2026-10-0{i+1}",
                "decision": "BUY",
                "rating": "STRONG",
                "status": "settled",
                "raw_return": 0.05,
                "reflection": f"BTC lesson {i}",
            })
        # 5 条 ETH（取 2 跨 ticker）
        for i in range(5):
            log.append_internal({
                "ticker": "ETHUSDT",
                "trade_date": f"2026-10-0{i+1}",
                "decision": "SELL",
                "rating": "WEAK",
                "status": "settled",
                "raw_return": -0.02,
                "reflection": f"ETH lesson {i}",
            })
        ctx = log.get_past_context("BTCUSDT", n_same=3, n_cross=2)
        btc_count = ctx.count("BTC lesson")
        eth_count = ctx.count("ETH lesson")
        assert btc_count == 3
        assert eth_count == 2

    def test_as_of_filters_future(self, tmp_log_path: Path) -> None:
        """as_of=2026-10-05 应只返回 resolved <= 2026-10-05 的"""
        log = DecisionLog()
        log.append_internal({
            "ticker": "BTCUSDT",
            "trade_date": "2026-10-01",
            "decision": "BUY",
            "rating": "STRONG",
            "status": "settled",
            "raw_return": 0.05,
            "resolved": "2026-10-05",
            "reflection": "lesson before as_of",
        })
        log.append_internal({
            "ticker": "BTCUSDT",
            "trade_date": "2026-10-06",
            "decision": "BUY",
            "rating": "STRONG",
            "status": "settled",
            "raw_return": 0.03,
            "resolved": "2026-10-08",  # 晚于 as_of
            "reflection": "lesson after as_of",
        })
        ctx = log.get_past_context("BTCUSDT", as_of="2026-10-05")
        assert "lesson before as_of" in ctx
        assert "lesson after as_of" not in ctx


class TestDecisionEntry:
    """Pydantic/dataclass 风格 entry 校验"""

    def test_entry_required_fields(self) -> None:
        entry = DecisionEntry(
            ticker="BTCUSDT",
            trade_date="2026-10-07",
            decision="BUY",
            rating="STRONG",
        )
        assert entry.status == "pending"

    def test_entry_settled_status(self) -> None:
        entry = DecisionEntry(
            ticker="BTCUSDT",
            trade_date="2026-10-07",
            decision="BUY",
            rating="STRONG",
            status="settled",
        )
        assert entry.status == "settled"


# ====================================================================
# v3.5 PR-6b 决策记忆 settle + reflect 测试
# 设计要点：
# - settle_pending 走 PointInTimeFetcher（基座 fetch_market_data 包装）
# - 基准：v3 preset yaml benchmark_map（默认 SPY；币用 BTCUSDT 做 alpha）
# - holding_days 默认 5（TA 一致）
# - 完整窗口未成交 → 保留 pending（不假结算）
# - reflect 用基座 LLMClient（不引入 TA 依赖）
# ====================================================================


class TestSettlePending:
    """settle_pending: 拉实价 → 算 raw/alpha → 标记 settled + 写 reflection"""

    def test_no_pending_entries_returns_zero(self, tmp_log_path: Path) -> None:
        log = DecisionLog()
        assert log.settle_pending() == 0

    def test_pending_too_recent_stays_pending(self, tmp_log_path: Path) -> None:
        """未来日期：完整窗口未成交 → 保留 pending"""
        log = DecisionLog()
        log.append("BTCUSDT", "2099-01-01", "BUY", "STRONG")
        assert log.settle_pending() == 0
        entries = log.load_entries()
        assert entries[0]["status"] == "pending"
        assert entries[0].get("raw_return") is None

    def test_settled_entry_gets_raw_alpha_returns(
        self, tmp_log_path: Path, monkeypatch
    ) -> None:
        """老日期 + mock 拉价 → 应被结算，含 raw/alpha/resolved"""
        from src.vibe_trading_cn.decision_log import settle_helper

        # mock PointInTimeFetcher：返回固定序列价
        def fake_closes(ticker: str, start: str, end: str):
            # entry 100 → 5d 后 110（+10% raw）
            return {start: 100.0, end: 110.0}

        monkeypatch.setattr(settle_helper, "fetch_closes", fake_closes)
        log = DecisionLog()
        log.append("BTCUSDT", "2020-01-01", "BUY", "STRONG")
        settled = log.settle_pending(holding_days=5, benchmark="BTCUSDT")
        assert settled == 1
        entries = log.load_entries()
        e = entries[0]
        assert e["status"] == "settled"
        assert e["raw_return"] == pytest.approx(0.10, abs=0.001)
        assert e["alpha_return"] == pytest.approx(0.0, abs=0.001)  # benchmark=自己
        assert e["holding_days"] == 5
        assert e["resolved"] is not None  # resolution_date

    def test_settled_entry_writes_reflection(
        self, tmp_log_path: Path, monkeypatch
    ) -> None:
        """settle 后调用 reflect（mock LLM）→ 写入 reflection 字段"""
        from src.vibe_trading_cn.decision_log import settle_helper

        monkeypatch.setattr(settle_helper, "fetch_closes", lambda *a, **k: {
            "2020-01-01": 100.0, "2020-01-08": 110.0
        })
        # mock LLM（duck typing：call(messages) → obj with .content）
        monkeypatch.setattr(
            settle_helper, "call_llm",
            lambda *a, **k: type("R", (), {"content": "alpha +10%，STRONG 持仓 5d 后兑现，看多信号胜出"})()
        )

        log = DecisionLog()
        log.append("BTCUSDT", "2020-01-01", "BUY", "STRONG")
        log.settle_pending(holding_days=5, benchmark="BTCUSDT", reflect=True)
        entries = log.load_entries()
        assert "alpha +10%" in entries[0]["reflection"]

    def test_settle_idempotent(self, tmp_log_path: Path, monkeypatch) -> None:
        """已 settled 的不应再处理"""
        from src.vibe_trading_cn.decision_log import settle_helper
        monkeypatch.setattr(settle_helper, "fetch_closes", lambda *a, **k: {
            "2020-01-01": 100.0, "2020-01-08": 110.0
        })
        log = DecisionLog()
        log.append("BTCUSDT", "2020-01-01", "BUY", "STRONG")
        log.settle_pending()
        # 再调一次应跳过
        assert log.settle_pending() == 0


class TestReflect:
    """reflect(): 单条 settled 决策 → LLM 反思字符串"""

    def test_reflect_returns_string(self) -> None:
        from src.vibe_trading_cn.decision_log import reflect_decision
        text = reflect_decision(
            decision="STRONG_BUY at 65000",
            raw_return=0.10,
            alpha_return=0.05,
            benchmark="SPY",
            holding_days=5,
            llm_client=lambda msgs: type("R", (), {"content": "看多胜出"})(),
        )
        assert isinstance(text, str)
        assert "看多胜出" in text

    def test_reflect_without_llm_raises(self) -> None:
        from src.vibe_trading_cn.decision_log import reflect_decision
        with pytest.raises(ValueError, match="llm_client"):
            reflect_decision(
                decision="BUY", raw_return=0.0, alpha_return=0.0,
                benchmark="SPY", holding_days=5, llm_client=None,
            )
