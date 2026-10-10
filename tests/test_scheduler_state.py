"""v3.5 PR-11 scheduler 持久化 测试（红绿重构循环）

设计目标：
- scheduler state 持久化到 JSON 文件（last_run / next_run / 任务历史）
- 重启后不丢失下次执行时间
- 持久化原子写入（write-then-rename）
- 失败降级：state 文件损坏 → 重置为空 state
- 提供 recover() 入口

v3 简化（对照 APScheduler jobstores）：
- 单 JSON 文件（不接 SQL / Redis）
- 不支持任务依赖 / 优先级
- 单实例（多实例由 v3.6+ 考虑加文件锁）
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ====================================================================
# State 持久化单元测试
# ====================================================================

class TestSchedulerState:
    """SchedulerState JSON 持久化"""

    def test_state_default_empty(self, tmp_path: Path) -> None:
        from src.vibe_trading_cn.scheduler_state import SchedulerState
        path = tmp_path / "state.json"
        state = SchedulerState.load_or_default(path)
        assert state.last_run is None
        assert state.next_run is None
        assert state.task_history == []
        assert state.run_count == 0

    def test_state_save_and_load(self, tmp_path: Path) -> None:
        """save → load 恢复所有字段"""
        from src.vibe_trading_cn.scheduler_state import SchedulerState
        path = tmp_path / "state.json"
        s1 = SchedulerState(
            last_run=1234567890.0,
            next_run=1234571490.0,  # +3600s
            task_history=[{"name": "settle_pending", "code": 0, "ts": 1234567890.0}],
            run_count=5,
        )
        s1.save(path)
        assert path.exists()

        s2 = SchedulerState.load_or_default(path)
        assert s2.last_run == 1234567890.0
        assert s2.next_run == 1234571490.0
        assert s2.run_count == 5
        assert len(s2.task_history) == 1
        assert s2.task_history[0]["name"] == "settle_pending"

    def test_state_handles_corrupted_file(self, tmp_path: Path) -> None:
        """损坏文件 → 返回默认 state，不抛"""
        from src.vibe_trading_cn.scheduler_state import SchedulerState
        path = tmp_path / "state.json"
        path.write_text("invalid json {{{")
        state = SchedulerState.load_or_default(path)
        assert state.run_count == 0  # 默认值

    def test_state_atomic_write(self, tmp_path: Path) -> None:
        """save 用 write-then-rename 原子写入"""
        from src.vibe_trading_cn.scheduler_state import SchedulerState
        path = tmp_path / "state.json"
        s1 = SchedulerState(last_run=1.0, next_run=2.0, run_count=1)
        s1.save(path)
        # 不应有临时文件残留
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


# ====================================================================
# Scheduler 集成测试
# ====================================================================

class TestSchedulerPersistence:
    """Scheduler 持久化集成"""

    def test_scheduler_persists_state_after_run_once(self, tmp_path: Path) -> None:
        """run_once 后 state 文件被更新"""
        from src.vibe_trading_cn.scheduler import Scheduler
        from src.vibe_trading_cn.scheduler_state import SchedulerState
        state_path = tmp_path / "state.json"

        s = Scheduler(interval_seconds=3600, state_path=state_path)
        s.add_task("noop", lambda: 0)
        s.run_once()

        assert state_path.exists()
        state = SchedulerState.load_or_default(state_path)
        assert state.run_count == 1
        assert state.last_run is not None
        assert state.task_history[-1]["name"] == "noop"
        assert state.task_history[-1]["code"] == 0

    def test_scheduler_recovers_state_on_restart(self, tmp_path: Path) -> None:
        """重启后 state 持续累积（run_count 递增）"""
        from src.vibe_trading_cn.scheduler import Scheduler
        from src.vibe_trading_cn.scheduler_state import SchedulerState
        state_path = tmp_path / "state.json"

        # 第 1 次"重启"前
        s1 = Scheduler(interval_seconds=3600, state_path=state_path)
        s1.add_task("noop", lambda: 0)
        s1.run_once()

        # 模拟重启
        s2 = Scheduler(interval_seconds=3600, state_path=state_path)
        s2.add_task("noop", lambda: 0)
        s2.run_once()

        state = SchedulerState.load_or_default(state_path)
        assert state.run_count == 2  # 累积
        assert len(state.task_history) == 2

    def test_scheduler_without_state_path_no_persistence(self, tmp_path: Path) -> None:
        """不传 state_path → 不持久化（兼容旧行为）"""
        from src.vibe_trading_cn.scheduler import Scheduler
        s = Scheduler(interval_seconds=3600)
        s.add_task("noop", lambda: 0)
        s.run_once()
        # 内存中不持久化
        assert s._state is None or s._state.run_count == 0


# ====================================================================
# 端到端：CLI 重启恢复
# ====================================================================

class TestCLIStateRecovery:
    """cli_scheduler 支持 state 路径"""

    def test_cli_scheduler_uses_state_path(self, tmp_path: Path, monkeypatch) -> None:
        """--state-path 持久化 + 重启后 run_count 递增"""
        from src.vibe_trading_cn.cli_scheduler import main
        from src.vibe_trading_cn.scheduler_state import SchedulerState
        state_path = tmp_path / "state.json"

        # 第 1 次
        rc = main(["--once", "--state-path", str(state_path)])
        assert rc == 0
        state1 = SchedulerState.load_or_default(state_path)
        assert state1.run_count == 1

        # 第 2 次
        rc = main(["--once", "--state-path", str(state_path)])
        assert rc == 0
        state2 = SchedulerState.load_or_default(state_path)
        assert state2.run_count == 2  # 持续累积
