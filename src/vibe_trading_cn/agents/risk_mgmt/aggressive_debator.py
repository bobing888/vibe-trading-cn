"""v3.5 PR-8 Aggressive Debator — 高风险高收益视角

v3 简化（对照 TA aggressive_debator.py 4315 B ≈ 145 行）：
- 不调 langchain
- 不读 risk_debate_state.history（无多轮辩论）
- 单次 LLM 调用 + 注入 investment_plan → parse
- 失败降级：LLM 抛错 → 返回 MEDIUM 评估
"""

from __future__ import annotations

from ._base import call_llm, parse_risk
from .schemas import RiskAssessment


SYSTEM_PROMPT = (
    "You are the Aggressive Risk Analyst. You champion high-reward opportunities. "
    "Given an investment plan, write 2-3 sentences arguing FOR bold action, "
    "and explicitly state: 仓位X%, 止损-X%, 目标+X%, 风险等级 HIGH/MEDIUM/LOW."
)


def evaluate(investment_plan: str) -> RiskAssessment:
    """评估投资计划 → 风险评估（aggressive 视角）"""
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", f"Investment plan:\n{investment_plan}"),
    ]

    try:
        result = call_llm(messages)
        content = result.content
        parsed = parse_risk(content, "aggressive")
    except Exception as e:
        return RiskAssessment(
            debator="aggressive",
            investment_plan=investment_plan,
            position_size=0.5, stop_loss=-0.05, target_upside=0.15,
            risk_rating="MEDIUM",
            rationale=f"aggressive 评估失败（{type(e).__name__}），已降级为 MEDIUM",
        )

    return RiskAssessment(
        debator="aggressive",
        investment_plan=investment_plan,
        position_size=parsed["position_size"],
        stop_loss=parsed["stop_loss"],
        target_upside=parsed["target_upside"],
        risk_rating=parsed["risk_rating"],
        rationale=parsed["rationale"],
    )


__all__ = ["evaluate"]
