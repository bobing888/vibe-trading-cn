"""v3.5 PR-8 risk_mgmt 共享：call_llm 注入 + 文本解析

设计要点：
- 复用 PR-7 _base 模式：call_llm 注入点（生产由基座 LLMClient 适配）
- 解析 LLM 输出：position_size / stop_loss / target_upside / risk_rating
- 不引入 tradingagents.* 库
"""

from __future__ import annotations

import re
from typing import Any


def call_llm(messages: list[tuple[str, str]]) -> Any:
    """调 LLM（duck typing：call(messages) → obj with .content）

    默认 stub：测试 monkeypatch；生产注入基座 LLMClient。
    """
    class _Stub:
        content = ""
    return _Stub()


def parse_risk(content: str, debator: str) -> dict:
    """从 LLM 文本解析 risk 参数

    简化（对照 TA 严格 prompt）：正则 + 关键词 fallback
    - position_size: "仓位 X%" / "position X%"
    - stop_loss: "止损 X%" / "stop_loss X%"
    - target_upside: "目标 X%" / "target X%"
    - risk_rating: 关键词（HIGH / MEDIUM / LOW）
    """
    result = {
        "position_size": _default_position(debator),
        "stop_loss": _default_stop(debator),
        "target_upside": _default_target(debator),
        "risk_rating": _default_rating(debator),
        "rationale": content,
    }

    # 仓位
    m = re.search(r"仓位[^\d]*(\d+)\s*%", content)
    if m:
        result["position_size"] = int(m.group(1)) / 100
    else:
        m = re.search(r"position[^\d]*(\d+)\s*%", content, re.IGNORECASE)
        if m:
            result["position_size"] = int(m.group(1)) / 100

    # 止损（负数）
    m = re.search(r"止损[^\d-]*(-?\d+)\s*%", content)
    if m:
        result["stop_loss"] = int(m.group(1)) / 100
    else:
        m = re.search(r"stop[_\s-]?loss[^\d-]*(-?\d+)\s*%", content, re.IGNORECASE)
        if m:
            result["stop_loss"] = int(m.group(1)) / 100

    # 目标涨幅
    m = re.search(r"目标[^\d-]*\+?(\d+)\s*%", content)
    if m:
        result["target_upside"] = int(m.group(1)) / 100
    else:
        m = re.search(r"target[^\d-]*\+?(\d+)\s*%", content, re.IGNORECASE)
        if m:
            result["target_upside"] = int(m.group(1)) / 100

    # risk_rating（关键词）
    upper = content.upper()
    if "HIGH" in upper or "高风险" in content:
        result["risk_rating"] = "HIGH"
    elif "LOW" in upper or "低风险" in content or "保守" in content:
        result["risk_rating"] = "LOW"
    else:
        result["risk_rating"] = "MEDIUM"

    return result


def _default_position(debator: str) -> float:
    return {"aggressive": 0.8, "conservative": 0.3, "neutral": 0.5}.get(debator, 0.5)


def _default_stop(debator: str) -> float:
    return {"aggressive": -0.08, "conservative": -0.03, "neutral": -0.05}.get(debator, -0.05)


def _default_target(debator: str) -> float:
    return {"aggressive": 0.25, "conservative": 0.10, "neutral": 0.15}.get(debator, 0.15)


def _default_rating(debator: str) -> str:
    return {"aggressive": "HIGH", "conservative": "LOW", "neutral": "MEDIUM"}.get(debator, "MEDIUM")


__all__ = ["call_llm", "parse_risk"]
