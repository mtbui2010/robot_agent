"""A run that brings typed goals the SAGE loop takes does not translate its prompt (the prompt is only the caller's label of the goals)."""
import pytest

from robot_agent.core.unified_agent import _goals_replace_translation

GOALS = [{"type": "on", "object": "cup", "to": "table@room"}]


def test_goals_accepted_by_sage_skip_the_translation():
    assert _goals_replace_translation("sage", {"goals": GOALS, "accept_goals": "1"}) is True
    assert _goals_replace_translation("sage", {"goals": GOALS, "accept_goals": True}) is True
    assert _goals_replace_translation("sage", {"goals": GOALS, "accept_goals": "true"}) is True


@pytest.mark.parametrize("planner,options", [
    ("sage", {}),                                               # no goals: the prompt is the request
    ("sage", None),
    ("sage", {"goals": [], "accept_goals": "1"}),               # empty goals
    ("sage", {"goals": GOALS}),                                 # accept_goals not named: the loop may ignore the goals
    ("sage", {"goals": GOALS, "accept_goals": "0"}),
    ("sage", {"goals": GOALS, "accept_goals": ""}),
    ("sage", {"goals": "on cup", "accept_goals": "1"}),         # goals must be a list
    ("sage_plain", {"goals": GOALS, "accept_goals": "1"}),      # only the AssistLoop takes goals: the plain loop reads the prompt
    ("direct", {"goals": GOALS, "accept_goals": "1"}),
    (None, {"goals": GOALS, "accept_goals": "1"}),
])
def test_everything_else_translates_as_before(planner, options, monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_PLANNER", raising=False)
    assert _goals_replace_translation(planner, options) is False


def test_the_env_planner_counts_when_no_planner_is_given(monkeypatch):
    monkeypatch.setenv("ROBOT_AGENT_PLANNER", "sage")
    assert _goals_replace_translation(None, {"goals": GOALS, "accept_goals": "1"}) is True
    monkeypatch.setenv("ROBOT_AGENT_PLANNER", "direct")
    assert _goals_replace_translation(None, {"goals": GOALS, "accept_goals": "1"}) is False
