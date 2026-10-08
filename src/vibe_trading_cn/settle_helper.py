"""v3.5 PR-6b settle_helper — fetch_closes / call_llm 注入点

设计要点：
- 纯 stub 默认实现（test 时 monkeypatch 替换）
- 生产注入：
  - fetch_closes → 基座 loader（v3 后续 PR 接入；当前 PR 用 stub 保持零依赖）
  - call_llm → 基座 LLMClient（duck typing：call(messages).content）
- 不导入 tradingagents.*（保持零外部依赖，PR-6b 不引入 TA 库）
"""

from __future__ import annotations

from typing import Any, Callable


def fetch_closes(ticker: str, start_date: str, end_date: str) -> dict[str, float]:
    """拉 [start_date, end_date] 区间日线收盘价 → {date_str: price}

    默认 stub：测试必须 monkeypatch；生产由基座 loader 适配。
    返回空 dict 时 settle_pending 视作"拉价失败"，保留 pending。
    """
    return {}


def call_llm(messages: list[tuple[str, str]]) -> Any:
    """调 LLM 生成反思文本。

    默认 stub：返回空字符串（PR-6b 测试用 monkeypatch）。
    生产应注入基座 LLMClient（duck typing：call(messages) → obj with .content）。
    """
    class _Stub:
        content = ""
    return _Stub()


__all__ = ["fetch_closes", "call_llm"]
