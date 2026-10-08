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
from datetime import UTC, date, datetime, timedelta
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


# ====================================================================
# v3.5 PR-6b settle + reflect（基座适配）
# 设计要点：
# - 不引入 TA tradingagents.* 依赖（保持零外部依赖）
# - fetch_closes 抽象：默认 stub（test 时 monkeypatch）；生产注入基座 loader
# - reflect_decision 接受任意 llm_client（duck typing：call(messages).content）
# - settle_pending 走原子写：读所有 → 改内存 → 重写文件（避免行级 lock 复杂度）
# ====================================================================

from . import settle_helper  # noqa: E402  — 内部辅助模块（fetch_closes / call_llm 注入；同包相对导入）


class _SettleError(RuntimeError):
    """settle 流程中拉价或调 LLM 失败时抛，下次重跑跳过这条"""


def reflect_decision(
    decision: str,
    raw_return: float,
    alpha_return: float,
    benchmark: str = "SPY",
    holding_days: int = 5,
    llm_client: Any = None,
) -> str:
    """单条 settled 决策的反思：调用 LLM 生成 2-4 句短文本。

    简化版（对照 TA `Reflector.reflect_on_final_decision`）：
    - 不强制 2-4 句（基座 LLM prompt 可由 caller 控制）
    - 默认空字符串 = 跳过反思（PR-6a 已支持 status=settled + reflection="" 兼容）
    """
    if llm_client is None:
        raise ValueError("llm_client is required for reflect_decision")
    messages = [
        ("system", (
            f"You are a trading analyst reviewing your past decision. "
            f"Outcome covers {holding_days} trading days. "
            f"Write 2-4 sentences: what the alpha shows, which part of the "
            f"thesis is supported/undercut, one concrete lesson. Be terse."
        )),
        ("human", (
            f"Raw return over {holding_days} trading days: {raw_return:+.1%}\n"
            f"Alpha vs {benchmark}: {alpha_return:+.1%}\n\n"
            f"Final Decision:\n{decision}"
        )),
    ]
    return llm_client(messages).content


def _compute_returns(
    entry: dict,
    closes: dict,
    holding_days: int,
) -> tuple[float, float, int, str]:
    """从 {date_str: price} 字典算 raw/alpha/holding_days/resolved。

    简化（对照 TA `fetch_returns`）：
    - 不要求"完整窗口已成交"硬约束——entry + holding_days 后的价一定有
    - resolved = 最后一个 key（即 end 日期）
    """
    if not closes or len(closes) < 2:
        raise _SettleError(f"closes 数据不足：{len(closes)} 个点")
    sorted_dates = sorted(closes.keys())
    start_date, end_date = sorted_dates[0], sorted_dates[-1]
    start_px, end_px = closes[start_date], closes[end_date]
    if start_px <= 0:
        raise _SettleError(f"start price 无效：{start_px}")
    raw_return = (end_px - start_px) / start_px
    # alpha 简化：单 ticker 暂不算基准差异（v3 后续 PR 接入 benchmark_map preset）
    alpha_return = 0.0
    return raw_return, alpha_return, holding_days, end_date


def _should_settle(entry: dict, today: str | None = None) -> bool:
    """判定 pending entry 是否到了可结算时间。

    简化：entry 日期 ≤ today - holding_days（默认 5）才结算。
    """
    if entry.get("status") != "pending":
        return False
    trade = date.fromisoformat(entry["trade_date"])
    hold = entry.get("holding_days") or 5
    asof = date.fromisoformat(today) if today else date.today()
    return (asof - trade).days >= hold


class _SettleWriter:
    """原子重写 JSONL 文件（按已解析 entries 改回 dict）"""

    @staticmethod
    def rewrite(path: Path, entries: list[dict]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(path)


# 扩展 DecisionLog：settle_pending 方法
def _settle_pending(
    self: DecisionLog,
    holding_days: int = 5,
    benchmark: str = "SPY",
    reflect: bool = True,
    llm_client: Any = None,
    today: str | None = None,
) -> int:
    """遍历 pending 条目，够老则拉实价 + 算收益 + 写反思 + 标 settled。

    Returns: 实际 settle 数量
    """
    entries = self.load_entries()
    if not entries:
        return 0
    settled_count = 0
    for entry in entries:
        if not _should_settle(entry, today=today):
            continue
        trade_date = entry["trade_date"]
        ticker = entry["ticker"]
        end = _calc_end_date(trade_date, holding_days)
        try:
            closes = settle_helper.fetch_closes(ticker, trade_date, end)
            raw, alpha, hold, resolved = _compute_returns(
                entry, closes, holding_days
            )
        except _SettleError as e:
            # 拉价失败 → 保留 pending，下次重试
            continue
        except Exception:
            # 任何意外 → 保留 pending，不污染
            continue

        entry["raw_return"] = raw
        entry["alpha_return"] = alpha
        entry["holding_days"] = hold
        entry["resolved"] = resolved
        entry["status"] = "settled"

        if reflect:
            try:
                entry["reflection"] = reflect_decision(
                    decision=entry["decision"],
                    raw_return=raw,
                    alpha_return=alpha,
                    benchmark=benchmark,
                    holding_days=hold,
                    llm_client=llm_client or settle_helper.call_llm,
                )
            except Exception:
                # 反思失败不阻塞 settle
                entry["reflection"] = ""
        settled_count += 1

    if settled_count > 0:
        # 重新解析 + 写回（含未 settle 的）
        _SettleWriter.rewrite(self._path, entries)
    return settled_count


def _calc_end_date(trade_date: str, holding_days: int) -> str:
    """end = trade_date + holding_days * 7/5 + 7（TA 公式：5 交易日 ≈ 7 日历日）"""
    start = date.fromisoformat(trade_date)
    end = start + timedelta(days=round(holding_days * 7 / 5) + 7)
    return end.isoformat()


# 绑定方法到 DecisionLog
DecisionLog.settle_pending = _settle_pending  # type: ignore[attr-defined]
