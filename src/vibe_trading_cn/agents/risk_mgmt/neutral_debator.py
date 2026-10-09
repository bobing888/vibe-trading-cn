"""v3.5 PR-8 Neutral Debator — 平衡视角

v3 简化（对照 TA neutral_debator.py 4075 B ≈ 135 行）：
- 不调 langchain
- 不读 risk_debate_state.history
- 单次 LLM 调用 + 注入 investment_plan → parse
"""

from __future__ import annotations

from ._base import call_llm, parse_risk
from .schemas import RiskAssessment


SYSTEM_PROMPT = (
    "You are the Neutral Risk Analyst. You weigh both upside and downside. "
    "Given an investment plan, write 2-3 sentences presenting a balanced view, "
    "and explicitly state: 仓位X%, 止损-X%, 目标+X%, 风险等级 HIGH/MEDIUM/LOW."
)


def evaluate(investment_plan: str) -> RiskAssessment:
    """评估投资计划 → 风险评估（neutral 视角）"""
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", f"Investment plan:\n{investment_plan}"),
    ]

    try:
        result = call_llm(messages)
        content = result.content
        parsed = parse_risk(content, "neutral")
    except Exception as e:
        return RiskAssessment(
            debator="neutral",
            investment_plan=investment_plan,
            position_size=0.5, stop_loss=-0.05, target_upside=0.15,
            risk_rating="MEDIUM",
            rationale=f"neutral 评估失败（{type(e).__name__}），已降级为 MEDIUM",
        )

    return RiskAssessment(
        debator="neutral",
        investment_plan=investment_plan,
        position_size=parsed["position_size"],
        stop_loss=parsed["stop_loss"],
        target_upside=parsed["target_upside"],
        risk_rating=parsed["risk_rating"],
        rationale=parsed["rationale"],
    )


__all__ = ["evaluate"]
