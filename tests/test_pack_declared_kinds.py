"""Step S29: a goal kind a schema-2 pack declares with no rules callable (core/pack/declared_kinds.py, wired in
core/pack/goal_semantics.py, core/pack/verifiers.py ``unknown_fails`` and phases/verify_goals.py).

(1) The default data: the packs a robot gets without declaring one have no kind without ``rules``, no ``phrase`` key and no
``unknown_is: failure``, so every old path is taken (the differential grids of tests/test_goal_semantics_diff.py,
tests/test_goal_phrase_diff.py and tests/test_pack_verifiers.py keep proving it); here also a grid: a pack that ADDS such a
kind leaves the other kinds' results equal to the reference copy of the old code (tests/legacy_ref/goal_reach.py).
(2) The load errors: a kind with neither ``rules`` nor ``holds``, and ``unknown_is: failure`` with no ``observe``.
(3) The opt-in: ``holds`` (every predicate True; cannot tell = not holding), reached by the plan (the list after the steps'
rules and declared effects; cannot tell = not refuted), ``missing`` (one line per refuted predicate), the read-back from the
phrase key, and ``unknown_is: failure`` (a verifier that cannot tell gives an unmet goal, code perception_unavailable).
Every fact here is made up (assumed)."""

import itertools
import types

import pytest

from fakes import steps
from legacy_ref.goal_reach import Old
from robot_agent.contract.pack import PackError
from robot_agent.core.pack import action_rules as R
from robot_agent.core.pack import declared_kinds as dk
from robot_agent.core.pack import goal_semantics as gs
from robot_agent.core.pack import verifiers as V
from robot_agent.core.pack.loader import default_pack, resolve
from robot_agent.core.planning import action_model
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.phases.verify_goals import VerifyGoals
from test_goal_semantics_diff import ATS, GOAL_LISTS, MALFORMED, PLACED, PLANS, WORLDS, outcome

BIN, JIG = "bin@cell a", "jig@cell a"
MOUNT = {"holds": ["placed(?object, ?to)", "fastened(?object)"], "needs": ["Pick", "Place", "Fasten"],
         "observe": "std.find_at_destination", "unknown_is": "failure", "phrase": "rb_mount", "assumed": True}
PACK = {"schema": 2, "name": "s29_made_up_line", "extends": "household", "assumed": True,
        "predicates": {"fastened": {"kind": "fact", "args": ["object"], "scopes": ["effect", "pre", "goal"], "assumed": True}},
        "actions": {"Fasten": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                               "effects": {"add": ["fastened(?part)"]}, "assumed": True}},
        "phrases": {"rb_mount": {"en": "mount the {object} on the {place}", "slots": ["object", "place"], "assumed": True}},
        "goal_kinds": {"mount": MOUNT}}
GOAL = {"type": "mount", "object": "bracket", "from": BIN, "to": JIG}
FULL = steps(f"MoveTo {BIN}", "Find bracket", "Pick bracket", f"MoveTo {JIG}", f"Place {JIG}", "Fasten bracket")


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


class PackRobot:
    def __init__(self, pack):
        self.pack = pack

    def release_actions(self):
        return []


@pytest.fixture(scope="module")
def pack():
    return resolve(None, None, raw=PACK)


@pytest.fixture
def loop(pack):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = PackRobot(pack)
    lp._reaches = lambda s, w, g, loop=None: AssistLoop._reaches(s, w, g, lp)   # as __init__ does
    before = R.use(R.of_pack(pack))                                             # a run makes its robot's rules active
    yield lp
    R.use(before)


# ── (1) the default data ──
def test_the_default_packs_declare_no_kind_of_this_step():
    p = default_pack()
    for kind, e in p.table("goal_kinds").items():
        assert e.get("rules") and not e.get("holds") and not e.get("phrase"), kind
        assert dk.declared(p, kind) is None and dk.phrase_entry(p, kind) is None
        assert p.value("goal_kinds", kind, "unknown_is") == "belief" and not V.unknown_fails(p, kind)
    assert not any(V.unknown_fails(p, k) for k in ("polish", "", None, 3)) and dk.declared(None, "on") is None


