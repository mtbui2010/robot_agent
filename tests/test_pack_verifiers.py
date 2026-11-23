"""Step S12: the end-of-run goal check and the checks after a step are pack verifiers chosen by name.

What these tests prove: (1) the verify methods moved verbatim to the VerifyGoals mixin (the AST of each, without its
docstring, equals the reference copy tests/legacy_ref/s12_verify.py, taken from assist_loop.py at 9f1786d), the mixin is
in AssistLoop's method resolution order and serves them, ``_post_check`` stays in the loop file; (2) the standard pack's
data equals the old constants: the kinds looked at are exactly ("on", "in"), every other kind is declared ``observe:
null``, the step verifiers are the release rule then the Find rule, every entry is labelled; (3) packs that declare
nothing about verifiers (``extends: null``, a removed or replaced kind) keep the old checks, and a pack that declares
``observe: null`` or removes a step verifier switches that check off; (4) a differential test: the new
``AssistLoop._observe_goal`` and ``_post_check`` against the reference copy on grids of made-up goals, steps, sensor
readings and results, comparing the answers AND the calls made (looks, option hooks, gripper reads, memory), in order.
Made-up robots and places only."""

import ast
import inspect
import itertools
import textwrap
from types import SimpleNamespace as NS

import pytest

from legacy_ref import s12_verify as old
from robot_agent.contract import FailureCode
from robot_agent.core.pack import loader, verifiers
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.phases import PHASE_MIXINS, verify_goals
from robot_agent.core.planning.records import VerifyResult

MOVED = ("_observe_goals", "_end_action", "_return_tries", "_return_to_end", "_look", "_final_check")
OLD_OBSERVED = ("on", "in")                     # assist_loop.py:3048 at 9f1786d


# -- (1) structure and verbatim move ------------------------------------------------------------------------------------------
def _body(fn) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
        node.body = node.body[1:]
    return ast.dump(node)


def test_the_mixin_is_listed_and_serves_the_verify_methods():
    assert verify_goals.VerifyGoals in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(verify_goals.VerifyGoals) < mro.index(assist_loop.WhichOne)
    for n in MOVED + ("_observe_goal", "_step_checks"):
        assert n not in vars(assist_loop.AssistLoop), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(verify_goals.VerifyGoals, n), n
    assert "_post_check" in vars(assist_loop.AssistLoop) and "_post_check" not in vars(verify_goals.VerifyGoals)


@pytest.mark.parametrize("name", MOVED)
def test_the_moved_method_is_the_old_one(name):
    assert _body(getattr(verify_goals.VerifyGoals, name)) == _body(getattr(old.RefVerify, name))


# -- (2) the default data equals the old constants ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def std():
    return loader.default_pack()


def test_the_kinds_looked_at_are_the_old_ones(std):
    assert tuple(verifiers.observed_kinds(std)) == OLD_OBSERVED
    for kind, entry in std.table("goal_kinds").items():
        assert "observe" in entry, kind                      # declared, so a robot pack's entry is what decides
        assert entry["observe"] == ("std.find_at_destination" if kind in OLD_OBSERVED else None), kind
    fn, entry = verifiers.goal_verifier(std, "on")
    assert fn.__name__ == "find_at_destination" and entry["callable"] == "std.find_at_destination"
    for kind in ("open", "close", "go", "say", "deliver", "give", "install", None):
        assert verifiers.goal_verifier(std, kind) == (None, None), kind


def test_the_step_verifiers_are_the_release_rule_then_the_find_rule(std):
    for action in ("Place", "PutIn", "Find", "Pick", "MoveTo", "Handover", None):
        assert [f.__name__ for f, _e in verifiers.step_verifiers(std, action)] == ["release_second_look", "find_reach"]
    assert list(std.table("verifiers")) == ["std.find_at_destination", "std.release_second_look", "std.find_reach"]
    assert all(e.get("source") for e in std.table("verifiers").values())
    assert not [p for p in std.problems if "verif" in p.path or "observe" in p.path]


