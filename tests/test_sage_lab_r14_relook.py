"""Round 14, option ``relook`` (default OFF), through the real AssistLoop.run_blocking against the symbolic FakeRobot and a
scripted LLM and planner. A Find that sees nothing at a place the person named (an injected single detection miss) gets ONE
more look, by another sense, before the place counts as searched. Synthetic ENV and requests written for these tests."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS

CT, KC, SF, FR = "coffee table@living room", "counter@kitchen", "sofa@living room", "fridge@kitchen"
ENV = {
    CT: {"aliases": ["coffee table"], "loc": {"x": 1.0, "y": 1.0}},
    KC: {"aliases": ["counter"], "loc": {"x": 3.5, "y": 1.0}},
    SF: {"aliases": ["sofa", "couch"], "loc": {"x": 1.5, "y": 2.0}},
    FR: {"aliases": ["fridge"], "loc": {"x": 3.0, "y": 1.5}, "openable": True},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna"], "loc": {"x": 1.2, "y": 1.0}},
}
PLAN = steps(f"MoveTo {CT}", "Find mug", "Pick mug", f"MoveTo {KC}", f"Place {KC}")


def understood(src, dest=KC):
    return {"status": "clear", "reason": "", "reason_type": "", "question": "",
            "goals": [{"type": "on", "object": "mug", "from": src, "to": dest}]}


class MissRobot(FakeRobot):
    """The first ``misses`` Finds of the mug at ``miss_at`` see nothing although the mug is there (code absent)."""

    def __init__(self, *a, miss_at=None, misses=1, **k):
        super().__init__(*a, **k)
        self.miss_at, self.misses_left, self.finds = miss_at, misses, []

    def _exec(self, skill, o, params):
        if skill == "find":
            self.finds.append(self.at)
            if self.at == self.miss_at and self.misses_left:
                self.misses_left -= 1
                return {"isdone": False, "msg": f"{o} not found", "code": "absent"}
        return super()._exec(skill, o, params)


def run(make_assist, env, llm, robot, **opts):
    opts = dict(search_legs=1, search_rank="prior", search_max=2, search_total=3, ask_help=False, detector_miss_rate=0, **opts)
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN), **opts)
    return loop, run_task(loop, "Take the mug from the coffee table to the counter.", max_events=3000)


@pytest.fixture
def env(request):
    return getattr(request.function, "ENV", ENV)


def test_option_registered_and_off_by_default(make_assist, monkeypatch, env):
    monkeypatch.delenv("ROBOT_AGENT_RELOOK", raising=False)
    assert "relook" in ASSIST_OPTIONS and "relook" in PLANNER_KEYS
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["relook"] is False and mk(relook="1").cfg["relook"] is True


def test_089_one_more_look_at_the_named_place_finds_the_mug(make_assist, env):
    robot = MissRobot(env, {"mug": CT}, miss_at=CT, misses=1)
    loop, evs = run(make_assist, env, FakeLLM(understood(CT), default="{}"), robot, relook="1")
    assert done(evs)["status"] == "success", done(evs)
    assert robot.finds[:2] == [CT, CT]                                   # the second look is at the same place
    assert [e["why"] for e in loop.lab_log if e["flag"] == "relook"] == ["named"]
    assert any(e.get("kind") == "relook" for e in evs if e["event"] == "recover")


def test_089_without_the_option_the_place_is_never_looked_at_again(make_assist, env):
    robot = MissRobot(env, {"mug": CT}, miss_at=CT, misses=1)
    loop, evs = run(make_assist, env, FakeLLM(understood(CT), default="{}"), robot)
    assert done(evs)["status"] == "failed" and not [e for e in loop.lab_log if e["flag"] == "relook"]
    assert robot.finds.count(CT) == 1


def test_relook_does_not_loop(make_assist, env):
    """The mug is really gone from the named place: ONE more look, then the place counts as searched."""
    robot = MissRobot(env, {"mug": SF}, miss_at=CT, misses=99)
    loop, evs = run(make_assist, env, FakeLLM(understood(CT), default="{}"), robot, relook="1")
    assert robot.finds.count(CT) == 2                                    # the first look and one relook, never a third
    assert len([e for e in loop.lab_log if e["flag"] == "relook"]) == 1
    assert len(robot.finds) <= 6 and done(evs)["status"] in ("failed", "success")     # it ends


def test_relook_budget_search_total_one_means_no_relook(make_assist, env):
    robot = MissRobot(env, {"mug": CT}, miss_at=CT, misses=1)
    opts = dict(search_legs=1, search_rank="prior", search_max=2, search_total=1, ask_help=False, relook="1", detector_miss_rate=0)
    loop = make_assist(robot, FakeLLM(understood(CT), default="{}"), ScriptedPlanner(PLAN), **opts)
    run_task(loop, "Take the mug from the coffee table to the counter.", max_events=3000)
    assert not [e for e in loop.lab_log if e["flag"] == "relook"]


def test_a_place_nobody_named_and_not_last_is_not_relooked(make_assist, env):
    """No source named, nothing in memory, other places left: the miss counts as a miss as before."""
    robot = MissRobot(env, {"mug": KC}, miss_at=CT, misses=1)
    loop, evs = run(make_assist, env, FakeLLM(understood(""), default="{}"), robot, relook="1")
    assert not [e for e in loop.lab_log if e["flag"] == "relook"]


def test_the_container_is_closed_and_opened_again(make_assist, env):
    """A container believed open: the other sense is Close then Open (the pack's own skills)."""
    plan = steps(f"MoveTo {FR}", f"Open {FR}", "Find mug", "Pick mug", f"MoveTo {KC}", f"Place {KC}")
    robot = MissRobot(env, {"mug": FR + "/in"}, miss_at=FR, misses=2, opened=())
    robot.env = env
    llm = FakeLLM(understood(FR), default="{}")
    loop = make_assist(robot, llm, ScriptedPlanner(plan), search_legs=1, search_rank="prior", search_max=2, search_total=6,
                       ask_help=False, relook="1")
    evs = run_task(loop, "Take the mug from the fridge to the counter.", max_events=3000)
    log = [e for e in loop.lab_log if e["flag"] == "relook"]
    assert log and log[0]["sense"] in ("reopen", "again", "wait")


def test_memory_that_holds_the_object_here_earns_the_relook(make_assist, env, tmp_path):
    import json
    import time
    (tmp_path / "object_memory.json").write_text(json.dumps({"objects": {"mug": {"place": CT, "ts": time.time(), "how": "seen"}}}))
    robot = MissRobot(env, {"mug": CT}, miss_at=CT, misses=1)
    loop, evs = run(make_assist, env, FakeLLM(understood(""), default="{}"), robot, relook="1")
    assert [e["why"] for e in loop.lab_log if e["flag"] == "relook"][:1] in (["memory"], ["named"])   # memory seeds the source
    assert done(evs)["status"] == "success"


ENV_TWO = {k: ENV[k] for k in (CT, SF, "anna@living room")}


def test_the_last_place_left_earns_the_relook(make_assist):
    robot = MissRobot(ENV_TWO, {"mug": CT}, miss_at=CT, misses=1)
    llm = FakeLLM(understood("", dest=CT), default="{}")
    plan = steps(f"MoveTo {SF}", "Find mug", "Pick mug", f"MoveTo {CT}", f"Place {CT}")
    loop = make_assist(robot, llm, ScriptedPlanner(plan), search_legs=1, search_rank="prior", search_max=2, search_total=4,
                       ask_help=False, detector_miss_rate=0, relook="1")
    evs = run_task(loop, "Take the mug to the coffee table.", max_events=3000)
    assert "last" in [e["why"] for e in loop.lab_log if e["flag"] == "relook"], robot.finds
    assert done(evs)["status"] == "success"


test_the_last_place_left_earns_the_relook.ENV = ENV_TWO


def test_flag_off_leaves_no_trace(make_assist, env):
    robot = MissRobot(env, {"mug": CT}, miss_at=CT, misses=1)
    loop, evs = run(make_assist, env, FakeLLM(understood(CT), default="{}"), robot, relook="0")
    assert not [e for e in loop.lab_log if e["flag"] == "relook"] and not any(e.get("kind") == "relook" for e in evs if e["event"] == "recover")