def test_a_pack_that_adds_a_declared_kind_leaves_every_other_kind_as_the_old_code(loop):
    for world, plan, goals in itertools.product(WORLDS, PLANS, GOAL_LISTS + MALFORMED):
        assert outcome(loop._reaches, plan, world, goals) == outcome(Old._reaches, plan, world, goals, loop), (plan, goals)
        assert outcome(loop._missing, plan, world, goals) == outcome(Old._missing, loop, plan, world, goals), (plan, goals)
    for world, placed, at, goals in itertools.product(WORLDS, PLACED, ATS, GOAL_LISTS + MALFORMED):
        assert outcome(loop._goals_hold, goals, world, placed, (), at) == outcome(Old._goals_hold, loop, goals, world, placed, (), at)


def test_a_read_back_with_no_active_declared_kind_is_the_old_text():
    assert R.active() is R.EMPTY
    assert str(AssistLoop.summary([GOAL])) == "I will mount the bracket."
    assert str(AssistLoop.summary([dict(GOAL, type="polish")])) == "I will polish the bracket."


# ── (2) the load errors ──
@pytest.mark.parametrize("entry, why", [
    ({"needs": ["Fasten"], "assumed": True}, "needs `rules`"),
    (dict(MOUNT, observe=None), "`unknown_is: failure` needs an `observe` verifier"),
    ({k: v for k, v in MOUNT.items() if k != "observe"}, "`unknown_is: failure` needs an `observe` verifier")])
def test_a_kind_that_could_never_be_decided_is_a_load_error(entry, why):
    with pytest.raises(PackError, match=why):
        resolve(None, None, raw=dict(PACK, goal_kinds={"mount": entry}))


# ── (3) the opt-in ──
def test_holds_needs_every_predicate_true_and_cannot_tell_is_not_holding(pack, loop):
    done = W(arrived=JIG)
    done.facts = {"fastened": {("bracket",)}}
    assert dk.declared(pack, "mount") == MOUNT
    assert loop._goals_hold([GOAL], done, {"bracket": JIG})
    assert not loop._goals_hold([GOAL], W(arrived=JIG), {"bracket": JIG}), "fastened is not true"
    assert not loop._goals_hold([GOAL], done, {"bracket": BIN}), "placed elsewhere"
    held = W(holding="bracket")
    held.facts = {"fastened": {("bracket",)}}
    assert not loop._goals_hold([GOAL], held, {"bracket": JIG}), "placed is not true while it is in the hand"
    no_store = types.SimpleNamespace(holding=None, arrived=JIG)                 # a world with no fact store: cannot tell
    assert dk.values(pack, MOUNT, GOAL, no_store, {"bracket": JIG}) == [("placed(?object, ?to)", True), ("fastened(?object)", None)]
    assert not dk.holds(pack, MOUNT, GOAL, no_store, {"bracket": JIG})


def test_a_plan_reaches_the_kind_when_the_list_holds_after_its_steps_and_effects(loop):
    assert loop._reaches(FULL, W(), [GOAL]) and loop._missing(FULL, W(), [GOAL]) == []
    assert not loop._reaches(FULL[:5], W(), [GOAL])
    assert loop._missing(FULL[:5], W(), [GOAL]) == ["Make fastened(bracket) true (mount bracket)"]
    assert loop._missing([], W(), [GOAL]) == ["Make placed(bracket, jig@cell a) true (mount bracket)",
                                              "Make fastened(bracket) true (mount bracket)"]
    assert loop._missing(FULL[:5], W(), [GOAL, {"type": "on", "object": "bolt", "to": BIN}]) == [
        "Make fastened(bracket) true (mount bracket)", f"Place the bolt on {BIN}"], "one line per predicate, in goal order"
    world = W()
    loop._reaches(FULL, world, [GOAL])
    assert world.holding is None and world.facts == {} and world.arrived is None, "the plan runs on a copy"


