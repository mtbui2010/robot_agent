"""Step S12 chose the verifiers by name: ``AssistLoop._observe_goal`` asks the goal kind's ``observe`` verifier of the robot's
pack, and ``_post_check`` runs the pack's step checks after the core layers (robot_agent/core/pack/verifiers.py; the household
bodies: robot_agent/packs/household/callables/verifiers.py; the moved methods: core/planning/phases/verify_goals.py).
These tests prove nothing changed for the standard pack: (1) its data equals the old constants (only ``on`` / ``in`` were
looked at; the two release rules and the Find rule ran after every step, in that order); (2) a differential grid: the new
methods and the reference copy of the old ones (tests/legacy_ref/s12_verify.py) return the same value and make the same
calls in the same order over made-up goals, runs, look results and sensor readings; (3) the methods stay class attributes
of AssistLoop and the hooks keep their arguments; (4) opt-in: a pack's own data chooses, removes or adds a verifier, and a
pack with ``extends: null`` and no verifier keeps the default checks."""
import itertools
from types import SimpleNamespace

import pytest

from legacy_ref.s12_verify import Old
from robot_agent.contract import FailureCode
from robot_agent.core.pack import Registry, default_pack, resolve
from robot_agent.core.pack import verifiers as pv
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.phases.verify_goals import VerifyGoals
from robot_agent.core.planning.records import VerifyResult

PLACE, BOX = "table@den", "box@den"
PACK = default_pack()


# ── 1. the standard pack's data equals the old constants ─────────────────────────────────────────────────────────────────
def test_only_on_and_in_goals_are_looked_at_by_the_standard_pack():
    assert pv.observed_kinds(PACK) == ["on", "in"]                           # old: g.get("type") not in ("on", "in")
    for kind in list(goal_kinds.GOAL_KINDS) + ["give", "polish", None, 3]:
        fn, _e = pv.goal_verifier(PACK, kind)
        assert (fn is not None) == (kind in ("on", "in")), kind


def test_every_step_gets_the_release_check_then_the_find_check():
    for action in ("Place", "PutIn", "Find", "Pick", "MoveTo", "Handover", "Open", None):
        assert pv.step_names(PACK, action) == ["std.release_second_look", "std.find_reach"], action
        assert [e["callable"] for _f, e in pv.step_verifiers(PACK, action)] == ["std.release_second_look", "std.find_reach"]


def test_the_standard_verifiers_carry_a_source_label():
    for name, e in PACK.table("verifiers").items():
        assert e.get("source"), name


# ── 2. differential grid: the new methods against the verbatim copy of the old ones ──────────────────────────────────────
class _Res:
    def __init__(self, ok, code=None, msg="m", device=None):
        self.ok, self.code, self.msg, self.device = ok, code, msg, device


LOOKS = {"none": None, "ok": _Res(True, msg="seen"), "absent": _Res(False, FailureCode.ABSENT, "not found"),
         "absent_nomsg": _Res(False, FailureCode.ABSENT, ""), "timeout": _Res(False, FailureCode.TIMEOUT, "slow")}


class _Log(list):
    def call(self, *what, ret=None):
        self.append(what)
        return ret


class _Robot:
    def __init__(self, log):
        self.log, self.pack = log, PACK

    def model_capabilities(self):
        self.log.call("capabilities")
        return SimpleNamespace(release_actions={"Handover"})

    def failure_code(self, res, msg):
        return self.log.call("failure_code", msg, ret=res.code)


class _Options:
    def __init__(self, log, verdicts):
        self.log, self.verdicts = log, verdicts

    def decide(self, hook, run, *args):
        assert run is not None
        return self.log.call("decide", hook, *args, ret=self.verdicts.get(hook))


class _Base:
    """The loop's services both copies call; every call is logged with its arguments."""

    def __init__(self, looks=(), second=None, verdicts=None, handed=None, holding=(), point=None, near=None):
        self.log = _Log()
        self.robot, self.options = _Robot(self.log), _Options(self.log, verdicts or {})
        self.cfg = {"sensor_health": False, "reach_far": 1.0}
        self._looks, self._second, self._holdings, self._point, self._near = list(looks), second, list(holding), point, near
        if handed is not None:
            self._handed = set(handed)

    def _look(self, r, st):
        res = LOOKS[self._looks.pop(0)] if self._looks else None
        return self.log.call("look", st["action"], st["object"], ret=(res, f"sk_{st['action']}" if res else None))

    def _second_look_seen(self, r, o, place, nice, skill):
        return self.log.call("second_look_seen", o, place, nice, skill, ret=self._second)

    def _holding(self, node, default):
        return self.log.call("holding", default, ret=self._holdings.pop(0) if self._holdings else default)

    def _found_point(self, result, o):
        return self.log.call("found_point", result, o, ret=self._point)

    def _place_of_far_point(self, p3, node, places, world):
        return self.log.call("far_point", p3, ret=self._near)


def _outcome(fn, *args):
    """The value of ``fn(*args)``, or the exception it raises (type and text): both copies must do the same."""
    try:
        return fn(*args)
    except Exception as e:                                   # noqa: BLE001 - compared, not hidden
        return ("raised", type(e).__name__, str(e))


