"""v3.5 PR-8 Risk 3 辩论者 测试（红绿重构循环）

设计目标：
- 3 debator (aggressive / conservative / neutral) 各自基于 investment_plan 给出风险评估
- 不引入 TA 多轮辩论（v3 简化为单次表态）
- 投票汇总：aggressive / conservative / neutral → 最终评级
- 接 PR-7 research_manager 输出（investment_plan）
- 接 PR-6a decision_log

v3 简化（对照 TA）：
- 不调 langchain + state 多轮传递
- 不引入 LangGraph risk_debate_state
- 纯函数：debator(investment_plan) → risk_assessment
- 投票：majority vote → final risk rating
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ====================================================================
# Mock helpers
# ====================================================================

def _mock_llm_aggressive(messages: list[tuple[str, str]]) -> object:
    """mock LLM — aggressive 偏向高风险高收益"""
    return type("R", (), {"content": "AGGRESSIVE: 看好，目标价 +25%，可加大仓位 80%，止损 -8%，风险等级 HIGH"})()


def _mock_llm_conservative(messages: list[tuple[str, str]]) -> object:
    """mock LLM — conservative 偏向低风险"""
    return type("R", (), {"content": "CONSERVATIVE: 风险偏高，建议减仓至 30%，止损 -3%，目标价 +10%，风险等级 LOW 保守"})()


def _mock_llm_neutral(messages: list[tuple[str, str]]) -> object:
    """mock LLM — neutral 平衡"""
    return type("R", (), {"content": "NEUTRAL: 维持当前仓位 50%，止损 -5%，目标价 +15%，风险等级 MEDIUM"})()


# ====================================================================
# Schema 测试
# ====================================================================

class TestRiskSchemas:
    """Risk 评估 Pydantic v2 模型"""

    def test_risk_assessment_required_fields(self) -> None:
        from src.vibe_trading_cn.agents.risk_mgmt.schemas import RiskAssessment
        r = RiskAssessment(
            debator="aggressive",
            investment_plan="STRONG_BUY",
            position_size=0.8,
            stop_loss=-0.05,
            target_upside=0.25,
            risk_rating="HIGH",
            rationale="高风险高收益",
        )
        assert r.debator == "aggressive"
        assert r.risk_rating == "HIGH"

    def test_risk_verdict_vote(self) -> None:
        from src.vibe_trading_cn.agents.risk_mgmt.schemas import RiskAssessment, RiskVerdict
        assessments = [
            RiskAssessment(debator="aggressive", investment_plan="x",
                           position_size=0.8, stop_loss=-0.05, target_upside=0.25,
                           risk_rating="HIGH", rationale="..."),
            RiskAssessment(debator="conservative", investment_plan="x",
                           position_size=0.3, stop_loss=-0.03, target_upside=0.10,
                           risk_rating="LOW", rationale="..."),
            RiskAssessment(debator="neutral", investment_plan="x",
                           position_size=0.5, stop_loss=-0.04, target_upside=0.15,
                           risk_rating="MEDIUM", rationale="..."),
        ]
        v = RiskVerdict.from_assessments(assessments, llm_client=None)
        assert v.aggressive.risk_rating == "HIGH"
        assert v.conservative.risk_rating == "LOW"
        assert v.neutral.risk_rating == "MEDIUM"
        assert v.final_risk_rating in {"HIGH", "MEDIUM", "LOW"}
        assert 0.0 < v.final_position_size <= 1.0


# ====================================================================
# 3 Debator 单元测试
# ====================================================================

class TestDebators:
    """aggressive / conservative / neutral debator 各自行为"""

    def test_aggressive_debator_returns_high_risk(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.risk_mgmt import aggressive_debator
        monkeypatch.setattr(aggressive_debator, "call_llm", _mock_llm_aggressive)
        r = aggressive_debator.evaluate(
            investment_plan="STRONG_BUY at 65000, target 70000",
        )
        assert r.debator == "aggressive"
        assert r.risk_rating == "HIGH"
        assert r.target_upside >= 0.15

    def test_conservative_debator_returns_low_risk(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.risk_mgmt import conservative_debator
        monkeypatch.setattr(conservative_debator, "call_llm", _mock_llm_conservative)
        r = conservative_debator.evaluate(
            investment_plan="STRONG_BUY at 65000, target 70000",
        )
        assert r.debator == "conservative"
        assert r.risk_rating == "LOW"
        assert r.stop_loss >= -0.10  # 较紧的止损

    def test_neutral_debator_returns_medium_risk(self, monkeypatch) -> None:
        from src.vibe_trading_cn.agents.risk_mgmt import neutral_debator
        monkeypatch.setattr(neutral_debator, "call_llm", _mock_llm_neutral)
        r = neutral_debator.evaluate(
            investment_plan="STRONG_BUY at 65000, target 70000",
        )
        assert r.debator == "neutral"
        assert r.risk_rating == "MEDIUM"
        assert 0.3 <= r.position_size <= 0.7

    def test_debator_handles_llm_failure_gracefully(self, monkeypatch) -> None:
        """LLM 抛错 → 返回 MEDIUM 降级（不抛）"""
        from src.vibe_trading_cn.agents.risk_mgmt import aggressive_debator
        def boom(*a, **k):
            raise RuntimeError("LLM 限流")
        monkeypatch.setattr(aggressive_debator, "call_llm", boom)
        r = aggressive_debator.evaluate(investment_plan="BUY")
        assert r.risk_rating == "MEDIUM"
        assert "失败" in r.rationale or "降级" in r.rationale


# ====================================================================
# 投票 / 汇总测试
# ====================================================================

class TestRiskVerdict:
    """3 debator 投票 → RiskVerdict"""

    def test_majority_high_risk_wins(self, monkeypatch) -> None:
        """2 HIGH + 1 LOW → final= HIGH（majority 票）"""
        from src.vibe_trading_cn.agents.risk_mgmt import (
            aggressive_debator, conservative_debator, neutral_debator, turn,
        )
        monkeypatch.setattr(aggressive_debator, "call_llm", _mock_llm_aggressive)
        monkeypatch.setattr(conservative_debator, "call_llm", _mock_llm_aggressive)
        monkeypatch.setattr(neutral_debator, "call_llm", _mock_llm_conservative)
        v = turn.run_all(investment_plan="STRONG_BUY")
        # 2 HIGH + 1 LOW → 多数票 HIGH（无 2 个 LOW 触发保守）
        assert v.final_risk_rating == "HIGH"

    def test_two_low_overrides_single_high(self, monkeypatch) -> None:
        """2 LOW + 1 HIGH → final= LOW（保守优先：2 个保守派占优）"""
        from src.vibe_trading_cn.agents.risk_mgmt import (
            aggressive_debator, conservative_debator, neutral_debator, turn,
        )
        monkeypatch.setattr(aggressive_debator, "call_llm", _mock_llm_aggressive)
        monkeypatch.setattr(conservative_debator, "call_llm", _mock_llm_conservative)
        monkeypatch.setattr(neutral_debator, "call_llm", _mock_llm_conservative)
        v = turn.run_all(investment_plan="STRONG_BUY")
        assert v.final_risk_rating == "LOW"

    def test_split_3_ways_defaults_to_medium(self, monkeypatch) -> None:
        """HIGH + LOW + MEDIUM → final=MEDIUM（无 majority）"""
        from src.vibe_trading_cn.agents.risk_mgmt import (
            aggressive_debator, conservative_debator, neutral_debator, turn,
        )
        monkeypatch.setattr(aggressive_debator, "call_llm", _mock_llm_aggressive)
        monkeypatch.setattr(conservative_debator, "call_llm", _mock_llm_conservative)
        monkeypatch.setattr(neutral_debator, "call_llm", _mock_llm_neutral)
        v = turn.run_all(investment_plan="STRONG_BUY")
        assert v.final_risk_rating == "MEDIUM"


# ====================================================================
# 端到端：PR-7 research_manager → PR-8 risk_mgmt → PR-6a decision_log
# ====================================================================

class TestEndToEnd:
    """投资计划 → risk 评估 → 决策记忆"""

    def test_full_risk_pipeline_appends_to_decision_log(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from src.vibe_trading_cn.agents.risk_mgmt import (
            aggressive_debator, conservative_debator, neutral_debator, turn,
        )
        from src.vibe_trading_cn.decision_log import DecisionLog

        log_path = tmp_path / "decision_log.jsonl"
        monkeypatch.setenv("VIBE_DECISION_LOG_PATH", str(log_path))

        monkeypatch.setattr(aggressive_debator, "call_llm", _mock_llm_aggressive)
        monkeypatch.setattr(conservative_debator, "call_llm", _mock_llm_conservative)
        monkeypatch.setattr(neutral_debator, "call_llm", _mock_llm_neutral)

        investment_plan = "INVESTMENT_PLAN: 综合 4 报告看多，建议 STRONG_BUY，目标价 +15%"
        v = turn.run_all(investment_plan=investment_plan)

        log = DecisionLog()
        # 风险评估写入 decision_log（含 position_size + stop_loss + target）
        risk_summary = (
            f"RISK_VERDICT: {v.final_risk_rating} | "
            f"position={v.final_position_size:.0%} | "
            f"stop_loss={v.final_stop_loss:.0%} | "
            f"target={v.final_target_upside:.0%}"
        )
        full_decision = f"{investment_plan}\n\n{risk_summary}"
        appended = log.append(
            ticker="BTCUSDT", trade_date="2026-10-07",
            decision=full_decision, rating="STRONG",
        )
        assert appended is True
        entries = log.load_entries()
        assert "RISK_VERDICT" in entries[0]["decision"]
        assert entries[0]["rating"] == "STRONG"