# -- (3) compatibility and switches -------------------------------------------------------------------------------------------
def _pack(**decl):
    return loader.resolve(None, None, raw={"schema": 2, "name": "s12_line", "assumed": True, **decl})


def test_a_pack_with_no_base_keeps_the_old_checks():
    alone = _pack(extends=None, goal_kinds={"on": {"rules": "std.on"}, "mount": {"rules": "std.on"}})
    assert alone.table("verifiers") == {}
    assert verifiers.goal_verifier(alone, "on")[0].__name__ == "find_at_destination"
    assert verifiers.goal_verifier(alone, "in")[0].__name__ == "find_at_destination"
    assert verifiers.goal_verifier(alone, "mount") == (None, None)
    assert [f.__name__ for f, _e in verifiers.step_verifiers(alone, "Place")] == ["release_second_look", "find_reach"]


def test_a_removed_or_replaced_kind_keeps_the_old_look():
    p = _pack(extends="household", remove={"goal_kinds": ["in"]},
              goal_kinds={"on": {"rules": "std.on", "needs": ["Pick", "Place"]}})
    assert "observe" not in p.entry("goal_kinds", "on") and p.entry("goal_kinds", "in") is None
    assert tuple(verifiers.observed_kinds(p)) == ("on",)
    assert verifiers.goal_verifier(p, "in")[0].__name__ == "find_at_destination"


def test_observe_null_and_a_removed_step_verifier_switch_the_check_off():
    p = _pack(extends="household", remove={"verifiers": ["std.find_reach"]},
              goal_kinds={"on": {"rules": "std.on", "observe": None}})
    assert verifiers.goal_verifier(p, "on") == (None, None)
    assert verifiers.goal_verifier(p, "in")[0].__name__ == "find_at_destination"
    assert [f.__name__ for f, _e in verifiers.step_verifiers(p, "Find")] == ["release_second_look"]
    none = _pack(extends="household", remove={"verifiers": ["std.find_at_destination", "std.release_second_look",
                                                            "std.find_reach"]})
    assert verifiers.step_verifiers(none, "Place") == []


def test_the_loop_says_no_sensor_for_a_kind_with_no_verifier():
    me = NS()                                                # no robot view: the default pack
    out = assist_loop.AssistLoop._observe_goal(me, NS(), {"type": "close", "object": "drawer"})
    assert out == (None, "close drawer: no sensor declared for it (belief)", None)


# -- (4) differential test: _observe_goal -------------------------------------------------------------------------------------
class Res:
    def __init__(self, ok, code=FailureCode.ABSENT, msg="m"):
        self.ok, self.code, self.msg, self.device = ok, code, msg, "cam"


class Me:
    """A made-up loop: every service the check uses records its call."""

    def __init__(self, looks, decide, handed, seen2, log):
        self._looks, self._decide, self._handed, self._seen2, self.log = list(looks), decide, handed, seen2, log
        self.options = NS(decide=self._decide_call)

    def _decide_call(self, hook, r, *a):
        self.log.append(("decide", hook, a))
        return self._decide

    def _look(self, r, st):
        self.log.append(("look", st["action"], st["object"]))
        res = self._looks.pop(0) if self._looks else None
        return res, (f"skill{len(self.log)}" if res is not None else None)

    def _second_look_seen(self, r, o, place, nice, skill):
        self.log.append(("seen2", o, place, nice, skill))
        return self._seen2


def _run(r_args, me_args, goal, fn):
    log = []
    me = Me(*me_args, log=log)
    placed, openable, opened, arrived = r_args
    r = NS(placed=dict(placed), places=NS(openable=lambda p: p in openable), world=NS(opened=set(opened), arrived=arrived),
           memory=NS(see=lambda *a: log.append(("see",) + a)), robot=NS(failure_code=lambda res, msg: res.code))
    return fn(me, r, dict(goal)), log


