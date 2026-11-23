"""Declared rules of pack actions (core/pack/action_rules.py, Step S26) and the action model wrapper that reads them.

The default data: the packs a robot gets without declaring one (core + standard household) declare no ``rules: declared``
action and no typed ``args``, so their rules are EMPTY and the wrapper's ``check`` / ``apply`` are embodiplan's exactly
(a differential test over a grid of steps and worlds against ``embodiplan.action_model`` itself, the old wrapper's body).
Then the opt-in: unmet ``pre`` predicates (fact and registered evaluator) become ``pre_<name>`` violations, ``effects`` add
and remove facts, typed arguments are checked and reach the skill only when its params declare them."""

import itertools

import pytest

from robot_agent.contract import Unbindable
from robot_agent.core.pack import action_rules as R
from robot_agent.core.pack.loader import default_pack, resolve
from robot_agent.core.planning import action_model
from robot_agent.core.planning.base import WorldState

PACK = {"schema": 2, "name": "made_up_line", "extends": "household", "assumed": True,
        "predicates": {"inspected": {"kind": "fact", "args": ["object"], "scopes": ["tree", "effect", "pre", "goal"]},
                       "clamped": {"kind": "fact", "args": ["object?", "place?"], "scopes": ["effect", "pre"]}},
        "actions": {"Inspect": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                                "effects": {"add": ["inspected(?part)"]}},
                    "Clamp": {"arg": {"name": "station", "type": "place"}, "rules": "declared", "pre": ["holding(*)"],
                              "effects": {"add": ["clamped(?held, ?station)"]}},
                    "Unclamp": {"arg": {"name": "station", "type": "place"}, "rules": "declared",
                                "effects": {"del": ["clamped(*, ?station)"]}},
                    "Fasten": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                               "pre": ["inspected(?part)", "not clamped(?part, ?station)"],
                               "args": {"torque_nm": {"type": "number", "min": 0, "max": 10, "default": 2},
                                        "station": {"type": "string"},
                                        "side": {"type": "enum", "values": ["left", "right"], "required": True}}},
                    "Wave": {"arg": {"name": "who", "type": "text"}, "args": {"times": {"type": "integer", "min": 1}}}}}


@pytest.fixture
def rules():
    return R.of_pack(resolve(None, None, raw=PACK))


@pytest.fixture
def active_rules(rules):
    before = R.use(rules)
    yield rules
    R.use(before)


# ── the default data ────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_default_packs_declare_no_rules_and_no_typed_arguments():
    assert R.of_pack(default_pack()) is R.EMPTY
    assert R.of_pack(None) is R.EMPTY
    assert R.active() is R.EMPTY                         # nothing is active outside a run


def test_a_robot_whose_pack_cannot_be_read_has_no_rules():
    class Broken:
        @property
        def pack(self):
            raise RuntimeError("no pack here")
    assert R.of_robot(Broken()) is R.EMPTY and R.of_robot(object()) is R.EMPTY


@pytest.mark.parametrize("pkg", ["fakebot", "fakehome", "fakeline"])
def test_a_schema_1_robot_is_not_resolved_for_its_rules_and_a_pack_in_use_is_read(pkg):
    """of_robot reads only a pack already in use: a robot with no ``pack:`` section (schema 1) stays unresolved (a run of
    today's robots does no new work); after ``use_pack`` its declared rules are the ones read."""
    from robot_agent.core.planning.robot import RobotView
    view = RobotView.load(pkg)
    assert R.of_robot(view) is R.EMPTY and vars(view).get("_pack") is None
    assert R.of_pack(view.pack) is R.EMPTY                 # the resolved default pack declares no rule either
    view.use_pack(PACK)
    got = R.of_robot(view)
    assert set(got.declared) == {"Inspect", "Clamp", "Unclamp", "Fasten"} and set(got.args) == {"Fasten", "Wave"}


def _grid():
    """Steps of the model's vocabulary, a custom action and an empty one, over worlds of every hand / place / container state."""
    steps = [{"action": a, "object": o} for a, o in itertools.product(
        ("MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close", "TurnOn", "TurnOff", "Wait", "Inspect", "Fasten", ""),
        ("", "desk", "cup", "drawer"))]
    worlds = [WorldState(arrived=a, found=f, holding=h, opened=set(op), on=set(on))
              for a, f, h, op, on in itertools.product((None, "desk", "drawer"), (None, "cup"), (None, "cup"), ((), ("drawer",)),
                                                    ((), ("lamp",)))]
    kws = [{}, {"capabilities": {"hands": 1, "find_reaches": False, "release_actions": ["Handover"]}},
           {"actions": ["MoveTo", "Find", "Pick", "Place", "Inspect"], "openable": {"drawer"}, "containers": {"drawer"}}]
    return steps, worlds, kws


