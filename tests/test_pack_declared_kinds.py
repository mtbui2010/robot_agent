"""Step S29: a goal kind that a schema-2 pack declares with no ``rules`` callable is decided by its ``holds`` list
(robot_agent/core/pack/declared_kinds.py, asked by core/pack/goal_semantics.py), its ``unknown_is`` (core/pack/verifiers.py,
core/planning/phases/verify_goals.py) and its ``phrase`` key.

(1) The default data: no pack a robot gets without declaring one has a kind without rules, and none declares
``unknown_is: failure``, so the old code path is the only one taken (the differential grids of tests/test_goal_semantics_diff.py,
tests/test_goal_phrase_diff.py and tests/test_pack_verifiers.py still compare it with the reference copies); a kind no pack
declares stays vacuously reached and holding. (2) The opt-in on a made-up pack: holds, reached by simulation, missing, the
two load errors, the phrase key, unknown_is. Every fact of the made-up pack is assumed."""

from types import SimpleNamespace

import pytest

from robot_agent.contract.pack import PackError
from robot_agent.core.pack import action_rules, declared_kinds as dk, goal_semantics as gs, verifiers as pv
from robot_agent.core.pack.loader import default_pack, index, resolve, shipped
from robot_agent.core.planning.base import WorldState

JIG, BIN = "jig@cell a", "bin@cell a"
PACK = {"schema": 2, "name": "made_up_mount", "extends": "household", "assumed": True,
        "predicates": {"fastened": {"kind": "fact", "args": ["object"], "scopes": ["effect", "goal"]},
                       "seen": {"kind": "fact", "args": ["object"], "scopes": ["goal"]}},
        "actions": {"Fasten": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                               "effects": {"add": ["fastened(?part)"]}}},
        "goal_kinds": {"mount": {"needs": [["Pick"], "Place", "Fasten"], "holds": ["placed(?object, ?to)", "fastened(?object)"],
                                 "observe": "std.find_at_destination", "unknown_is": "failure", "phrase": "rb_mount",
                                 "conflicts": "none"},
                       "tag": {"needs": ["Find"], "holds": ["seen(?object)"]},
                       "hold": {"needs": ["Pick"], "holds": ["holding(?object)"], "phrase": "rb_hold"}},
        "phrases": {"rb_mount": {"slots": ["object", "place"], "en": "mount the {object} on the {place}"},
                    "rb_hold": {"slots": ["object"], "en": "hold the {object} {missing}"}}}
MOUNT = {"type": "mount", "object": "bracket", "from": "", "to": JIG}
GOOD = [{"action": "MoveTo", "object": BIN}, {"action": "Pick", "object": "bracket"}, {"action": "MoveTo", "object": JIG},
        {"action": "Fasten", "object": "bracket"}, {"action": "Place", "object": JIG}]
NO_FASTEN = [s for s in GOOD if s["action"] != "Fasten"]


@pytest.fixture(scope="module")
def pack():
    return resolve(None, None, raw=PACK)


@pytest.fixture
def active(pack):
    before = action_rules.use(action_rules.of_pack(pack))
    yield pack
    action_rules.use(before)


def _loop(pack, released=()):
    return SimpleNamespace(robot=SimpleNamespace(pack=pack, release_actions=lambda: list(released)))


def _seams():
    return gs.Seams(lambda steps, world: None, lambda goal, got: False)


def _world(**facts):
    w = WorldState()
    w.facts = {k: set(v) for k, v in facts.items()}
    return w


# ── (1) the default data ────────────────────────────────────────────────────────────────────────────────────────────
def _shipped_packs():
    out = [default_pack()]
    for name in index()["packs"]:
        out.append(shipped(name))
    return out


def test_no_shipped_pack_declares_a_kind_without_rules_or_with_unknown_is_failure():
    for p in _shipped_packs():
        kinds = p.table("goal_kinds") if hasattr(p, "table") else (getattr(p, "tables", None) or {}).get("goal_kinds", {})
        for kind, e in kinds.items():
            assert e.get("rules"), (kind, "a shipped kind names its rules object")
            assert dk.declared(default_pack(), kind) is None and dk.phrase_key(default_pack(), kind) is None
            assert e.get("unknown_is", "belief") == "belief" and not pv.unknown_fails(default_pack(), kind)


def test_a_kind_no_pack_declares_stays_vacuously_reached_and_holding_with_no_text():
    goal, loop = {"type": "polish", "object": "apple", "from": "", "to": ""}, _loop(default_pack())
    w = WorldState()
    assert gs.reaches(loop, [], w, [goal], _seams()) is True
    assert gs.missing(loop, [], w, [goal], _seams()) == []
    assert gs.holds(loop, [goal], gs.Belief(w, {})) is True
    assert gs.phrase(goal, None, lambda k: k) == "polish the apple"
    assert pv.unknown_fails(default_pack(), "polish") is False and pv.unknown_fails(None, "polish") is False