def test_without_the_declared_effect_the_plan_does_not_reach_it(pack, loop):
    R.use(R.EMPTY)                                    # no active rules: Fasten adds no fact
    assert not loop._reaches(FULL, W(), [GOAL])
    R.use(R.of_pack(pack))


def test_cannot_tell_after_the_plan_is_not_refuted(pack):
    no_store = types.SimpleNamespace(holding=None, arrived=None, found=None, opened=set(), on=set())
    after = dk.after(FULL, no_store, {}, set(), lambda st, w: types.SimpleNamespace(**{**vars(w), "holding": (
        st["object"] if st["action"] == "Pick" else None if st["action"] == "Place" else w.holding)}))
    assert after[1] == {"bracket": JIG}
    assert dk.unmet(pack, MOUNT, GOAL, after) == [], "fastened cannot be told on a world with no fact store: not refuted"


def test_the_run_s_placed_map_is_the_start_of_the_plan(loop):
    loop._ctx = types.SimpleNamespace(placed={"bracket": JIG})
    assert loop._reaches(steps("Fasten bracket"), W(arrived=JIG), [GOAL])
    del loop._ctx
    assert not loop._reaches(steps("Fasten bracket"), W(arrived=JIG), [GOAL])


def test_a_release_action_of_the_robot_places_the_object_where_the_robot_is(pack):
    after = dk.after(steps("Pick bracket", f"MoveTo {JIG}", "Handover"), W(found="bracket"), {}, {"Handover"},
                     action_model.apply)
    assert after[1] == {"bracket": JIG} and after[0].holding is None


def test_the_read_back_reads_the_phrase_key(pack, loop):
    said = AssistLoop.summary([GOAL])
    assert str(said) == "I will mount the bracket on the jig in the cell a."
    one = said.slots["what"].parts[0]
    assert one.key == "rb_mount" and one.slots == {"object": "bracket", "place": JIG}
    assert gs.phrase(dict(GOAL, said="Halter"), None, str, pack).slots["object"] == "Halter"
    bare = resolve(None, None, raw=dict(PACK, phrases={"rb_mount": {"ko": "x {object}", "slots": ["object"], "assumed": True}}))
    assert str(gs.phrase(GOAL, None, str, bare)) == "mount the bracket", "no English text: the old fallback text"


def _verify(loop, ok):
    vg = VerifyGoals()
    vg.robot, vg.cfg = loop.robot, {"goal_check": "observe"}
    vg._observe_goal = lambda r, g: (ok, "could not look for the bracket (perception_unavailable: camera is down)", "cam")
    vg._cancelled = lambda r: None
    r = types.SimpleNamespace(completed=[], goals=[GOAL], world=W(arrived=JIG), goal_checks=[], placed={"bracket": JIG},
                              ev=lambda kind, **kw: dict(kw, event=kind))
    gen, out = vg._observe_goals(r), None
    try:
        while True:
            next(gen)
    except StopIteration as e:
        out = e.value
    return out, r


def test_unknown_is_failure_turns_cannot_tell_into_an_unmet_goal(loop):
    unmet, r = _verify(loop, None)
    assert unmet == [{"code": "perception_unavailable", "device": "cam", "evidence": {
        "goal": f"mount bracket {JIG}", "observed": "could not look for the bracket (perception_unavailable: camera is down)"}}]
    assert r.placed == {"bracket": JIG} and r.goal_checks[0]["ok"] is None, "the belief is not withdrawn: it was not seen"
    assert _verify(loop, True)[0] == []
    loop.robot = PackRobot(default_pack())
    assert _verify(loop, None)[0] == [], "unknown_is belief (the default): the goal stays on the belief"
