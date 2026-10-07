"""v3.5 PR-6a 决策记忆 append + load 实现（绿：最少的代码让测试通过）

设计要点：
- JSONL 格式（v3 偏好结构化查询 vs TA markdown）
- 存储路径：~/.vibe-trading/decision_log.jsonl（可通过 VIBE_DECISION_LOG_PATH 重定向）
- 线程安全（threading.Lock）
- 同 (ticker, trade_date) 幂等
- 兼容旧条目（malformed 行跳过，不让单行污染整个文件）
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path("~/.vibe-trading/decision_log.jsonl").expanduser()


@dataclass
class DecisionEntry:
    """单条交易决策记录"""

    ticker: str
    trade_date: str  # YYYY-MM-DD
    decision: str
    rating: str  # STRONG / WEAK / NEUTRAL
    status: str = "pending"  # pending / settled
    raw_return: float | None = None
    alpha_return: float | None = None
    holding_days: int | None = None
    resolved: str | None = None  # YYYY-MM-DD 实际结算日（point-in-time 过滤用）
    reflection: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class DecisionLog:
    """append-only 决策记忆

    写入：append() — pending 状态
    读取：load_entries() / get_past_context()
    """

    def __init__(self, path: Path | str | None = None) -> None:
        env_path = os.environ.get("VIBE_DECISION_LOG_PATH")
        if path is not None:
            self._path = Path(path).expanduser()
        elif env_path:
            self._path = Path(env_path).expanduser()
        else:
            self._path = _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # --- Write ---

    def append(
        self,
        ticker: str,
        trade_date: str,
        decision: str,
        rating: str,
    ) -> bool:
        """append 一条 pending 决策

        Returns: True if appended, False if (ticker, trade_date) already exists
        """
        entry = DecisionEntry(
            ticker=ticker,
            trade_date=trade_date,
            decision=decision,
            rating=rating,
        )
        return self.append_internal(entry)

    def append_internal(self, entry: DecisionEntry | dict) -> bool:
        """append 一条决策（支持 dict 或 DecisionEntry）

        幂等：(ticker, trade_date) 已存在则拒绝。
        """
        if isinstance(entry, dict):
            entry = DecisionEntry(**entry)
        with self._lock:
            if self._exists(entry.ticker, entry.trade_date):
                return False
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            return True

    def _exists(self, ticker: str, trade_date: str) -> bool:
        if not self._path.exists():
            return False
        for line in self._path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ticker") == ticker and row.get("trade_date") == trade_date:
                return True
        return False

    # --- Read ---

    def load_entries(self) -> list[dict[str, Any]]:
        """解析所有 JSONL 条目，malformed 行跳过"""
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def get_past_context(
        self,
        ticker: str,
        n_same: int = 5,
        n_cross: int = 3,
        as_of: str | None = None,
    ) -> str:
        """返回格式化的 past context 字符串，给 agent prompt 注入。

        - 只返回 status=settled 的条目（避免学习未发生的未来）
        - as_of 指定时，只返回 resolved <= as_of 的（point-in-time 过滤）
        """
        entries = [e for e in self.load_entries() if e.get("status") == "settled"]
        if as_of is not None:
            entries = [e for e in entries if e.get("resolved") and e["resolved"] <= as_of]
        if not entries:
            return ""

        same, cross = [], []
        # 倒序：最新的在前
        for e in reversed(entries):
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross:
            return ""

        parts: list[str] = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            for e in same:
                parts.append(self._format_full(e))
        if cross:
            parts.append("Recent cross-ticker lessons:")
            for e in cross:
                parts.append(self._format_reflection_only(e))
        return "\n\n".join(parts)

    @staticmethod
    def _format_full(e: dict) -> str:
        raw = DecisionLog._fmt_pct(e.get("raw_return"))
        alpha = DecisionLog._fmt_pct(e.get("alpha_return"))
        holding = e.get("holding_days", "n/a")
        tag = f"[{e['trade_date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}d]"
        text = f"{tag}\nDECISION:\n{e['decision']}"
        if e.get("reflection"):
            text += f"\nREFLECTION:\n{e['reflection']}"
        return text

    @staticmethod
    def _format_reflection_only(e: dict) -> str:
        raw = DecisionLog._fmt_pct(e.get("raw_return"))
        tag = f"[{e['trade_date']} | {e['ticker']} | {e['rating']} | {raw}]"
        if e.get("reflection"):
            return f"{tag}\n{e['reflection']}"
        decision = e.get("decision", "")
        suffix = "..." if len(decision) > 300 else ""
        return f"{tag}\n{decision[:300]}{suffix}"

    @staticmethod
    def _fmt_pct(v: Any) -> str:
        """仅 float 走百分比格式化，其它原样转 str"""
        if isinstance(v, (int, float)):
            return f"{v:.2%}"
        return str(v) if v is not None else "n/a"
