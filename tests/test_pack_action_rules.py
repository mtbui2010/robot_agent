"""Step S26: declared preconditions and effects of pack actions, and typed arguments at bind (robot_agent/core/pack/action_rules.py,
the wrapper robot_agent/core/planning/action_model.py, ExecHelpers._bind, RunContext).

Proof that nothing changed for a robot that declares no rules: (1) the default data declares none (no shipped action has
``rules: declared``, ``pre``, ``effects`` or ``args``; the rules of the default pack and of every made-up schema-1 robot are
EMPTY); (2) the wrapper gives exactly the answer of the old wrapper (a verbatim copy in tests/legacy_ref/s26_world_state.py) on
a grid of steps, worlds and keywords, with no rules active and with rules active for other actions; (3) a schema-1 robot is
not resolved for this. Then the new behaviour on made-up packs: unmet ``pre`` (fact and evaluated predicates, ``not``, ``*``,
``?held``), effects that add and remove facts, typed arguments checked and passed only when the skill declares them, the run
start clearing the facts and making the robot's rules active until its end. Made-up robots and packs only."""

import itertools
import types

import pytest

from legacy_ref import s26_world_state as old
from robot_agent.contract import Unbindable
from robot_agent.core.pack import action_rules as R
from robot_agent.core.pack import loader
from robot_agent.core.planning import action_model, loop as loop_mod
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.robot import RobotView

PACKAGES = ("fakebot", "fakehome", "fakeline")
LINE_PACK = {"schema": 2, "name": "rules_line", "extends": "household", "assumed": True,
             "predicates": {"inspected": {"kind": "fact", "args": ["object"], "scopes": ["effect", "pre"]},
                            "seated": {"kind": "fact", "args": ["object", "place?"], "scopes": ["effect", "pre"]}},
             "actions": {"Inspect": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                                     "effects": {"add": ["inspected(?part)"]}},
                         "Fasten": {"arg": {"name": "part", "type": "object"}, "rules": "declared",
                                    "pre": ["inspected(?part)", "holding(*)", "not seated(?part, *)"],
                                    "effects": {"add": ["seated(?held, ?station)"], "del": ["inspected(?part)"]},
                                    "args": {"torque": {"type": "number", "min": 0, "max": 10, "default": 2},
                                             "arm": {"type": "enum", "values": ["left", "right"]},
                                             "station": {"type": "string"},
                                             "count": {"type": "integer", "required": False},
                                             "slow": {"type": "boolean"}}}}}


@pytest.fixture
def line_rules():
    view = RobotView.load("fakeline")
    view.use_pack(LINE_PACK)
    return R.of_robot(view)


@pytest.fixture(autouse=True)
def _no_active_rules():
    before = R.use(None)
    yield
    R.use(before)


# -- (1) the default data declares no rules --------------------------------------------------------------------------------
def test_no_shipped_action_declares_rules_pre_effects_or_args():
    for name in loader.index()["packs"]:
        for action, e in loader.shipped(name).tables.get("actions", {}).items():
            assert not {"rules", "pre", "effects", "args"} & set(e), (name, action)
    assert R.of_pack(loader.default_pack()) is R.EMPTY and R.EMPTY.empty


@pytest.mark.parametrize("pkg", PACKAGES)
def test_a_schema_1_robot_has_no_rules_and_is_not_resolved_for_them(pkg):
    view = RobotView.load(pkg)
    assert "_pack" not in vars(view)
    assert R.of_robot(view) is R.EMPTY
    assert "_pack" not in vars(view), "reading the rules must not resolve a schema-1 pack"
    assert R.of_pack(view.pack) is R.EMPTY and R.of_robot(view) is R.EMPTY


def test_a_robot_that_is_no_robot_view_has_no_rules():
    assert R.of_robot(None) is R.EMPTY and R.of_robot(types.SimpleNamespace()) is R.EMPTY


# -- (2) the wrapper is the old one when no rule applies ---------------------------------------------------------------------
STEPS = [{"action": a, "object": o} for a, o in itertools.product(
    ("MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close", "TurnOn", "Wait", "Say", "Dock", "Inspect", "Fasten", ""),
    ("jig", "bracket", "drawer", ""))]
WORLDS = [dict(), dict(arrived="jig"), dict(found="bracket", arrived="bin"), dict(holding="bracket", arrived="jig"),
          dict(opened={"drawer"}, holding="bolt"), dict(on={"lamp"}, found="drawer", arrived="drawer")]
KWS = [dict(), dict(capabilities={"hands": 2, "find_reaches": True}), dict(openable={"drawer"}, containers={"drawer"}),
       dict(actions={"MoveTo", "Find", "Pick", "Place", "Say"}, object_places={"bracket": "bin"})]


