"""The run's LLM-call budget and the stop rule of the plan gate's edit rounds.

Planner/robot-agnostic. Every core LLM call of a run (understand + its repairs and re-samples,
the planner's generate_plan / replan, each sub-goal edit, the gate's refinement) takes one unit
of ``understand.LLMBudget`` (``llm_budget``, default :data:`DEFAULT_LLM_BUDGET`,
``ROBOT_AGENT_LLM_BUDGET``; 0 = no cap). Past the cap the run ends ``failed`` with
FailureCode BUDGET_EXCEEDED and the robot's phrase ``llm_budget`` (:func:`budget_end`).

The gate's edit rounds stop early when a round leaves as many critical findings as the one
before (:class:`EditProgress`): another round of the same edits would only spend calls.
See docs/verify.md.
"""

from __future__ import annotations

from typing import Any, Optional

from robot_agent.contract import FailureCode

DEFAULT_LLM_BUDGET = 20

# the core's phrase when the robot's i18n has none (RobotView.text falls back to CORE_TEXT)
BUDGET_TEXT = "I have to stop: I could not work this task out within my limit of tries."


class EditProgress:
    """``stalled(n)``: True when ``n`` findings is not fewer than the round before."""

    def __init__(self) -> None:
        self.history: list = []

    def stalled(self, n_findings: int) -> bool:
        stop = bool(self.history) and n_findings >= self.history[-1]
        self.history.append(int(n_findings))
        return stop


def budget_end(robot: Any, budget: Any, lang: Optional[str] = None) -> dict:
    """The ``done`` fields of a run that used its budget: code, device, evidence and the robot's
    ``llm_budget`` phrase in ``lang`` (``localized``: the robot has it in that language, so it is
    not translated again: no LLM call after the budget is used)."""
    say = robot.text("llm_budget", lang) if robot is not None else BUDGET_TEXT
    phrases = ((getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get("llm_budget") or {})
    return {"code": FailureCode.BUDGET_EXCEEDED.value, "device": "llm",
            "evidence": {"llm_calls": getattr(budget, "used", None), "limit": getattr(budget, "limit", None)},
            "say": say, "localized": (lang or "en")[:2] in phrases}


__all__ = ["DEFAULT_LLM_BUDGET", "BUDGET_TEXT", "EditProgress", "budget_end"]