class New(_Base, VerifyGoals):          # the base first: its _look is the faked one
    pass


class Ref(_Base, Old):
    pass


def _run(log, placed=None, openable=False, opened=(), arrived=None, holding=None, vres=(), search=False):
    places = SimpleNamespace(openable=lambda p: openable and p == BOX)
    world = SimpleNamespace(arrived=arrived, opened=set(opened), holding=holding)
    memory = SimpleNamespace(see=lambda *a: log.call("see", *a))
    verifier = SimpleNamespace(verify=lambda st, res, w, node, params=None: list(vres))
    srch = SimpleNamespace(see=lambda o, near, how: log.call("search_see", o, near, how)) if search else None
    return SimpleNamespace(placed=dict(placed or {}), places=places, world=world, memory=memory, robot=_Robot(log),
                           node=None, seen_at={}, seen_far=[], search=srch, verifier=verifier)


def _goal_case(kind, to, handed, placed, verdict, door, at, looks, second):
    g = {"type": kind, "object": "mug", "to": to} if kind is not None else {"object": "mug"}
    runs = []
    for cls in (New, Ref):
        lp = cls(looks=looks, second=second, verdicts={"on_goal_check": verdict}, handed=handed)
        r = _run(lp.log, placed={"mug": to} if placed else {}, openable=door != "plain", opened=[BOX] if door == "open" else (),
                 arrived=to if at else "hall")
        runs.append((_outcome(cls._observe_goal, lp, r, g), list(lp.log), r.placed))
    return runs


GOAL_GRID = list(itertools.product(
    ("on", "in"), (PLACE, BOX), (None, {"mug"}, set()), (False, True), (None, (False, "own", "dev")),
    ("plain", "closed", "open"), (False, True), [list(x) for x in itertools.product(LOOKS, repeat=3)],
    ((True, "seen twice", "sk"), (False, "doubted", "sk"), ())))


@pytest.mark.parametrize("chunk", range(8))
def test_the_goal_check_of_an_on_or_in_goal_is_the_old_one(chunk):
    for case in GOAL_GRID[chunk::8]:
        new, ref = _goal_case(*case)
        assert new == ref, case


def test_a_goal_of_any_other_kind_stays_on_the_belief_as_before():
    for kind in ("open", "close", "go", "say", "deliver", "give", "polish", None, ""):
        new, ref = _goal_case(kind, PLACE, {"mug"}, True, (True, "x", None), "plain", False, ["ok"], ())
        assert new == ref and new[0][0] is None and "no sensor declared for it (belief)" in new[0][1] and new[1] == []


VRES = {"empty": [], "ok": [VerifyResult("isdone", True, "d")], "fail": [VerifyResult("isdone", False, "gripper empty")],
        "sensor_full": [VerifyResult("isdone", True, "d"), VerifyResult("sensor", False, "full")],
        "sensor_full_fx": [VerifyResult("isdone", True, "d"), VerifyResult("sensor", False, "full"), VerifyResult("effect", True)],
        "sensor_full_fx_bad": [VerifyResult("isdone", True, "d"), VerifyResult("sensor", False, "full"), VerifyResult("effect", False)],
        "sensor_ok": [VerifyResult("isdone", True, "d"), VerifyResult("sensor", True, "empty")]}


def _step_case(action, vres, code, holds, held, point, near, search):
    st, res = {"action": action, "object": "mug"}, _Res(True, code)
    runs = []
    for cls in (New, Ref):
        lp = cls(holding=holds, point=point, near=near)
        r = _run(lp.log, holding=held, vres=VRES[vres], search=search)
        out = cls._post_check(lp, r, st, "sk", res, {"p": 1}, SimpleNamespace(result={"isdone": True}))
        runs.append(([(v.layer, v.ok, v.detail, v.unknown) for v in out], list(lp.log), r.seen_at, r.seen_far))
    return runs


STEP_GRID = list(itertools.product(
    ("Place", "PutIn", "Handover", "Find", "Pick", "MoveTo"), VRES, (FailureCode.GRASP_FAILED, FailureCode.ABSENT),
    [list(x) for x in itertools.product((False, True), repeat=3)], (None, "mug"), (None, (0.5, 0.1, 0.8), (3.0, 0.0, 0.8)),
    (None, PLACE), (False, True)))


@pytest.mark.parametrize("chunk", range(4))
def test_the_checks_after_a_step_are_the_old_ones(chunk):
    for case in STEP_GRID[chunk::4]:
        new, ref = _step_case(*case)
        assert new == ref, case


def test_the_grids_reach_every_branch():
    """The grids are not vacuous: they reach the hand-over, the second look, the release re-reads and the far Find."""
    goal = [_goal_case(*c)[0] for c in GOAL_GRID[::37]]
    texts = " | ".join(str(x[0][1]) for x in goal)
    for part in ("handed over", "inside the closed", "could not go back", "no Find skill", "is seen on", "seen twice",
                 "is not on", "could not look", "own"):
        assert part in texts, part
    step = [_step_case(*c)[0][0] for c in STEP_GRID[::7]]
    flat = {layer for out in step for layer, *_ in out}
    assert {"recheck", "reach"} <= flat
    assert any(d.endswith("two second readings, gripper empty") for out in step for _l, _o, d, _u in out)


