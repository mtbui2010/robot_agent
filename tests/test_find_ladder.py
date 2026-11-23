"""Step S13: the Find ladder (_recover_find, _relook_why, _relook_here, SECOND_LOOKS, _relook_budget, _second_look, _splice_leg,
_said_of, _confirm_near, _search_on, _recover_phantom) moved verbatim from assist_loop.py to the FindLadder mixin in
core/planning/find_ladder.py. These tests prove nothing changed:
(1) the mixin is in AssistLoop's method resolution order, serves the eleven names, and the loop file keeps ``_refuted`` and
``_step_failed``; (2) a differential test: the REAL AssistLoop run end to end against the symbolic FakeRobot with a scripted
LLM and planner, once with the new mixin and once with the verbatim reference copy (tests/legacy_ref/s13_find_ladder.py) put
first in the method resolution order: every event, the lab log, the robot's skill trace and the questions asked are equal, over a
grid of made-up scenarios (misses, a place named by the person, a closed container, a false detection, a missing object, answers
of the person, option combinations); (3) the grid reaches every recover kind the ladder emits. Made-up robots only."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, scrub, steps
from legacy_ref import s13_find_ladder as old
from robot_agent.core.planning import assist_loop, find_ladder
from robot_agent.core.planning.phases import PHASE_MIXINS

NAMES = ("_recover_find", "_relook_why", "_relook_here", "SECOND_LOOKS", "_relook_budget", "_second_look", "_splice_leg",
         "_said_of", "_confirm_near", "_search_on", "_recover_phantom")

CT, KC, SF, FR = "coffee table@living room", "counter@kitchen", "sofa@living room", "fridge@kitchen"
ENV = {
    CT: {"aliases": ["coffee table"], "loc": {"x": 1.0, "y": 1.0}},
    KC: {"aliases": ["counter"], "loc": {"x": 3.5, "y": 1.0}},
    SF: {"aliases": ["sofa", "couch"], "loc": {"x": 1.5, "y": 2.0}},
    FR: {"aliases": ["fridge"], "loc": {"x": 3.0, "y": 1.5}, "openable": True},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna"], "loc": {"x": 1.2, "y": 1.0}},
}
PLAN = steps(f"MoveTo {CT}", "Find mug", "Pick mug", f"MoveTo {KC}", f"Place {KC}")
FRIDGE_PLAN = steps(f"MoveTo {FR}", f"Open {FR}", "Find mug", "Pick mug", f"MoveTo {KC}", f"Place {KC}")
TASK = "Take the mug from the coffee table to the counter."
LEGS = dict(search_legs=1, search_rank="prior", search_max=2, search_total=3, ask_help=False, detector_miss_rate=0)


class OldLoop(old.RefFindLadder, assist_loop.AssistLoop):
    """The loop with the reference copy's methods first in the method resolution order (the old behaviour)."""


@pytest.fixture
def env():
    return ENV


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_eleven_names():
    assert find_ladder.FindLadder in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(find_ladder.FindLadder) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(assist_loop.AssistLoop), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(find_ladder.FindLadder, n), n
    assert find_ladder.FindLadder.SECOND_LOOKS == assist_loop.AssistLoop.SECOND_LOOKS == old.RefFindLadder.SECOND_LOOKS == 2


def test_refuted_and_step_failed_stay_in_the_loop_file():
    for n in ("_refuted", "_step_failed"):
        assert n in vars(assist_loop.AssistLoop) and n not in vars(find_ladder.FindLadder)


def test_no_name_of_the_ladder_is_defined_by_another_mixin_or_base():
    for base in assist_loop.AssistLoop.__mro__[1:]:
        if base is find_ladder.FindLadder:
            continue
        assert not [n for n in NAMES if n in vars(base)], base


def test_reference_copy_and_module_have_the_same_method_bodies():
    import inspect
    for n in NAMES:
        if n == "SECOND_LOOKS":
            continue
        a, b = inspect.getsource(vars(old.RefFindLadder)[n]), inspect.getsource(vars(find_ladder.FindLadder)[n])
        a = a.replace("from robot_agent.core.planning.understand import", "from .understand import")   # the one import of the copy
        assert a == b, n


# -- (2) differential test ----------------------------------------------------------------------------------------------------
def understood(src, dest=KC):
    return {"status": "clear", "reason": "", "reason_type": "", "question": "",
            "goals": [{"type": "on", "object": "mug", "from": src, "to": dest}]}


