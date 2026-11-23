"""Step S12: the end-of-run look at a goal and the checks after a step are the robot pack's verifiers, chosen by name.

(1) Structure: the ``VerifyGoals`` mixin is listed in PHASE_MIXINS and serves the moved names; ``_post_check`` (its core layers)
stays in assist_loop.py; ``_observe_goal`` stays a method of the class. (2) The default data equals the old constants: the
kinds looked at are exactly the old ``("on", "in")``, every other kind declares ``observe: null``; the step verifiers run in
the old rule order; every entry is labelled and registered. (3) Which pack checks a run (``checking_pack``). (4) A
differential test against the verbatim copy of the old code (tests/legacy_ref/s12_verify.py) over a grid of made-up goals,
places, skill results, sensor readings and option answers: equal results and equal calls, in order. (5) An action's ``post``
names a step verifier (opt-in; no shipped pack uses it). Made-up robots only."""

import itertools
from types import SimpleNamespace as NS

import pytest

from legacy_ref import s12_verify as old
from robot_agent.contract import FailureCode
from robot_agent.core.pack import goal_semantics, verifiers as V
from robot_agent.core.pack.loader import Pack
from robot_agent.core.pack.registry import Registry
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.phases import PHASE_MIXINS, verify_goals
from robot_agent.core.planning.records import VerifyResult

OLD_OBSERVED = ("on", "in")                        # assist_loop.py:3048 at 9f1786d: the kinds the end check looked at
MOVED = ("_observe_goals", "_end_action", "_return_tries", "_return_to_end", "_observe_goal", "_look", "_final_check")
DEFAULT = goal_semantics._default()


# -- (1) structure ------------------------------------------------------------------------------------------------------------
def test_mixin_is_listed_and_serves_the_moved_names():
    assert verify_goals.VerifyGoals in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(verify_goals.VerifyGoals) < mro.index(assist_loop.WhichOne) < mro.index(assist_loop.ClosedLoop)
    for n in MOVED + ("_step_checks",):
        assert n not in vars(assist_loop.AssistLoop), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(verify_goals.VerifyGoals, n), n


def test_post_check_core_layers_stay_in_the_loop_file():
    assert "_post_check" in vars(assist_loop.AssistLoop) and "_post_check" not in vars(verify_goals.VerifyGoals)


# -- (2) the default data equals the old constants -----------------------------------------------------------------------------
def test_the_kinds_looked_at_are_the_old_ones():
    assert 'not in ("on", "in")' in open(old.__file__, encoding="utf-8").read()       # the constant of the reference copy
    assert tuple(V.observed_kinds(DEFAULT)) == OLD_OBSERVED
    for kind, entry in DEFAULT.table("goal_kinds").items():
        want = "std.find_at_destination" if kind in OLD_OBSERVED else None
        assert "observe" in entry and entry["observe"] == want, kind


def test_the_step_verifiers_run_in_the_old_rule_order_for_every_action():
    for action in ("Place", "PutIn", "Find", "Pick", "MoveTo", "Handover", "Open", None):
        assert [e["callable"] for _fn, e in V.step_verifiers(DEFAULT, action)] == ["std.release_second_look", "std.find_reach"]


def test_every_verifier_entry_is_labelled_and_registered_in_its_namespace():
    want = {"std.find_at_destination": V.GOAL, "std.release_second_look": V.STEP, "std.find_reach": V.STEP}
    table = DEFAULT.table(V.TABLE)
    assert list(table) == list(want)
    for name, ns in want.items():
        assert table[name].get("source"), name
        assert DEFAULT.named(ns, table[name]["callable"]) is not None, name
    assert DEFAULT.problems == []


# -- (3) which pack checks a run -------------------------------------------------------------------------------------------------
def _pack(chain, tables=None, registry=None):
    return Pack(schema=2, name=chain[-1], chain=tuple(chain), tables=tables or {}, registry=registry)


