"""v3.5 PR-8 Conservative Debator — 低风险视角

v3 简化（对照 TA conservative_debator.py 4218 B ≈ 140 行）：
- 不调 langchain
- 不读 risk_debate_state.history
- 单次 LLM 调用 + 注入 investment_plan → parse
"""

from __future__ import annotations

from ._base import call_llm, parse_risk
from .schemas import RiskAssessment


SYSTEM_PROMPT = (
    "You are the Conservative Risk Analyst. You protect capital and prefer "
    "tight stops. Given an investment plan, write 2-3 sentences arguing for "
    "CAUTION, and explicitly state: 仓位X%, 止损-X%, 目标+X%, 风险等级 HIGH/MEDIUM/LOW."
)


def evaluate(investment_plan: str) -> RiskAssessment:
    """评估投资计划 → 风险评估（conservative 视角）"""
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", f"Investment plan:\n{investment_plan}"),
    ]

    try:
        result = call_llm(messages)
        content = result.content
        parsed = parse_risk(content, "conservative")
    except Exception as e:
        return RiskAssessment(
            debator="conservative",
            investment_plan=investment_plan,
            position_size=0.3, stop_loss=-0.03, target_upside=0.10,
            risk_rating="LOW",
            rationale=f"conservative 评估失败（{type(e).__name__}），已降级为 LOW",
        )

    return RiskAssessment(
        debator="conservative",
        investment_plan=investment_plan,
        position_size=parsed["position_size"],
        stop_loss=parsed["stop_loss"],
        target_upside=parsed["target_upside"],
        risk_rating=parsed["risk_rating"],
        rationale=parsed["rationale"],
    )


__all__ = ["evaluate"]
