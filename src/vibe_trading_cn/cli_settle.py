"""v3.5 PR-6b CLI 入口：手动触发 settle_pending

用法：
    python -m src.vibe_trading_cn.cli_settle [--dry-run] [--no-reflect]

设计：
- 默认全量扫描 pending 条目
- --dry-run: 只打印将要 settle 的数量，不写文件
- --no-reflect: 跳过 LLM 反思调用（节省 token / 调试用）
- v3 后续 PR 接入 scheduler 后可省略本 CLI
"""

from __future__ import annotations

import argparse
import sys

from .decision_log import DecisionLog


def main() -> int:
    parser = argparse.ArgumentParser(description="settle pending decision entries")
    parser.add_argument("--dry-run", action="store_true", help="don't write file")
    parser.add_argument("--no-reflect", action="store_true", help="skip LLM reflection")
    parser.add_argument("--holding-days", type=int, default=5)
    parser.add_argument("--benchmark", default="SPY")
    args = parser.parse_args()

    log = DecisionLog()
    pending_before = sum(
        1 for e in log.load_entries() if e.get("status") == "pending"
    )
    print(f"[cli-settle] pending entries: {pending_before}")

    if args.dry_run:
        print(f"[cli-settle] dry-run: would settle up to {pending_before} entries")
        return 0

    settled = log.settle_pending(
        holding_days=args.holding_days,
        benchmark=args.benchmark,
        reflect=not args.no_reflect,
    )
    print(f"[cli-settle] settled: {settled}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