CASES = {
    "relook_named_place": dict(objects={"mug": CT}, faults=[{"skill": "find", "obj": "mug", "nth": 1, "kind": "fail"}],
                               opts=dict(LEGS, relook="1")),
    "relook_without_option": dict(objects={"mug": CT}, faults=[{"skill": "find", "obj": "mug", "nth": 1, "kind": "fail"}],
                                  opts=dict(LEGS)),
    "relook_never_found": dict(objects={"mug": SF}, faults=[{"skill": "find", "obj": "mug", "nth": [1, 2], "kind": "fail"}],
                               opts=dict(LEGS, relook="1")),
    "next_leg": dict(objects={"mug": SF}, opts=dict(LEGS)),
    "next_leg_no_legs": dict(objects={"mug": SF}, opts=dict(LEGS, search_legs=0)),
    "nowhere": dict(objects={"mug": "attic"}, opts=dict(LEGS)),
    "nowhere_no_legs": dict(objects={"mug": "attic"}, opts=dict(LEGS, search_legs=0, search_max=1)),
    "nowhere_asks_gives_up": dict(objects={"mug": "attic"}, opts=dict(LEGS, ask_help=True, search_total=6),
                                  reply_rules=[{"if": "where is it", "then": ""}]),
    "answer_names_a_place": dict(objects={"mug": SF}, opts=dict(LEGS, ask_help=True, search_total=1, search_max=0),
                                 reply_rules=[{"if": "where is it", "then": "the sofa"}]),
    "answer_dont_know_legs": dict(objects={"mug": SF}, opts=dict(LEGS, ask_help=True, search_total=1, search_max=0),
                                  reply_rules=[{"if": "where is it", "then": ""}]),
    "answer_noisy_place": dict(objects={"mug": SF}, opts=dict(LEGS, ask_help=True, search_total=1, search_max=0),
                               reply_rules=[{"if": "where is it", "then": "the sofah"}, {"if": "did you mean", "then": "yes"}]),
    "answer_noisy_place_refused": dict(objects={"mug": SF}, opts=dict(LEGS, ask_help=True, search_total=1, search_max=0),
                                       reply_rules=[{"if": "where is it", "then": "the sofah"}, {"if": "did you mean", "then": "no"}]),
    "second_looks_high_miss_rate": dict(objects={"mug": SF}, faults=[{"skill": "find", "obj": "mug", "nth": [1, 3], "kind": "fail"}],
                                        opts=dict(LEGS, detector_miss_rate=0.4, search_total=5)),
    "phantom_detection": dict(objects={"mug": SF}, faults=[{"skill": "find", "obj": "mug", "nth": 1, "kind": "false_positive"}, {"skill": "pick", "obj": "mug", "nth": 1, "kind": "fail", "msg": "mug not found"}],
                              opts=dict(LEGS)),
    "phantom_detection_no_legs": dict(objects={"mug": SF},
                                      faults=[{"skill": "find", "obj": "mug", "nth": 1, "kind": "false_positive"}, {"skill": "pick", "obj": "mug", "nth": 1, "kind": "fail", "msg": "mug not found"}],
                                      opts=dict(LEGS, search_legs=0)),
    "phantom_twice": dict(objects={"mug": SF},
                          faults=[{"skill": "find", "obj": "mug", "nth": [1, 2], "kind": "false_positive"}, {"skill": "pick", "obj": "mug", "nth": [1, 2], "kind": "fail", "msg": "mug not found"}],
                          opts=dict(LEGS, search_total=5)),
    "closed_fridge_believed_open": dict(plan=FRIDGE_PLAN, objects={"mug": FR + "/in"},
                                        faults=[{"skill": "open_drawer", "nth": 1, "kind": "partial_open"}], opts=dict(LEGS)),
    "closed_fridge_no_option": dict(plan=FRIDGE_PLAN, objects={"mug": FR + "/in"},
                                    faults=[{"skill": "open_drawer", "nth": 1, "kind": "partial_open"}], opts=dict(search_max=2)),
    "found_at_then_lost_relook": dict(objects={"mug": CT},
                                      faults=[{"skill": "find", "obj": "mug", "nth": 2, "kind": "fail"},
                                              {"skill": "pick", "obj": "mug", "nth": 1, "kind": "slip"}],
                                      opts=dict(LEGS, relook="1")),
    "default_options": dict(objects={"mug": SF}, opts={}),
    "default_options_nowhere": dict(objects={"mug": "attic"}, opts={}),
}


def _run(make_assist, agent_state, case, as_old, folder):
    """One run in its own folder with a fresh world: neither the object memory, the search experience nor the belief world of a
    run may reach the next one (the fixture's state is shared by the two runs of a test)."""
    agent_state.world = type(agent_state.world)()
    d = folder / ("old" if as_old else "new")
    d.mkdir(parents=True)
    robot = FakeRobot(ENV, case["objects"], faults=case.get("faults", ()), reply_rules=case.get("reply_rules"))
    plan = case.get("plan", PLAN)
    loop = make_assist(robot, FakeLLM(understood(CT), default="{}"), ScriptedPlanner(plan, replans=[plan] * 6),
                       **dict(memory_path=str(d / "object_memory.json"), log_dir=str(d / "runs"), live_path=str(d / "live.jsonl"),
                              **case["opts"]))
    if as_old:
        loop.__class__ = OldLoop
    evs = run_task(loop, TASK, max_events=4000)
    return scrub(evs), scrub(list(loop.lab_log)), list(robot.trace), list(robot.asked), evs


def _first_difference(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i, {k: (x.get(k), y.get(k)) for k in set(x) | set(y) if x.get(k) != y.get(k)}
    return len(a), len(b)


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_run_is_the_same_with_the_reference_copy(name, make_assist, agent_state, tmp_path):
    new = _run(make_assist, agent_state, CASES[name], False, tmp_path / name)
    ref = _run(make_assist, agent_state, CASES[name], True, tmp_path / name)
    assert new[0] == ref[0], (name, _first_difference(new[0], ref[0]))
    assert new[1] == ref[1] and new[2] == ref[2] and new[3] == ref[3], name
    assert done(new[4])["status"] == done(ref[4])["status"]


# -- (3) coverage of the grid -------------------------------------------------------------------------------------------------
def test_the_grid_reaches_every_recover_kind_of_the_ladder(make_assist, agent_state, tmp_path):
    kinds, flags, statuses = set(), set(), set()
    for name, case in CASES.items():
        _s, lab, _t, _a, evs = _run(make_assist, agent_state, case, False, tmp_path / name)
        kinds |= {e.get("kind") for e in evs if e["event"] == "recover"}
        flags |= {e["flag"] for e in lab}
        statuses.add(done(evs)["status"])
    assert {"relook", "second_look", "search_leg", "phantom"} <= kinds, kinds
    assert "relook" in flags, flags
    assert statuses == {"success", "failed"}, statuses