def _same(step, fields, kw):
    new_w, old_w = WorldState(**fields), old.WorldState(**fields)
    got, want = action_model.check(step, new_w, **kw), old.check(step, old_w, **kw)
    assert [(v.code, v.reason, v.severity) for v in got] == [(v.code, v.reason, v.severity) for v in want], (step, fields, kw)
    caps = {k: v for k, v in kw.items() if k == "capabilities"}
    assert action_model.apply(step, new_w, **caps).to_dict() == old.apply(step, old_w, **caps).to_dict()
    action_model.apply(step, new_w, inplace=True, **caps)
    old.apply(step, old_w, inplace=True, **caps)
    assert new_w.to_dict() == old_w.to_dict() and new_w.facts == {}


@pytest.mark.parametrize("step", STEPS, ids=lambda s: f"{s['action'] or 'none'}-{s['object'] or 'none'}")
def test_with_no_rules_the_wrapper_answers_as_the_old_one(step):
    assert R.active() is R.EMPTY
    for fields, kw in itertools.product(WORLDS, KWS):
        _same(step, fields, kw)


@pytest.mark.parametrize("step", [s for s in STEPS if s["action"] not in ("Inspect", "Fasten")],
                         ids=lambda s: f"{s['action'] or 'none'}-{s['object'] or 'none'}")
def test_with_rules_for_other_actions_the_wrapper_answers_as_the_old_one(step, line_rules):
    R.use(line_rules)
    for fields, kw in itertools.product(WORLDS, KWS):
        _same(step, fields, kw)


# -- (3) the declared rules -------------------------------------------------------------------------------------------------
def test_the_rules_of_a_pack_are_its_declared_actions_only(line_rules):
    assert set(line_rules.declared) == {"Inspect", "Fasten"} and set(line_rules.args) == {"Fasten"}
    assert line_rules.declared["Fasten"]["arg"] == "part" and not line_rules.empty


def test_unmet_preconditions_are_violations_named_after_the_predicate(line_rules):
    st = {"action": "Fasten", "object": "bracket"}
    assert [c for c, _ in R.violations(line_rules, st, WorldState())] == ["pre_inspected", "pre_holding"]
    w = WorldState(holding="bolt", facts={"inspected": {("bracket",)}})
    assert R.violations(line_rules, st, w) == []
    w.facts["seated"] = {("bracket", "jig")}
    assert [c for c, _ in R.violations(line_rules, st, w)] == ["pre_seated"]
    assert "not seated(?part, *)" in R.violations(line_rules, st, w)[0][1]
    assert R.violations(line_rules, {"action": "Pick", "object": "bracket"}, WorldState()) == []


def test_cannot_tell_is_no_violation(line_rules):
    assert R.violations(line_rules, {"action": "Fasten", "object": ""}, WorldState(holding="x")) == []   # ?part unbound
    assert R.holds(line_rules, "nosuch(?part)", {"part": "a"}, WorldState()) is None
    assert R.holds(line_rules, "holding(*, *)", {}, WorldState()) is None


def test_the_wrapper_reports_them_as_errors_first(line_rules):
    R.use(line_rules)
    st = {"action": "Fasten", "object": "bracket"}
    found = action_model.check(st, WorldState(), actions={"Fasten"})
    assert [v.code for v in found] == ["pre_inspected", "pre_holding"] and all(v.is_error for v in found)
    from robot_agent.core.planning.verify import symbolic_check
    ok, why = symbolic_check(st, WorldState())
    assert not ok and "inspected(?part)" in why


def test_effects_add_and_remove_facts_through_the_wrapper(line_rules):
    R.use(line_rules)
    w = WorldState(arrived="jig", holding="bracket")
    after = action_model.apply({"action": "Inspect", "object": "bracket"}, w)
    assert after.facts == {"inspected": {("bracket",)}} and w.facts == {}, "not in place: the copy only"
    action_model.apply({"action": "Inspect", "object": "bracket"}, w, inplace=True)
    assert action_model.check({"action": "Fasten", "object": "bracket"}, w, actions={"Fasten"}) == []
    action_model.apply({"action": "Fasten", "object": "bracket", "args": {"station": "jig"}}, w, inplace=True)
    assert w.facts == {"seated": {("bracket", "jig")}}, "?held is read before the step; inspected was deleted"
    assert w.to_dict()["facts"] == {"seated": [["bracket", "jig"]]}


def test_an_effect_with_an_unbound_variable_or_a_wildcard_adds_nothing_and_del_matches_wildcards(line_rules):
    w = WorldState(facts={"seated": {("a", "x"), ("a", "y"), ("b", "x")}})
    R.apply_effects(line_rules, {"action": "Fasten", "object": "c"}, {"part": "c"}, w)       # ?held, ?station unbound
    assert w.facts == {"seated": {("a", "x"), ("a", "y"), ("b", "x")}}
    rules = R.Rules({"Clear": {"arg": "part", "pre": (), "add": ("seated(?part, *)",), "del": ("seated(?part, *)",)}},
                    {}, line_rules.preds, line_rules.pack)
    R.apply_effects(rules, {"action": "Clear", "object": "a"}, {"part": "a"}, w)
    assert w.facts == {"seated": {("b", "x")}}
    R.apply_effects(rules, {"action": "Clear", "object": "b"}, {"part": "b"}, w)
    assert w.facts == {}


