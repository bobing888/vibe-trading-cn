"""v3.5 PR-11 scheduler_state — Scheduler 状态 JSON 持久化

设计：
- 单 JSON 文件（不接 SQL / Redis）
- 原子写入（write-to-tmp + rename）
- 损坏文件 → 返回默认 state（不抛）
- 单实例（多实例由 v3.6+ 考虑加文件锁）

v3 简化（对照 APScheduler jobstores）：
- 不支持任务依赖 / 优先级
- 不支持事务
- 单进程串行写
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


@dataclass
class SchedulerState:
    """Scheduler 状态（持久化单元）"""

    last_run: Optional[float] = None  # time.time() 秒
    next_run: Optional[float] = None  # time.time() 秒（计算: last_run + interval）
    task_history: list[dict] = field(default_factory=list)  # [{name, code, ts}, ...]
    run_count: int = 0

    def save(self, path: Path) -> None:
        """原子写入（write-to-tmp + rename）

        避免半写状态导致 JSON 损坏
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # 写入临时文件（同目录 atomic rename）
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, prefix=".state_", suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            logger.debug(f"[state] saved to {path} (run_count={self.run_count})")
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    @classmethod
    def load_or_default(cls, path: Path) -> "SchedulerState":
        """从 JSON 加载；不存在/损坏 → 返回默认 state

        Returns:
            SchedulerState（永远不抛）
        """
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                last_run=data.get("last_run"),
                next_run=data.get("next_run"),
                task_history=data.get("task_history", []),
                run_count=data.get("run_count", 0),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[state] corrupted state file {path}: {e}; using default")
            return cls()

    def record_run(self, results: dict[str, int], next_run_ts: float) -> None:
        """记录一次 run 结果"""
        self.last_run = next_run_ts - 3600  # 调用方传 next_run，回推 last_run
        self.next_run = next_run_ts
        self.run_count += 1
        for name, code in results.items():
            self.task_history.append({
                "name": name,
                "code": code,
                "ts": self.last_run,
            })
        # 保留最近 100 条历史（避免文件无限增长）
        if len(self.task_history) > 100:
            self.task_history = self.task_history[-100:]


__all__ = ["SchedulerState"]