# ── 3. seams and hooks ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_verify_methods_stay_class_attributes_of_the_loop():
    for name in ("_observe_goal", "_observe_goals", "_post_check", "_final_check", "_look", "_return_to_end"):
        assert getattr(AssistLoop, name) is getattr(VerifyGoals, name), name


def test_a_patched_observe_goal_is_what_the_goal_check_calls(monkeypatch):
    monkeypatch.setattr(AssistLoop, "_observe_goal", lambda self, r, g: (False, "patched", "dev"))
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg, lp._cancelled = {"goal_check": "observe"}, lambda r: None
    log = _Log()
    r = _run(log, arrived=PLACE)
    r.completed, r.goals, r.goal_checks = [], [{"type": "go", "object": "", "to": PLACE}], []
    r.ev = lambda kind, **kw: {"event": kind, **kw}
    r.world.to_dict = lambda: {}
    events = list(lp._observe_goals(r))
    assert [e.get("evidence") for e in events if e["event"] == "goal_check"] == ["patched"]


# ── 4. opt-in: the pack's own data decides ───────────────────────────────────────────────────────────────────────────────
class _Pack:
    """A made-up resolved pack: tables and a registry (duck-typed like loader.Pack)."""

    def __init__(self, tables, registry, chain=("core", "mine")):
        self.tables, self.registry, self.chain = tables, registry, chain

    def table(self, name):
        return self.tables.get(name, {})

    def entry(self, table, name):
        return self.table(table).get(name)

    def named(self, namespace, name):
        return self.registry.get(namespace, name) if name else None


def _registry():
    reg = Registry()
    reg.register("verifier.goal", "tst.look", lambda goal, ctx: (True, "tst", None), module="tests.made_up")
    reg.register("verifier.step", "tst.every", lambda step, result, ctx: ctx, module="tests.made_up")
    reg.register("verifier.step", "tst.post", lambda step, result, ctx: ctx, module="tests.made_up")
    return reg


def test_a_kind_names_its_own_goal_verifier_and_null_means_none():
    reg = _registry()
    pk = _Pack({"goal_kinds": {"install": {"observe": "tst.look"}, "wipe": {"observe": None}, "ghost": {"observe": "tst.none"}},
                "verifiers": {"tst.look": {"callable": "tst.look"}, "tst.none": {"callable": "tst.unregistered"}}}, reg)
    assert pv.goal_verifier(pk, "install")[0] is reg.get("verifier.goal", "tst.look")
    assert pv.goal_verifier(pk, "wipe") == (None, None) == pv.goal_verifier(pk, "ghost") == pv.goal_verifier(pk, "other")
    assert pv.goal_verifier(None, "on") == (None, None) and pv.observed_kinds(pk) == ["install"]


def test_step_checks_run_every_step_ones_first_then_the_actions_post_once():
    reg = _registry()
    pk = _Pack({"verifiers": {"tst.post": {"callable": "tst.post"}, "tst.every": {"callable": "tst.every", "every_step": True},
                              "tst.declared": {"kind": "sensor", "skill": "look"}},
                "actions": {"Mount": {"post": ["tst.post", "tst.every", "tst.declared", "tst.missing"]}}}, reg)
    assert pv.step_names(pk, "Mount") == ["tst.every", "tst.post", "tst.declared", "tst.missing"]
    assert [f for f, _e in pv.step_verifiers(pk, "Mount")] == [reg.get("verifier.step", "tst.every"), reg.get("verifier.step", "tst.post")]
    assert pv.step_names(pk, "Other") == ["tst.every"] and pv.step_verifiers(None, "Mount") == []


def test_a_pack_that_removes_a_step_check_does_not_run_it():
    pk = resolve(None, None, raw={"schema": 2, "name": "tst_s12_rm", "extends": "household", "assumed": True,
                                  "remove": {"verifiers": ["std.find_reach"]}})
    assert pv.checking_pack(pk) is pk
    assert pv.step_names(pk, "Find") == ["std.release_second_look"] and pv.observed_kinds(pk) == ["on", "in"]


def test_a_pack_with_no_base_and_no_verifier_keeps_the_default_checks():
    bare = resolve(None, None, raw={"schema": 2, "name": "tst_s12_bare", "assumed": True})
    assert pv.checking_pack(bare) is not bare and pv.observed_kinds(pv.checking_pack(bare)) == ["on", "in"]
    assert pv.checking_pack(None) is pv.checking_pack(bare)
    own = _Pack({"verifiers": {"tst.every": {"callable": "tst.every", "every_step": True}}}, _registry())
    assert pv.checking_pack(own, default=PACK) is own                         # it declares a verifier: its own data counts
    assert pv.checking_pack(PACK) is PACK