def _model():
    from embodiplan import action_model as am
    return am


@pytest.mark.parametrize("declared", [False, True], ids=["no_rules", "rules_of_other_actions"])
def test_check_and_apply_are_the_models_for_every_action_the_pack_does_not_declare(declared, rules):
    """Differential: the wrapper against embodiplan.action_model (the old wrapper's body). With rules active, only the
    actions they do not declare are compared (here: every model action)."""
    am, (steps, worlds, kws) = _model(), _grid()
    before = R.use(rules if declared else R.EMPTY)
    try:
        for st, w, kw in itertools.product(steps, worlds, kws):
            if declared and st["action"] in rules.declared:
                continue
            assert action_model.check(st, w, **kw) == am.check(st, w, **kw), (st, w, kw)
            cap = {k: v for k, v in kw.items() if k == "capabilities"}
            assert action_model.apply(st, w, **cap) == am.apply(st, w, **cap), (st, w, kw)
            w2, w3 = w.copy(), w.copy()
            action_model.apply(st, w2, inplace=True, **cap)
            am.apply(st, w3, inplace=True, **cap)
            assert w2 == w3 and w2.facts == {}
    finally:
        R.use(before)


# ── preconditions ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_an_unmet_fact_precondition_is_a_pre_violation_and_a_met_one_is_none(active_rules):
    st = {"action": "Fasten", "object": "bracket", "args": {"side": "left", "station": "jig"}}
    w = WorldState()
    vs = action_model.check(st, w, actions=["Fasten"])
    assert [(v.code, v.is_error) for v in vs] == [("pre_inspected", True)]
    assert vs[0].reason == "the precondition inspected(bracket) does not hold"
    w.facts = {"inspected": {("bracket",)}}
    assert action_model.check(st, w, actions=["Fasten"]) == []
    w.facts["clamped"] = {("bracket", "jig")}           # a negated precondition
    assert [v.code for v in action_model.check(st, w, actions=["Fasten"])] == ["pre_clamped"]


def test_a_precondition_that_cannot_be_told_is_no_violation(rules):
    st = {"action": "Fasten", "object": "bracket"}       # ?station is not bound: not clamped(...) cannot be told
    w = WorldState(facts={"inspected": {("bracket",)}})
    assert R.violations(rules, st, w) == []

    class NoFacts:                                       # the planner's state has no fact store
        holding = None
    assert R.violations(rules, st, NoFacts()) == []
    assert R.holds(rules, "unknown(?part)", {"part": "x"}, w) is None


def test_a_registered_evaluator_reads_the_world_and_the_wildcard(rules):
    st = {"action": "Clamp", "object": "jig"}
    assert [c for c, _ in R.violations(rules, st, WorldState())] == ["pre_holding"]
    assert R.violations(rules, st, WorldState(holding="bracket")) == []
    assert R.holds(rules, "holding(cup)", {}, WorldState(holding="cup")) is True
    assert R.holds(rules, "holding(cup, *)", {}, WorldState(holding="cup")) is None   # a wildcard among values: cannot tell
    assert R.holds(rules, "placed(cup)", {}, WorldState()) is None                     # the evaluator fails on one argument
    assert R.holds(rules, "not holding(cup)", {}, WorldState(holding="cup")) is False


def test_the_symbolic_guard_reports_a_declared_precondition_of_an_action_the_model_does_not_know(active_rules):
    from robot_agent.core.planning.verify import symbolic_check
    ok, why = symbolic_check({"action": "Fasten", "object": "bracket"}, WorldState())
    assert not ok and why == "the precondition inspected(bracket) does not hold"
    R.use(R.EMPTY)
    assert symbolic_check({"action": "Fasten", "object": "bracket"}, WorldState()) == (True, "")


# ── effects ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_effects_add_and_remove_facts_through_apply(active_rules):
    w = WorldState(holding="bracket")
    after = action_model.apply({"action": "Inspect", "object": "bracket"}, w)
    assert after.facts == {"inspected": {("bracket",)}} and w.facts == {}          # a copy unless inplace
    action_model.apply({"action": "Clamp", "object": "jig"}, after, inplace=True)
    assert after.facts["clamped"] == {("bracket", "jig")}                          # ?held: the hand before the step
    after.facts["clamped"].add(("bolt", "press"))
    action_model.apply({"action": "Unclamp", "object": "jig"}, after, inplace=True)
    assert after.facts["clamped"] == {("bolt", "press")}                           # del with a wildcard: every match