def test_without_an_active_pack_the_read_back_of_an_unknown_kind_is_the_old_text(pack):
    assert action_rules.active() is action_rules.EMPTY
    assert gs.phrase(MOUNT, None, lambda k: k.replace("@", " in the ")) == "mount the bracket"


# ── (2) the opt-in: holds ───────────────────────────────────────────────────────────────────────────────────────────
def test_holds_needs_every_predicate_true_in_the_belief(pack):
    loop = _loop(pack)
    assert gs.holds(loop, [MOUNT], gs.Belief(_world(fastened=[("bracket",)]), {"bracket": JIG})) is True
    assert gs.holds(loop, [MOUNT], gs.Belief(_world(), {"bracket": JIG})) is False                  # not fastened
    assert gs.holds(loop, [MOUNT], gs.Belief(_world(fastened=[("bracket",)]), {"bracket": BIN})) is False
    held = _world(fastened=[("bracket",)])
    held.holding = "bracket"
    assert gs.holds(loop, [MOUNT], gs.Belief(held, {"bracket": JIG})) is False                       # still in the hand


NO_STORE = SimpleNamespace(holding=None, arrived=None)              # a belief with no fact store: a fact cannot be told


def test_cannot_tell_is_not_holding(pack):
    tag = {"type": "tag", "object": "bracket", "from": "", "to": ""}
    vals = dk.values(pack, dk.declared(pack, "tag"), tag, NO_STORE, {})
    assert vals == [("seen(?object)", None)]
    assert gs.holds(_loop(pack), [tag], gs.Belief(NO_STORE, {})) is False
    assert gs.holds(_loop(pack), [tag], gs.Belief(_world(seen=[("bracket",)]), {})) is True
    no_slot = {"type": "mount", "object": "", "from": "", "to": JIG}
    assert dk.values(pack, dk.declared(pack, "mount"), no_slot, WorldState(), {})[1][1] is None     # an unbound slot


# ── reached by simulation, missing ──────────────────────────────────────────────────────────────────────────────────
def test_a_plan_reaches_the_goal_when_the_steps_and_declared_effects_make_the_list_true(active):
    loop, w = _loop(active), WorldState()
    assert gs.reaches(loop, GOOD, w, [MOUNT], _seams()) is True
    assert gs.missing(loop, GOOD, w, [MOUNT], _seams()) == []
    assert w.facts == {} and w.holding is None                        # the belief itself is not changed


def test_missing_has_one_line_per_refuted_predicate(active):
    loop = _loop(active)
    assert gs.reaches(loop, NO_FASTEN, WorldState(), [MOUNT], _seams()) is False
    assert gs.missing(loop, NO_FASTEN, WorldState(), [MOUNT], _seams()) == ["Make fastened(bracket) true (mount bracket)"]
    assert gs.missing(loop, [], WorldState(), [MOUNT], _seams()) == [
        "Make placed(bracket, jig@cell a) true (mount bracket)", "Make fastened(bracket) true (mount bracket)"]
    assert gs.reaches(loop, [], WorldState(), [MOUNT], _seams()) is False   # not vacuously done before the first step


def test_cannot_tell_is_not_refuted_by_a_plan(pack):
    tag = {"type": "tag", "object": "bracket", "from": "", "to": ""}
    assert gs.reaches(_loop(pack), [], NO_STORE, [tag], _seams()) is True
    assert gs.missing(_loop(pack), [], NO_STORE, [tag], _seams()) == []
    assert gs.missing(_loop(pack), [], WorldState(), [tag], _seams()) == ["Make seen(bracket) true (tag bracket)"]


def test_a_predicate_outside_the_goal_scope_cannot_be_told(pack):
    import dataclasses
    tables = dict(pack.tables, predicates=dict(pack.table("predicates"), seen={"kind": "fact", "args": ["object"],
                                                                              "scopes": ["effect"]}))
    other = dataclasses.replace(pack, tables=tables)
    tag = {"type": "tag", "object": "bracket", "from": "", "to": ""}
    assert dk.values(other, dk.declared(other, "tag"), tag, _world(seen=[("bracket",)]), {}) == [("seen(?object)", None)]