GOALS = [{"type": t, "object": "cup", "to": to} for t in ("on", "in", "open", "go", "give", None) for to in ("shelf", "box@den")]
LOOK = [None, Res(True), Res(False), Res(False, FailureCode.GRASP_FAILED)]


def _observe_cases():
    worlds = [(placed, openable, opened, arrived)
              for placed in ({}, {"cup": "shelf"}, {"cup": "box@den"})
              for openable, opened in (((), ()), (("box@den",), ()), (("box@den",), ("box@den",)))
              for arrived in ("shelf", "box@den", "hall")]
    looks = [seq for n in range(4) for seq in itertools.product(LOOK, repeat=n)]
    others = list(itertools.product((None, (False, "option", None)), (set(), {"cup"}), (None, (True, "again", "s2"))))
    for goal, world, seq, (decide, handed, seen2) in itertools.product(GOALS, worlds, looks, others):
        yield world, (seq, decide, handed, seen2), goal


def test_observe_goal_is_the_old_one_on_a_grid():
    n = 0
    for world, me_args, goal in _observe_cases():
        new = _run(world, me_args, goal, assist_loop.AssistLoop._observe_goal)
        ref = _run(world, me_args, goal, old.RefVerify._observe_goal)
        assert new == ref, (world, me_args, goal)
        n += 1
    assert n > 5000


# -- (4) differential test: _post_check ---------------------------------------------------------------------------------------
V = VerifyResult
VRES = [[], [V("isdone", True)], [V("isdone", False, "full")], [V("isdone", True), V("sensor", False, "full")],
        [V("isdone", True), V("sensor", False), V("symbolic", True)], [V("isdone", True), V("sensor", False), V("vlm", False)],
        [V("isdone", True), V("sensor", True)]]
POINTS = [None, (0.4, 0.1, 0.8), (3.0, 1.0, 0.8)]


class PostMe:
    def __init__(self, reads, point, near, log):
        self._reads, self._point, self._near, self.log = list(reads), point, near, log
        self.cfg = {"sensor_health": False, "reach_far": 1.5}
        self.robot = NS(model_capabilities=self._caps)

    def _caps(self):
        self.log.append(("caps",))
        return NS(release_actions={"Handover"})

    def _holding(self, node, default):
        self.log.append(("holding", node, default))
        return self._reads.pop(0) if self._reads else default

    def _found_point(self, result, o):
        self.log.append(("point", result, o))
        return self._point

    def _place_of_far_point(self, p3, node, places, world):
        self.log.append(("far", p3))
        return self._near

    _step_checks = verify_goals.VerifyGoals._step_checks


def _post(fn, action, vres, code, reads, held, point, near, search):
    log = []
    me = PostMe(reads, point, near, log)
    r = NS(verifier=NS(verify=lambda st, res, world, node, params=None: [V(v.layer, v.ok, v.detail) for v in vres]),
           world=NS(holding=held), node="n0", robot=None, places="P", seen_at={}, seen_far=[],
           memory=NS(see=lambda *a: log.append(("see",) + a)))
    if search:
        r.search = NS(see=lambda *a, **k: log.append(("search",) + a + tuple(sorted(k.items()))))
    rec = NS(result={"isdone": True})
    out = fn(me, r, {"action": action, "object": "cup"}, "skillX", NS(code=code), {}, rec)
    return [(v.layer, v.ok, v.detail, v.unknown) for v in out], log, r.seen_at, r.seen_far


def test_post_check_is_the_old_one_on_a_grid():
    n = 0
    grid = itertools.product(("Place", "PutIn", "Handover", "Find", "Pick", "MoveTo"), VRES,
                             (FailureCode.GRASP_FAILED, FailureCode.ABSENT), itertools.product((True, False), repeat=3),
                             ("cup", None), POINTS, (None, "table"), (False, True))
    for case in grid:
        assert _post(assist_loop.AssistLoop._post_check, *case) == _post(old.RefVerify._post_check, *case), case
        n += 1
    assert n > 10000