def test_an_effect_on_a_world_without_a_fact_store_or_an_unbound_variable_changes_nothing(rules):
    class NoFacts:
        holding = None
    w = NoFacts()
    R.apply_effects(rules, {"action": "Inspect", "object": "x"}, {"part": "x"}, w)
    assert not hasattr(w, "facts")
    w2 = WorldState()
    R.apply_effects(rules, {"action": "Clamp", "object": "jig"}, R.binding(rules, {"action": "Clamp", "object": "jig"}, w2), w2)
    assert w2.facts == {}                                                          # ?held was empty: skipped


def test_the_gate_simulation_sees_a_fact_made_true_by_an_earlier_step(active_rules):
    plan = [{"action": "Inspect", "object": "bracket"}, {"action": "Fasten", "object": "bracket"}]
    w = WorldState()
    codes = []
    for st in plan:
        codes += [v.code for v in action_model.check(st, w, actions=["Inspect", "Fasten"])]
        action_model.apply(st, w, inplace=True)
    assert codes == []


# ── typed arguments ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_typed_arguments_reach_the_skill_only_when_its_params_declare_them(rules):
    st = {"action": "Fasten", "object": "bracket", "args": {"side": "left", "station": "jig"}}
    assert R.typed_params(rules, st, {"inputs", "torque_nm", "side"}) == {"torque_nm": 2, "side": "left"}
    assert R.typed_params(rules, st, {"inputs"}) == {}
    assert R.typed_params(rules, dict(st, args={"side": "right", "torque_nm": 7.5}), {"torque_nm", "side"}) == \
        {"torque_nm": 7.5, "side": "right"}


@pytest.mark.parametrize("args, text", [
    ({"side": "up"}, "is not one of"), ({"side": "left", "torque_nm": 11}, "outside"), ({"side": "left", "torque_nm": "3"}, "not a number"),
    ({"side": "left", "speed": 1}, "no argument speed"), ({}, "required"), ("left", "must be a mapping")])
def test_a_wrong_typed_argument_is_unbindable(rules, args, text):
    with pytest.raises(Unbindable, match=text):
        R.typed_params(rules, {"action": "Fasten", "object": "bracket", "args": args}, {"side", "torque_nm"})


def test_integer_and_boolean_types_and_an_action_without_args(rules):
    assert R.typed_params(rules, {"action": "Wave", "args": {"times": 2}}, {"times"}) == {"times": 2}
    for bad in (1.5, True, 0):
        with pytest.raises(Unbindable):
            R.typed_params(rules, {"action": "Wave", "args": {"times": bad}}, {"times"})
    assert R.typed_params(rules, {"action": "Inspect", "args": {"anything": 1}}, {"anything"}) == {}   # declares no args: as before
    assert R.typed_params(R.EMPTY, {"action": "Fasten", "args": {"side": "up"}}, {"side"}) == {}


def test_bind_adds_the_typed_arguments_of_the_active_rules():
    """ExecHelpers._bind: the adapter's params plus the typed arguments the skill declares; nothing with EMPTY rules."""
    import types
    from robot_agent.core.planning.phases.exec_helpers import ExecHelpers
    skill = types.SimpleNamespace(name="fasten", params={"properties": {"inputs": {}, "side": {}}})
    robot = types.SimpleNamespace(bind=lambda st, w: ("fasten", {"inputs": st["object"]}),
                                  manifest=types.SimpleNamespace(skills=[skill]))
    h = ExecHelpers()
    h.robot, h.cfg, h._find_params = robot, {"find_min_conf": 0, "find_verify": False}, set()
    st = {"action": "Fasten", "object": "bracket", "args": {"side": "right"}}
    assert h._bind(st, WorldState()) == ("fasten", {"inputs": "bracket"})
    before = R.use(R.of_pack(resolve(None, None, raw=PACK)))
    try:
        assert h._bind(st, WorldState()) == ("fasten", {"inputs": "bracket", "side": "right"})
        with pytest.raises(Unbindable):
            h._bind(dict(st, args={"side": "up"}), WorldState())
    finally:
        R.use(before)


def test_use_returns_the_rules_it_replaces(rules):
    before = R.use(rules)
    try:
        assert R.active() is rules and R.use(rules) is rules
    finally:
        assert R.use(before) is rules
    assert R.active() is before
    assert R.use("not rules") is before and R.active() is R.EMPTY
    R.use(before)