def test_checking_pack():
    assert V.checking_pack(None) == DEFAULT and V.checking_pack(DEFAULT) is DEFAULT
    alone = _pack(("core", "line"))                                    # extends: null, no verifier: the old checks
    assert V.checking_pack(alone) == DEFAULT and V.checking_pack(alone, DEFAULT) is DEFAULT
    own = _pack(("core", "line"), {"verifiers": {"line.look": {"callable": "line.look"}}})
    assert V.checking_pack(own) is own
    dropped = _pack(DEFAULT.chain[:-1] + ("line",))                    # extends the standard pack and removes every verifier
    assert V.checking_pack(dropped) is dropped and V.step_verifiers(dropped, "Place") == []


def test_a_kind_without_observe_stays_on_the_belief():
    class Loop(verify_goals.VerifyGoals):
        pass
    for kind in ("open", "close", "go", "say", "deliver", "weld", None):
        g = {"type": kind, "object": "bolt", "to": "jig"}
        assert Loop()._observe_goal(NS(), g) == (None, f"{kind} bolt: no sensor declared for it (belief)", None)


# -- (4) differential test: _observe_goal ----------------------------------------------------------------------------------------
def _res(ok, code=None, msg=""):
    return None if ok is None else NS(ok=ok, code=code, msg=msg)


class FakeGoalLoop:
    """What the old ``_observe_goal`` and ``std.find_at_destination`` use: _handed, options.decide, _look, _second_look_seen."""

    def __init__(self, handed, decide, looks, second, log):
        if handed is not None:
            self._handed = handed
        self.options = NS(decide=lambda hook, r, o: log.append(("decide", hook, o)) or decide)
        self._looks, self._second, self._log = {k: list(v) for k, v in looks.items()}, second, log

    def _look(self, r, st):
        self._log.append(("look", st["action"], st["object"]))
        res = self._looks[st["action"]].pop(0)
        return res, (f"{st['action'].lower()}_skill" if res is not None else None)

    def _second_look_seen(self, r, o, place, nice, skill):
        self._log.append(("second", o, place, nice, skill))
        return self._second


def _run_goal(fn, kind, case):
    handed, placed_here, decide, opened, arrived, move, find1, find2, second = case
    log = []
    loop = FakeGoalLoop(handed, decide, {"MoveTo": [move], "Find": [find1, find2]}, second, log)
    place = "bench@den"
    r = NS(placed={"gizmo": place if placed_here else "shelf"},
           places=NS(openable=lambda p: opened is not None),
           world=NS(opened={place} if opened else set(), arrived=place if arrived else "hall"),
           memory=NS(see=lambda *a: log.append(("see",) + a)),
           robot=NS(failure_code=lambda res, msg: res.code or FailureCode.UNKNOWN))
    out = fn(loop, r, {"type": kind, "object": "gizmo", "to": place})
    return out, log


GOAL_GRID = list(itertools.product(
    (None, set(), {"gizmo"}),                                          # _handed: absent, empty, holds the object
    (True, False),                                                     # placed at the goal's place by the run
    (None, (False, "out of reach now", "dev")),                        # option on_goal_check
    (None, False, True),                                               # not openable / openable and closed / opened
    (True, False),                                                     # the robot stands at the place
    (None, _res(False, FailureCode.BLOCKED, "blocked"), _res(True)),   # MoveTo
    (None, _res(True), _res(False, FailureCode.ABSENT, "nothing"), _res(False, FailureCode.PERCEPTION_UNAVAILABLE, "dark")),
    (None, _res(True), _res(False, FailureCode.ABSENT, "again nothing")),   # the second Find
    (None, (True, "seen twice", "find_skill")),                        # on_second_look's answer
))


@pytest.mark.parametrize("kind", ["on", "in", "open", "go", "say", "deliver", "weld"])
def test_observe_goal_equals_the_old_code(kind):
    grid = GOAL_GRID if kind in OLD_OBSERVED else GOAL_GRID[:50]
    for case in grid:
        assert _run_goal(assist_loop.AssistLoop._observe_goal, kind, case) == \
            _run_goal(old.OldVerify._observe_goal, kind, case), (kind, case)


