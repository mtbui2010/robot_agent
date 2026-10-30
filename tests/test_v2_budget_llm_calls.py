"""v2 verify-budget, P1.14: the run's LLM-call cap and the gate's edit-round stop (core/planning/budget.py,
subgoal_repair.repair_plan)."""

from robot_agent.contract import DEFAULT_RECOVERY, FailureCode, classify_legacy_failure
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.budget import DEFAULT_LLM_BUDGET, EditProgress, budget_end
from robot_agent.core.planning.robot import CORE_TEXT
from robot_agent.core.planning.understand import LLMBudget, LLMBudgetExceeded

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DS, DT, CT, DR = "desk@study room", "dining table@kitchen", "counter@kitchen", "drawer@kitchen"
ANYWHERE = {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "on", "object": "apple", "from": "", "to": "dining table"}]}


def test_edit_progress_stops_when_findings_do_not_decrease():
    p = EditProgress()
    assert not p.stalled(3) and not p.stalled(2) and p.stalled(2)
    q = EditProgress()
    assert not q.stalled(1) and q.stalled(4)


def test_budget_end_fields():
    b = LLMBudget(20)
    b.used = 20

    class R:
        i18n = type("I", (), {"phrases": {"llm_budget": {"ko": "중단합니다"}}})()

        def text(self, key, lang=None):
            return "중단합니다" if (lang or "")[:2] == "ko" else CORE_TEXT[key]
    d = budget_end(R(), b, "ko")
    assert d["code"] == "budget_exceeded" and d["device"] == "llm" and d["say"] == "중단합니다" and d["localized"]
    assert d["evidence"] == {"llm_calls": 20, "limit": 20}
    assert budget_end(R(), b, "vi")["localized"] is False


def test_budget_code_is_typed_and_never_retried():
    rule = DEFAULT_RECOVERY[FailureCode.BUDGET_EXCEEDED]
    assert not rule.retry_allowed
    try:
        LLMBudget(1).take(2, what="replan")
    except LLMBudgetExceeded as e:
        assert classify_legacy_failure(str(e)) is FailureCode.BUDGET_EXCEEDED


def test_default_budget_is_20_and_the_env_overrides(make_assist, env, monkeypatch):
    robot = FakeRobot(env, {"apple": DS})
    assert DEFAULT_LLM_BUDGET == 20
    assert make_assist(robot, FakeLLM(), ScriptedPlanner([])).cfg["llm_budget"] == 20
    monkeypatch.setenv("ROBOT_AGENT_LLM_BUDGET", "7")
    assert make_assist(robot, FakeLLM(), ScriptedPlanner([])).cfg["llm_budget"] == 7


class Wandering(ScriptedPlanner):
    """Each plan looks for the apple somewhere it is not."""

    def __init__(self, env):
        super().__init__([])
        self.k, self.PLACES = 0, [k for k in env if "drawer" not in k]

    def _plan(self):
        p = self.PLACES[self.k % len(self.PLACES)]
        self.k += 1
        return steps(f"MoveTo {p}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")

    def generate_plan(self, task, obs, visible_objects):
        self.calls.append(("generate_plan",))
        return self._plan(), {"method": "wandering"}

    def replan(self, *a, **k):
        self.calls.append(("replan",))
        return self._plan(), {"method": "wandering"}


def test_bad_plans_forever_stop_at_the_cap_with_the_code_and_phrase(make_assist, env):
    robot = FakeRobot(env, {"apple": "nowhere"})
    llm, planner = FakeLLM(default=ANYWHERE), Wandering(env)
    loop = make_assist(robot, llm, planner, max_replans=1000, search_max=1000, ask_help=False)
    d = done(run_task(loop, "Put the apple on the dining table.", max_events=5000))
    assert d["status"] == "failed" and d["code"] == "budget_exceeded" and d["device"] == "llm"
    assert d["say"] == CORE_TEXT["llm_budget"]
    assert len(llm.calls) + len(planner.calls) == 20 == d["evidence"]["llm_calls"]


def test_a_small_budget_caps_it_earlier(make_assist, env):
    robot = FakeRobot(env, {"apple": "nowhere"})
    llm, planner = FakeLLM(default=ANYWHERE), Wandering(env)
    loop = make_assist(robot, llm, planner, max_replans=1000, search_max=1000, ask_help=False, llm_budget=5)
    d = done(run_task(loop, "Put the apple on the dining table."))
    assert d["code"] == "budget_exceeded" and len(llm.calls) + len(planner.calls) == 5


# ── the gate's sub-goal edits ──
class Block:
    def __init__(self, name, steps_):
        self.name, self.steps = name, steps_


class Sage:
    """A planner with the SubgoalRepair capability; each repair returns the next item of ``fixes``."""

    def __init__(self, blocks, fixes):
        self._blocks, self.fixes, self.calls = blocks, list(fixes), []

    def blocks(self):
        return self._blocks

    def join_blocks(self, blocks):
        return [dict(s) for b in blocks for s in b.steps]

    def repair_block(self, block, feedback, *, task, state=None, obs=""):
        self.calls.append(feedback)
        return self.fixes.pop(0) if self.fixes else block.steps


BAD = steps(f"MoveTo {DT}", "Find orange", "Pick orange", f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}")
GOALS = [{"type": "in", "object": "orange", "from": "", "to": DR}]


def _edit(make_assist, env, sage, budget=None, rounds=3):
    from robot_agent.core.planning.assist_loop import Places
    loop = make_assist(FakeRobot(env, {"orange": DT}), FakeLLM(), ScriptedPlanner([]), edit_rounds=rounds)
    ground = lambda seq: [dict(s, grounded=True) for s in seq]                 # noqa: E731
    return loop._repair_plan(sage, "orange in the drawer", "", ground, WorldState(), Places(env, openable=loop.robot.openable), GOALS,
                           "in orange to drawer", budget=budget)


def test_edits_stop_when_the_findings_do_not_decrease(make_assist, env):
    sage = Sage([Block("orange in drawer", [dict(s) for s in BAD])], fixes=[])
    _plan, notes = _edit(make_assist, env, sage)
    assert len(sage.calls) == 1 and any("edits stopped" in n for n in notes)


def test_edits_go_on_while_the_findings_decrease(make_assist, env):
    worse = steps(f"MoveTo {DT}", "Pick orange", f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}")   # 2 findings
    half = [dict(s) for s in BAD]                                                                # 1 finding
    good = steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DT}", "Find orange", "Pick orange", f"MoveTo {DR}",
                 f"PutIn {DR}")
    sage = Sage([Block("orange in drawer", worse)], fixes=[half, good])
    plan, _notes = _edit(make_assist, env, sage)
    assert len(sage.calls) == 2 and [s["action"] for s in plan] == [s["action"] for s in good]


def test_each_edit_takes_the_budget(make_assist, env):
    sage = Sage([Block("orange in drawer", [dict(s) for s in BAD])], fixes=[])
    b = LLMBudget(0)
    _edit(make_assist, env, sage, budget=b)
    assert b.used == len(sage.calls) == 1
    try:
        _edit(make_assist, env, Sage([Block("x", [dict(s) for s in BAD])], fixes=[]), budget=LLMBudget(1))
    except LLMBudgetExceeded:
        raise AssertionError("one edit fits a budget of 1")
    full = LLMBudget(1)
    full.used = 1
    try:
        _edit(make_assist, env, Sage([Block("x", [dict(s) for s in BAD])], fixes=[]), budget=full)
        raise AssertionError("an edit past the cap must raise LLMBudgetExceeded")
    except LLMBudgetExceeded:
        pass