def test_effects_on_a_world_with_no_fact_store_do_nothing(line_rules):
    w = types.SimpleNamespace(holding=None)
    R.apply_effects(line_rules, {"action": "Inspect", "object": "a"}, {"part": "a"}, w)
    assert not hasattr(w, "facts")


# -- typed arguments ---------------------------------------------------------------------------------------------------------
def test_typed_args_are_checked_defaults_filled_and_passed_when_the_skill_declares_them(line_rules):
    st = {"action": "Fasten", "object": "bracket", "args": {"arm": "left", "slow": True, "count": 3}}
    assert R.typed_params(line_rules, st, {"arm", "torque", "slow", "count"}) == {"arm": "left", "torque": 2, "slow": True,
                                                                                  "count": 3}
    assert R.typed_params(line_rules, st, {"inputs"}) == {}
    assert R.typed_params(line_rules, {"action": "Inspect", "object": "x", "args": {"any": 1}}, {"any"}) == {}


@pytest.mark.parametrize("args, says", [({"arm": "up"}, "one of left, right"), ({"torque": 11}, "within"),
                                        ({"torque": "3"}, "a number"), ({"count": 1.5}, "a integer"),
                                        ({"slow": 1}, "a boolean"), ({"station": 4}, "a string"),
                                        ({"speed": 1}, "no argument speed")])
def test_a_wrong_typed_arg_cannot_be_bound(line_rules, args, says):
    with pytest.raises(Unbindable, match=says):
        R.typed_params(line_rules, {"action": "Fasten", "object": "bracket", "args": args}, {"arm", "torque"})


def test_a_required_typed_arg_must_be_given():
    rules = R.Rules({}, {"Seat": {"dz": {"type": "number", "required": True}}})
    with pytest.raises(Unbindable, match="required"):
        R.typed_params(rules, {"action": "Seat", "object": "wall"}, {"dz"})
    assert R.typed_params(rules, {"action": "Seat", "object": "wall", "args": {"dz": -1.5}}, {"dz"}) == {"dz": -1.5}


def test_param_names_reads_the_skill_params_schema():
    view = RobotView.load("fakehome")
    assert {"inputs", "mode", "keep_arm"} <= R.param_names(view.manifest.skills, "move")
    assert R.param_names(view.manifest.skills, "nosuch") == set()


def test_bind_adds_the_typed_args_only_with_declared_rules():
    from robot_agent.core.planning.phases.exec_helpers import ExecHelpers
    view = RobotView.load("fakehome")
    host = types.SimpleNamespace(robot=view, _query_of=lambda st, o: o, cfg={"find_min_conf": 0, "find_verify": False},
                                 _find_params=set(), _run_lang="en")
    st = {"action": "MoveTo", "object": "desk@study", "args": {"mode": "left"}}
    plain = ExecHelpers._bind(host, dict(st), WorldState())
    assert "mode" not in plain[1]
    view.use_pack({"schema": 2, "name": "rules_home", "extends": "household", "assumed": True,
                   "actions": {"MoveTo": {"class": "move", "arg": {"name": "place", "type": "place"},
                                          "args": {"mode": {"type": "enum", "values": ["front", "left", "right"]}}}}})
    R.use(R.of_robot(view))
    skill, p = ExecHelpers._bind(host, dict(st), WorldState())
    assert p == dict(plain[1], mode="left") and skill == plain[0]
    with pytest.raises(Unbindable):
        ExecHelpers._bind(host, dict(st, args={"mode": "back"}), WorldState())


# -- the run: facts reset at its start, the robot's rules active until its end -----------------------------------------------
def test_the_run_start_clears_the_facts_and_activates_the_robot_rules_until_the_end(tmp_path, monkeypatch, line_rules):
    view = RobotView.load("fakeline")
    view.use_pack(LINE_PACK)
    world = WorldState(facts={"inspected": {("bracket",)}})
    monkeypatch.setattr(loop_mod, "current", lambda: types.SimpleNamespace(world=world))
    loop = types.SimpleNamespace(robot=view, dm=None, cfg={"log_dir": tmp_path, "mute_skill_tts": False},
                                 _announcer=lambda lang: None)
    r = loop_mod.RunContext(loop, "task", "en", False, "test")
    assert world.facts == {} and R.active() == line_rules
    loop_mod.ClosedLoop._close_run(loop, r)
    assert R.active() is R.EMPTY


def test_a_schema_1_run_keeps_no_rules_active(tmp_path, monkeypatch):
    view = RobotView.load("fakeline")
    monkeypatch.setattr(loop_mod, "current", lambda: types.SimpleNamespace(world=WorldState()))
    loop = types.SimpleNamespace(robot=view, dm=None, cfg={"log_dir": tmp_path}, _announcer=lambda lang: None)
    loop_mod.RunContext(loop, "task", "en", False, "test")
    assert R.active() is R.EMPTY and "_pack" not in vars(view)


def test_use_returns_what_it_replaces(line_rules):
    assert R.use(line_rules) is R.EMPTY
    assert R.use(None) == line_rules and R.active() is R.EMPTY
