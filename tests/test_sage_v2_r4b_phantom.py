"""R4b item 3 (phantom pick): a Pick that finds nothing where a Find just saw the object is a search miss, not a
grasp failure. Synthetic places and objects only."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.search import SearchBelief, SearchConfig


def understood(*goals):
    return {"status": "clear", "reason": "", "reason_type": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


PLACES = ["a@r1", "b@r1", "c@r2", "d@r2"]


def belief(rate=0.1, now=1000.0):
    return SearchBelief(PLACES, SearchConfig(miss_rate=rate, rank="prior"), now=lambda: now)


def test_restore_search_takes_back_a_false_positive():
    b = belief(0.1)
    b.missed("cup", "a@r1", [])
    b.found("cup", "b@r1")                         # a Find that saw it at b ...
    assert b.restore_search("cup") and b.is_searched("cup", "a@r1") and b.misses["cup"] == {"a@r1": 1}
    assert not b.restore_search("cup")             # ... once


# ── the loop ──
A, B, C = "counter@kitchen", "desk@study room", "sofa@living room"


class PhantomRobot(FakeRobot):
    """The first Find at `phantom_at` sees the object although it is elsewhere; the Pick then finds nothing
    (code absent, as a pack reports it)."""

    def __init__(self, *a, phantom_at=None, phantoms=1, **k):
        super().__init__(*a, **k)
        self.phantom_at, self.phantom_left = phantom_at, phantoms
        self.pick_calls = 0

    def _exec(self, skill, o, params):
        if skill == "find" and self.at == self.phantom_at and self.phantom_left:
            self.phantom_left -= 1
            self.phantom = True
            return {"isdone": True, "ins": {o: {"pose_3d": [0.6, 0.0, 0.8]}}}
        if skill == "pick":
            self.pick_calls += 1
            if getattr(self, "phantom", False) and self._match(o) and not self._visible_here(self._match(o)):
                return {"isdone": False, "msg": f"{o} not found here", "code": "absent"}
        return super()._exec(skill, o, params)


@pytest.mark.parametrize("phantoms,kind", [(1, "relook"), (2, "phantom")])
def test_phantom_pick_is_a_search_miss_not_a_grasp_failure(make_assist, env, phantoms, kind):
    """One phantom: the look in place misses it (a plain Find miss). Two (the false positive repeats): the place
    is taken as a miss without more looks. Either way no grasp retry and no "move it closer"."""
    robot = PhantomRobot(env, {"apple": B}, phantom_at=A, phantoms=phantoms)
    llm = FakeLLM(understood(("on", "apple", "", "dining table")), default="{}")
    DT = "dining table"
    planner = ScriptedPlanner(steps(f"MoveTo {A}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner, search_legs=1, search_rank="prior", search_max=3, search_total=8),
                   "Put the apple on the dining table.", max_events=3000)
    assert done(evs)["status"] == "success", done(evs)
    kinds = [e.get("kind") for e in evs if e["event"] == "recover"]
    assert kind in kinds and "retry" not in kinds and "asked" not in kinds     # no grasp retry
    assert not any("closer to the edge" in q for q in robot.asked)       # no "move it closer"
    assert robot.pick_calls == phantoms + 1         # the phantoms and the real grasp, nothing else


def test_pick_absent_without_search_legs_is_as_before(make_assist, env):
    robot = PhantomRobot(env, {"apple": B}, phantom_at=A)
    llm = FakeLLM(understood(("on", "apple", "", "dining table")), default="{}")
    planner = ScriptedPlanner(steps(f"MoveTo {A}", "Find apple", "Pick apple"))
    evs = run_task(make_assist(robot, llm, planner, search_legs=0, search_rank="prior", search_max=1),
                   "Put the apple on the dining table.", max_events=3000)
    assert not any(e.get("kind") == "phantom" for e in evs if e["event"] == "recover")