def test_a_release_action_of_the_robot_places_the_held_object_where_the_robot_is(active):
    hand = [{"action": "MoveTo", "object": BIN}, {"action": "Pick", "object": "bracket"}, {"action": "MoveTo", "object": JIG},
            {"action": "Fasten", "object": "bracket"}, {"action": "Handover", "object": ""}]
    assert gs.reaches(_loop(active), hand, WorldState(), [MOUNT], _seams()) is False      # Handover is no release here
    assert gs.reaches(_loop(active, ["Handover"]), hand, WorldState(), [MOUNT], _seams()) is True


def test_after_is_pure_and_starts_with_nothing_placed():
    calls = []

    def apply(st, w, released):
        calls.append((st["action"], released))
        out = w.copy()
        out.holding = "bracket" if st["action"] == "Pick" else None if st["action"] == "Place" else out.holding
        return out
    w = WorldState()
    got, placed = dk.after([{"action": "Pick", "object": "bracket"}, {"action": "Place", "object": JIG}], w, {"Give"}, apply)
    assert placed == {"bracket": JIG} and got is not w and calls == [("Pick", {"Give"}), ("Place", {"Give"})]


# ── load errors ─────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("entry, text", [
    ({"needs": ["Pick"]}, "needs `rules`"),
    ({"needs": ["Pick"], "holds": ["holding(?object)"], "unknown_is": "failure"}, "needs an `observe` verifier"),
    ({"needs": ["Pick"], "holds": ["holding(?object)"], "unknown_is": "failure", "observe": None}, "needs an `observe` verifier"),
])
def test_a_kind_with_neither_rules_nor_holds_or_unknown_is_failure_without_observe_is_a_load_error(entry, text):
    raw = {"schema": 2, "name": "made_up_bad", "extends": "household", "assumed": True, "goal_kinds": {"grab": entry}}
    with pytest.raises(PackError) as e:
        resolve(None, None, raw=raw)
    assert text in str(e.value)


# ── the read-back phrase ────────────────────────────────────────────────────────────────────────────────────────────
def test_the_read_back_reads_the_kinds_phrase_key(pack, active):
    nice = lambda k: k.replace("@", " in the ")      # noqa: E731
    for given in (pack, None):                       # the robot's pack, else the pack of the run's active rules
        p = gs.phrase(dict(MOUNT, said="the bracket part"), None, nice, given)
        assert (p.key, p.slots, str(p)) == ("rb_mount", {"object": "the bracket part", "place": JIG},
                                            "mount the bracket on the jig in the cell a")


def test_a_phrase_with_a_slot_it_cannot_fill_reads_the_old_text(pack):
    p = gs.phrase({"type": "hold", "object": "bracket", "from": "", "to": ""}, None, lambda k: k, pack)
    assert (p.key, str(p)) == ("rb_hold", "hold the bracket")


def test_a_kind_with_rules_keeps_its_own_phrase(pack):
    g = {"type": "on", "object": "cup", "from": "", "to": JIG}
    assert gs.phrase(g, None, lambda k: k, pack).key == gs.phrase(g, None, lambda k: k).key == "rb_on"


# ── unknown_is ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_unknown_is_failure_is_read_per_kind(pack):
    assert pv.unknown_fails(pack, "mount") is True
    assert pv.unknown_fails(pack, "tag") is False and pv.unknown_fails(pack, "on") is False


def _observe(pack, goals, ok):
    from robot_agent.core.planning.phases.verify_goals import VerifyGoals

    class Run:
        completed, goal_checks, placed = [], [], {}
        world = SimpleNamespace(arrived=JIG, to_dict=lambda: {})
        memory = SimpleNamespace(see=lambda *a: None)

        def ev(self, kind, **kw):
            return (kind, kw)

    class Loop(VerifyGoals):
        cfg = {"goal_check": "observe"}
        robot = SimpleNamespace(pack=pack)

        def _observe_goal(self, r, g):
            return ok, "could not look for it", "camera"

        def _cancelled(self, r):
            return None
    r = Run()
    r.goals = goals
    gen, out = Loop()._observe_goals(r), None
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        out = stop.value
    return out


def test_a_verifier_that_cannot_tell_leaves_an_unknown_is_failure_goal_unmet(pack):
    unmet = _observe(pack, [MOUNT], None)
    assert unmet == [{"code": "perception_unavailable", "device": "camera",
                      "evidence": {"goal": "mount bracket jig@cell a", "observed": "could not look for it"}}]
    assert _observe(pack, [MOUNT], True) == []


def test_a_cannot_tell_of_a_belief_kind_stays_on_the_belief(pack):
    on = {"type": "on", "object": "cup", "from": "", "to": JIG}
    assert _observe(pack, [on], None) == [] and _observe(default_pack(), [on], None) == []
