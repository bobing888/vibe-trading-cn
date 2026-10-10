"""v3.5 PR-10 cli_scheduler — scheduler CLI 入口

用法：
    python -m src.vibe_trading_cn.cli_scheduler --once           # 跑一次 + 退出
    python -m src.vibe_trading_cn.cli_scheduler --interval 600   # 自定义间隔（秒）
    python -m src.vibe_trading_cn.cli_scheduler                  # 默认：1 小时循环（前台）

设计：
- --once: 单次模式（适合 cron / 测试）
- 无参数: 前台阻塞运行（Ctrl-C 停止）
- v3.6+ 考虑：daemonize / systemd / launchd 集成
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from .scheduler import Scheduler
from .scheduler_state import SchedulerState


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v3.5 scheduler")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--interval", type=int, default=3600,
                        help="interval in seconds (default 3600 = 1 hour)")
    parser.add_argument("--task", action="append", default=[],
                        help="task to run (default: all). e.g. --task settle_pending")
    parser.add_argument("--state-path", type=str, default=None,
                        help="path to scheduler state JSON (v3.5 PR-11: persist across restarts)")
    args = parser.parse_args(argv)

    state_path = Path(args.state_path) if args.state_path else None
    # 默认任务 + state 路径
    s = Scheduler.default()
    s.interval_seconds = args.interval  # 覆盖默认
    s._state_path = state_path  # 注入 state 路径
    s._state = SchedulerState.load_or_default(state_path) if state_path else None

    # 任务过滤（默认全部；--task 指定子集）
    if args.task:
        all_names = s.task_names()
        for name in all_names:
            if name not in args.task:
                # 移除未指定任务
                with s._lock:
                    s._tasks.pop(name, None)

    print(f"[cli-scheduler] tasks: {s.task_names()}")
    print(f"[cli-scheduler] interval: {s.interval_seconds}s")

    if args.once:
        # 单次模式
        results = s.run_once()
        for name, code in results.items():
            print(f"[cli-scheduler] task {name!r} returned {code}")
        return 0

    # 前台阻塞模式
    def _handle_sigint(signum, frame):
        print("\n[cli-scheduler] received SIGINT, stopping...")
        s.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)
    s.start()
    print("[cli-scheduler] running... (Ctrl-C to stop)")

    # 主线程等 stop 事件
    try:
        while s.is_running():
            s._stop_event.wait(1.0)
    except KeyboardInterrupt:
        s.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