# -- (4) differential test: _post_check ------------------------------------------------------------------------------------------
class FakeStepLoop(verify_goals.VerifyGoals):
    """What the old ``_post_check`` and the step verifiers use. Inherits ``_step_checks`` from the mixin."""

    def __init__(self, readings, point, near, sensor_health, log):
        self.cfg = {"sensor_health": sensor_health, "reach_far": 2.0}
        self.robot = NS(model_capabilities=lambda: log.append(("caps",)) or NS(release_actions={"Handover"}))
        self._readings, self._point, self._near, self._log = list(readings), point, near, log

    def _holding(self, node, default=True):
        self._log.append(("holding", node, default))
        return self._readings.pop(0) if self._readings else default

    def _found_point(self, result, obj):
        self._log.append(("point", obj))
        return self._point

    def _place_of_far_point(self, p3, node, places, world):
        self._log.append(("far", tuple(p3)))
        return self._near


V_OK, V_BAD = VerifyResult("isdone", True, "done"), VerifyResult("isdone", False, "gripper empty")
S_BAD, SYM_OK, SYM_BAD = VerifyResult("sensor", False, "gripper full"), VerifyResult("symbolic", True), VerifyResult("symbolic", False)
LAYERS = ([], [V_OK], [V_BAD], [V_OK, S_BAD], [V_OK, S_BAD, SYM_OK], [V_OK, SYM_BAD, S_BAD], [V_BAD, S_BAD])


def _run_step(fn, case):
    action, layers, code, readings, holding, point, near, search, health = case
    log = []
    loop = FakeStepLoop(readings, point, near, health, log)
    r = NS(verifier=NS(verify=lambda st, res, world, node, params=None: list(layers)), world=NS(holding=holding), node="n0",
           robot=None, seen_at={}, seen_far=[], places="P", memory=NS(see=lambda *a: log.append(("see",) + a)))
    if search:
        r.search = NS(see=lambda *a, **k: log.append(("search",) + a + tuple(sorted(k.items()))))
    out = fn(loop, r, {"action": action, "object": "cup"}, "skill_x", NS(code=code), {"p": 1}, NS(result={"ins": {}}))
    return [(v.layer, v.ok, v.detail, v.unknown) for v in out], log, r.seen_at, r.seen_far


STEP_GRID = list(itertools.product(
    ("Place", "PutIn", "Handover", "Pick", "Find", "MoveTo"), LAYERS, (FailureCode.GRASP_FAILED, None),
    ((), (False,), (True,), (False, False), (False, True), (False, False, False), (True, False, False)),
    ("cup", None), (None, (1.0, 0.0, 0.5), (3.0, 1.0, 0.5)), (None, "shelf@hall"), (False, True), (False, True)))


def test_post_check_equals_the_old_code():
    for case in STEP_GRID:
        assert _run_step(assist_loop.AssistLoop._post_check, case) == _run_step(old.OldVerify._post_check, case), case


# -- (5) an action's ``post`` names a step verifier (opt-in) ---------------------------------------------------------------------
def test_an_action_post_names_a_step_verifier_after_the_every_step_ones():
    reg = Registry()
    reg.register(V.STEP, "line.seated", lambda step, result, ctx: ctx.vres + [VerifyResult("seated", True)], module="tests.line")
    reg.register(V.STEP, "line.always", lambda step, result, ctx: ctx.vres + [VerifyResult("always", True)], module="tests.line")
    pack = _pack(("core", "line"), {"verifiers": {"line.seated": {"callable": "line.seated"},
                                                  "line.always": {"callable": "line.always", "every_step": True}},
                                    "actions": {"Fasten": {"post": ["line.seated"]}}}, reg)
    assert [e["callable"] for _f, e in V.step_verifiers(pack, "Fasten")] == ["line.always", "line.seated"]
    assert [e["callable"] for _f, e in V.step_verifiers(pack, "Find")] == ["line.always"]
