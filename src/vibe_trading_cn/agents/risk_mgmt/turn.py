"""v3.5 PR-8 turn.py — 3 debator 串行评估 + 投票

v3 简化（对照 TA 没有 risk_turn.py，由 LangGraph risk_debate_state 驱动）：
- 3 debator 串行评估（不并行 — 评估是顺序观点）
- RiskVerdict.from_assessments 投票（保守优先）
- 不引入 langgraph
"""

from __future__ import annotations

from . import aggressive_debator, conservative_debator, neutral_debator
from .schemas import RiskAssessment, RiskVerdict


def run_all(investment_plan: str) -> RiskVerdict:
    """3 debator 串行评估 → 投票汇总

    简化：串行（非并行）— 评估是不同观点的组合，并行无明显加速
    """
    a = aggressive_debator.evaluate(investment_plan)
    c = conservative_debator.evaluate(investment_plan)
    n = neutral_debator.evaluate(investment_plan)
    return RiskVerdict.from_assessments([a, c, n])


__all__ = ["run_all", "RiskAssessment", "RiskVerdict"]
