"""Step S11: the plan checker (_check_plan, _check_step, _word_step, _word_pick, _word_place) moved verbatim from assist_loop.py
to the GatePhase mixin in phases/gate.py; ``AssistLoop._check`` stays in assist_loop.py as a short method that calls it
(tests/test_wiring_function_size.py looks it up by file name). These tests prove nothing changed: (1) the mixin is in
AssistLoop's method resolution order, serves the five names and ``_check`` is the delegating stub; (2) a differential test
against the verbatim reference copy (tests/legacy_ref/s11_gate.py) over a seeded grid of made-up plans, worlds and goals: the old
and the new class return the same findings, leave the belief untouched and set the same ``_env_places``; (3) the grid really
reaches many different finding sentences, so the comparison bites. Made-up places only (the fakebot robot and the sim ENV)."""

import ast
import copy
import itertools
import random
from pathlib import Path

from legacy_ref import s11_gate as old
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.phases import PHASE_MIXINS, gate
from robot_agent.core.planning.robot import RobotView

NAMES = ("_check_plan", "_check_step", "_word_step", "_word_pick", "_word_place")
DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"


class OldLoop(old.RefGate, AssistLoop):
    pass


_ROBOT = RobotView.load("fakebot")


def _make(cls):
    lp = cls.__new__(cls)
    lp.robot, lp._namemap = _ROBOT, _ROBOT.adapter.namemap
    return lp, _ROBOT


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_five_names():
    assert gate.GatePhase in PHASE_MIXINS
    mro = AssistLoop.__mro__
    assert mro.index(gate.GatePhase) < mro.index(assist_loop.WhichOne)
    for n in NAMES:
        assert n not in vars(AssistLoop), n
        assert n in vars(gate.GatePhase), n
        assert getattr(AssistLoop, n) is not None


def test_check_stays_in_the_loop_file_as_a_short_delegating_method():
    src = Path(assist_loop.__file__).read_text()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_check")
    assert fn.end_lineno - fn.lineno + 1 <= 5
    assert "_check_plan" in ast.get_source_segment(src, fn)
    assert "_check" in vars(AssistLoop) and "_check" not in vars(gate.GatePhase)


def test_the_moved_module_is_small():
    assert len(Path(gate.__file__).read_text().splitlines()) <= 300


# -- (2) differential test ----------------------------------------------------------------------------------------------------
STEP_POOL = [
    ("MoveTo", DR), ("MoveTo", DT), ("MoveTo", DS), ("MoveTo", CT), ("MoveTo", "@kitchen"), ("MoveTo", "nowhere"),
    ("Open", DR), ("Open", CT), ("Open", DT), ("Close", DR), ("Close", CT),
    ("Find", "apple"), ("Find", "orange"), ("Pick", "apple"), ("Pick", "orange"), ("Pick", "cup"),
    ("Place", DT), ("Place", DS), ("Place", "@kitchen"), ("Place", ""), ("PutIn", DR), ("PutIn", DT),
    ("Teleport", "apple"), ("Wait", ""),
]
WORLDS = [{}, {"holding": "apple"}, {"holding": "orange"}, {"arrived": DR}, {"arrived": DT}, {"opened": [DR]},
          {"holding": "apple", "arrived": DT}, {"opened": [DR], "arrived": DR}]
GOAL_SETS = [
    [], [G("on", "apple", "", DT)], [G("on", "apple", DS, DT)], [G("open", DR, "", DR)], [G("close", DR, "", DR)],
    [G("in", "orange", "", DR)], [G("on", "apple", "", DT), G("in", "orange", "", DR)], [G("on", "cup", DR, DS)],
]


def _plans(rng, n):
    for _ in range(n):
        k = rng.randint(0, 7)
        plan = []
        for _ in range(k):
            a, o = rng.choice(STEP_POOL)
            st = {"action": a, "object": o}
            if rng.random() < 0.08:
                st["grounded"] = False
            if rng.random() < 0.04:
                st = {"skill": "custom_skill"}
            plan.append(st)
        yield plan


def _run(cls, env, plan, world, goals):
    lp, robot = _make(cls)
    places = Places(env, openable=robot.openable)
    w = W(**world)
    before = w.to_dict()
    probs = lp._check(copy.deepcopy(plan), w, places, copy.deepcopy(goals))
    assert w.to_dict() == before                      # the check never touches the belief
    return probs, lp._env_places is places


def test_check_equals_the_reference_on_a_seeded_grid(env):
    rng, seen = random.Random(11), set()
    for plan in _plans(rng, 800):
        for world, goals in itertools.product(rng.sample(WORLDS, 3), rng.sample(GOAL_SETS, 3)):
            a, b = _run(OldLoop, env, plan, world, goals), _run(AssistLoop, env, plan, world, goals)
            assert a == b, (plan, world, goals)
            assert a[1]
            seen.update(t.split(": ", 1)[-1][:24] for _, t, _ in a[0])
    assert len(seen) >= 25, sorted(seen)               # the grid reaches many different findings


def test_check_equals_the_reference_on_the_pinned_plans(env):
    plans = [
        [{"action": "Find", "object": "apple"}, {"action": "Pick", "object": "apple"}, {"action": "MoveTo", "object": DS},
         {"action": "Place", "object": DS}],
        [{"action": "MoveTo", "object": DR}, {"action": "Open", "object": DR}, {"action": "Close", "object": DR},
         {"action": "Open", "object": DR}],
        [{"action": "Pick", "object": "apple"}, {"action": "MoveTo", "object": DT}, {"action": "Place", "object": DT}],
        [{"action": "MoveTo", "object": "@kitchen", "grounded": False}],
    ]
    for plan, world, goals in itertools.product(plans, WORLDS, GOAL_SETS):
        assert _run(OldLoop, env, plan, world, goals) == _run(AssistLoop, env, plan, world, goals), (plan, world, goals)


def test_the_word_helpers_equal_the_reference():
    P = lambda out: (lambda i, txt, crit=True: out.append((i, txt, crit)))
    for fn in ("_word_pick", "_word_place"):
        for holding, o, placed in itertools.product(("", "apple", "orange"), ("apple", DT, DS, ""), ({}, {"apple": DT})):
            for codes in ((), ("already_holding",), ("place_wrong_location",), ("place_without_holding", "putin_closed_container")):
                outs = []
                for cls in (old.RefGate, gate.GatePhase):
                    out = []
                    has = lambda *c, _codes=codes: bool([x for x in c if x in _codes])
                    args = (0, o, holding, has, GOAL_SETS[1], dict(placed), P(out))
                    if fn == "_word_place":
                        args = (0, "Place", o, holding, o.startswith("@") or not o, has, GOAL_SETS[1], dict(placed), P(out))
                    getattr(cls, fn)(*args)
                    outs.append(out)
                assert outs[0] == outs[1], (fn, holding, o, placed, codes)


def test_the_planner_sees_the_same_findings_as_before(env):
    """The worded findings are the exact sentences the planner reads (pinned from the old loop)."""
    lp, robot = _make(AssistLoop)
    places = Places(copy.deepcopy(env), openable=robot.openable)
    plan = [{"action": "MoveTo", "object": DS}, {"action": "Pick", "object": "apple"}]
    probs = lp._check(plan, W(), places, [G("on", "apple", "", DT)])
    assert probs == _run(OldLoop, env, plan, {}, [G("on", "apple", "", DT)])[0]
    assert probs[0][1].startswith("step 2 'Pick apple':") and probs[0][2] is True
