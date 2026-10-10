"""v3.5 PR-10 scheduler — 简单 cron-like 调度器

设计：
- 纯 threading（不引入 APScheduler / Celery 等外部库）
- 任务：可调用对象 () -> int（返回 0 = 成功，>0 = 警告，<0 = 错误）
- 单实例（不支持多进程；多进程由 v3.6+ 考虑）
- 异常隔离：单任务失败不阻塞其它任务
- start() 非阻塞；stop() 设置标志后等待当前循环退出
- 默认任务：settle_pending（每 1 小时执行）

v3 简化（对照 APScheduler）：
- 不支持 cron 表达式（只支持固定 interval）
- 不支持任务依赖 / 优先级
- 不支持持久化（重启丢失下次执行时间 — v3.6+ 考虑持久化）
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .decision_log import DecisionLog
from .scheduler_state import SchedulerState


logger = logging.getLogger(__name__)


# 任务类型：callable() -> int
Task = Callable[[], int]


def settle_pending_task() -> int:
    """默认任务：扫 pending entries → 拉价 → 算 P&L → 写 reflection

    Returns: 实际 settle 数量
    """
    log = DecisionLog()
    settled = log.settle_pending(holding_days=5, benchmark="SPY", reflect=True)
    logger.info(f"[settle_pending] settled {settled} entries")
    return settled


class Scheduler:
    """简单调度器（threading + 间隔循环 + 可选 state 持久化）"""

    def __init__(
        self,
        interval_seconds: float = 3600,
        state_path: Path | None = None,
    ) -> None:
        self.interval_seconds = interval_seconds
        self._tasks: dict[str, Task] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # v3.5 PR-11: state 持久化（可选）
        self._state_path = state_path
        self._state: Optional[SchedulerState] = (
            SchedulerState.load_or_default(state_path) if state_path else None
        )

    def add_task(self, name: str, task: Task) -> None:
        """注册一个任务（同名覆盖）"""
        with self._lock:
            self._tasks[name] = task

    def task_names(self) -> list[str]:
        """返回已注册任务名列表"""
        with self._lock:
            return list(self._tasks.keys())

    def is_running(self) -> bool:
        """调度器是否在跑（线程 alive）"""
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> dict[str, int]:
        """立即跑一次所有任务（不依赖 start）

        Returns: {task_name: return_value} 字典
        """
        results: dict[str, int] = {}
        # 拷贝任务列表（避免 run 时被修改）
        with self._lock:
            tasks = list(self._tasks.items())
        for name, task in tasks:
            try:
                results[name] = task()
            except Exception as e:
                logger.error(f"[scheduler] task {name!r} failed: {e}\n{traceback.format_exc()}")
                results[name] = -1
        # v3.5 PR-11: 持久化 state
        if self._state is not None and self._state_path is not None:
            now = time.time()
            next_run = now + self.interval_seconds
            self._state.record_run(results, next_run_ts=next_run)
            self._state.save(self._state_path)
        return results

    def start(self) -> None:
        """启动后台线程（不阻塞）"""
        if self.is_running():
            logger.warning("[scheduler] already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="v3-scheduler", daemon=True)
        self._thread.start()
        logger.info(f"[scheduler] started, interval={self.interval_seconds}s")

    def stop(self, timeout: float = 5.0) -> None:
        """停止后台线程（设置标志 + join）"""
        if not self.is_running():
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("[scheduler] stopped")

    def _loop(self) -> None:
        """后台循环：每 interval_seconds 跑一次任务"""
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as e:
                # 兜底（run_once 已有 try/except，这里防止调度本身死掉）
                logger.error(f"[scheduler] loop error: {e}\n{traceback.format_exc()}")
            # 等待 interval（可被 stop 提前唤醒）
            if self._stop_event.wait(self.interval_seconds):
                break

    @classmethod
    def default(cls) -> "Scheduler":
        """默认调度器：1 小时间隔，含 settle_pending 任务"""
        s = cls(interval_seconds=3600)
        s.add_task("settle_pending", settle_pending_task)
        return s


__all__ = ["Scheduler", "settle_pending_task", "Task"]
