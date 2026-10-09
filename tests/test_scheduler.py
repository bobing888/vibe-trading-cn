"""v3.5 PR-10 scheduler 测试（红绿重构循环）

设计目标：
- scheduler.py 提供 cron-like 调度（独立线程 + 间隔循环）
- 任务：settle_pending 周期执行（PR-6b）
- 可启动 / 停止 / 单次执行
- 异常恢复：单次任务失败不应终止整个调度
- CLI 入口 cli_scheduler

v3 简化（对照 APScheduler / Celery）：
- 不引入外部调度库
- 纯 threading.Timer + 间隔循环
- 单实例（不支持多进程）
- 测试用 mock time / mock sleep
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest


# ====================================================================
# scheduler 单元测试
# ====================================================================

class TestScheduler:
    """scheduler 启动 / 停止 / 单次执行"""

    def test_scheduler_starts_and_stops(self) -> None:
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler(interval_seconds=0.1)
        assert s.is_running() is False
        s.start()
        assert s.is_running() is True
        s.stop()
        assert s.is_running() is False

    def test_scheduler_runs_task_once(self) -> None:
        """run_once 立即执行一次"""
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler(interval_seconds=999)
        call_count = [0]

        def task():
            call_count[0] += 1
            return 0

        s.add_task("test", task)
        result = s.run_once()
        assert result["test"] == 0
        assert call_count[0] == 1

    def test_scheduler_runs_multiple_tasks(self) -> None:
        """多个任务都跑"""
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler(interval_seconds=999)
        results = {"a": 0, "b": 0}
        s.add_task("a", lambda: results.__setitem__("a", results["a"] + 1) or 0)
        s.add_task("b", lambda: results.__setitem__("b", results["b"] + 1) or 0)
        s.run_once()
        assert results["a"] == 1
        assert results["b"] == 1

    def test_scheduler_task_failure_does_not_stop_others(self) -> None:
        """单任务失败不影响其它任务"""
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler(interval_seconds=999)
        call_count = [0]

        def bad_task():
            raise RuntimeError("task failure")

        def good_task():
            call_count[0] += 1
            return 0

        s.add_task("bad", bad_task)
        s.add_task("good", good_task)
        results = s.run_once()
        assert "bad" in results
        assert "good" in results
        assert call_count[0] == 1

    def test_scheduler_loop_runs_periodically(self) -> None:
        """start 后任务按 interval 周期执行（用短 interval 测试）"""
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler(interval_seconds=0.05)
        call_count = [0]

        def task():
            call_count[0] += 1
            return 0

        s.add_task("counter", task)
        s.start()
        time.sleep(0.15)  # 期望至少跑 2-3 次
        s.stop()
        # 0.15s / 0.05s = 3 次；允许 ±1 误差
        assert 2 <= call_count[0] <= 5

    def test_scheduler_default_task_is_settle(self) -> None:
        """默认任务列表含 settle_pending"""
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler.default()
        names = s.task_names()
        assert "settle_pending" in names


# ====================================================================
# CLI scheduler 测试
# ====================================================================

class TestCLIScheduler:
    """cli_scheduler 单次模式（不真正 start loop）"""

    def test_cli_scheduler_run_once(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """--once: 跑一次 + 退出"""
        log = tmp_path / "decision_log.jsonl"
        monkeypatch.setenv("VIBE_DECISION_LOG_PATH", str(log))

        from src.vibe_trading_cn.cli_scheduler import main
        rc = main(["--once"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "settle_pending" in out

    def test_cli_scheduler_default_interval(self) -> None:
        """默认 interval 3600s（1 小时）"""
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler.default()
        assert s.interval_seconds == 3600
