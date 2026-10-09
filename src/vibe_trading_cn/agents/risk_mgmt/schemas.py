"""v3.5 PR-8 Risk 评估 Pydantic v2 schemas

设计要点：
- RiskAssessment：单个 debator 评估（position_size / stop_loss / target / risk_rating）
- RiskVerdict：3 评估聚合 + 最终 rating（保守优先）
- rating 统一：HIGH / MEDIUM / LOW（与 PR-6a/7 评级体系分开）
"""

from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel, Field


_RISK_RATING = Field(default="MEDIUM", pattern=r"^(HIGH|MEDIUM|LOW)$")


class RiskAssessment(BaseModel):
    """单个 debator 风险评估"""

    debator: str  # aggressive / conservative / neutral
    investment_plan: str
    position_size: float = 0.5  # 0.0 - 1.0
    stop_loss: float = -0.05  # 负数
    target_upside: float = 0.10
    risk_rating: str = "MEDIUM"
    rationale: str = ""


class RiskVerdict(BaseModel):
    """3 评估聚合 + 最终风险结论

    投票规则（保守优先）：
    - 3 个都同 → 该 rating
    - 2 HIGH + 1 LOW → LOW（保守）
    - 2 LOW + 1 HIGH → LOW（保守）
    - 其它无 majority → MEDIUM
    """

    aggressive: RiskAssessment
    conservative: RiskAssessment
    neutral: RiskAssessment
    final_risk_rating: str = "MEDIUM"
    final_position_size: float = 0.5
    final_stop_loss: float = -0.05
    final_target_upside: float = 0.10
    vote_summary: str = ""

    @classmethod
    def from_assessments(
        cls,
        assessments: list[RiskAssessment],
        llm_client: Optional[Callable] = None,
    ) -> "RiskVerdict":
        """3 个 assessment → RiskVerdict（投票 + 保守优先）"""
        if len(assessments) != 3:
            raise ValueError(f"need 3 assessments, got {len(assessments)}")
        by_name = {a.debator: a for a in assessments}
        for name in ("aggressive", "conservative", "neutral"):
            if name not in by_name:
                raise ValueError(f"missing debator: {name}")

        ratings = [a.risk_rating for a in assessments]
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for r in ratings:
            counts[r] += 1

        # 投票：多数票胜（≥2 票 → 该 rating）
        if max(counts.values()) >= 2:
            final = max(counts, key=counts.get)
        else:
            # 全不同（HIGH + MEDIUM + LOW）→ MEDIUM
            final = "MEDIUM"

        # 平均 position/stop/target
        avg_pos = sum(a.position_size for a in assessments) / 3
        avg_stop = sum(a.stop_loss for a in assessments) / 3
        avg_target = sum(a.target_upside for a in assessments) / 3

        # 保守修正：final=HIGH → position 减小；final=LOW → position 加大（更保守）
        if final == "HIGH":
            avg_pos = min(avg_pos, 0.4)
        elif final == "LOW":
            avg_pos = max(avg_pos, 0.3)

        return cls(
            aggressive=by_name["aggressive"],
            conservative=by_name["conservative"],
            neutral=by_name["neutral"],
            final_risk_rating=final,
            final_position_size=round(avg_pos, 2),
            final_stop_loss=round(avg_stop, 3),
            final_target_upside=round(avg_target, 2),
            vote_summary=f"HIGH={counts['HIGH']} MEDIUM={counts['MEDIUM']} LOW={counts['LOW']} → {final}",
        )


__all__ = ["RiskAssessment", "RiskVerdict"]
