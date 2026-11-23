"""Step S06: the six tree conditions and the six effect predicates are ONE table of the standard pack (predicates.yaml, the ``std.*``
evaluators of packs/household/callables/predicates.py, read by core/pack/predicates.py). These tests prove the move changed
nothing: the default data equals the old constants (tests/legacy_ref/predicates.py, the old code), the new and the old
functions give the same answers over a grid of made-up worlds and arguments, each consumer keeps its own rule for a wrong
arity and for "cannot tell", and a pack that declares other names (or none) changes what the tree and the effect check read."""

import itertools
from types import SimpleNamespace as W

import pytest

from legacy_ref import predicates as old
from robot_agent.core.pack import Pack, Registry, predicates as P
from robot_agent.core.planning import verify
from robot_agent.core.planning.base import WorldState
from robot_agent.options import bt_goal

TREE_SIX = ("holding", "hand_free", "at", "placed", "opened", "closed")
EFFECT_SIX = ("hand_empty", "arrived", "found", "holding", "opened", "on")
NAMES = TREE_SIX + EFFECT_SIX + ("unknown", "", "Holding", None, ["holding"])
VALUES = (None, "a", "b", "", ["a"])
ARGS = [t for n in range(4) for t in itertools.product(VALUES, repeat=n) if n < 3 or t[0] == "a"]
WORLDS = [W(), W(holding=None), W(holding="a"), W(holding="b", arrived="a"), W(arrived="b", opened=["a"]), W(holding="a", opened={"a", "b"}),
          W(opened=(), found="a"), W(opened=None, arrived=None, holding="a", found="b"), W(opened=["b"], holding="b"),
          WorldState(), WorldState(holding="a", arrived="b", opened={"a"}), WorldState(arrived="a", opened={"b"}, found="a")]
PLACED = (None, {}, {"a": "b"}, {"a": "a", "b": "a"})


def test_the_default_data_is_the_old_constants():
    assert P.names("tree") == old.CONDITIONS == bt_goal.CONDITIONS
    assert sorted(P.names("effect")) == sorted(EFFECT_SIX)
    assert not P.default().problems
    for name, decl in P.table().items():
        assert P.default().named("predicate", decl["eval"]) is not None, name
        assert decl.get("source") or decl.get("assumed"), f"{name}: a fact of a robot needs a label"


def test_the_effect_scope_has_the_old_names_and_wildcards():
    table = P.table()
    assert [n for n in table if "effect" in table[n]["scopes"]] == ["holding", "hand_empty", "arrived", "opened", "found", "on"]
    assert all(a.endswith("?") for n in ("holding", "arrived", "opened", "found", "on") for a in table[n]["args"])


@pytest.mark.parametrize("world", WORLDS, ids=lambda w: repr(vars(w) if hasattr(w, "__dict__") else w)[:60])
def test_a_tree_condition_gives_the_answer_of_the_old_code(world):
    for name, args, placed in itertools.product(NAMES, ARGS, PLACED):
        got = bt_goal.holds(name, args, world, placed)
        assert got is old.holds(name, args, world, placed), (name, args, vars(world), placed)
        assert got is True or got is False


def _bind(arg):
    return arg if not arg.startswith("?") else {"obj": "a", "x": None, "y": "b"}.get(arg[1:], "zzz")


PREDS = [f"{neg}{n}{a}" for neg in ("", "not ", "!") for n in EFFECT_SIX + ("hand_free", "at", "placed", "closed", "fastened")
         for a in ("", "()", "(*)", "(a)", "(?obj)", "(?x)", "(?y)", "( b )", "(a, b)")] + ["", "not", "(a)", "holding a", "9x(a)", "on(a))"]


@pytest.mark.parametrize("world", WORLDS, ids=lambda w: repr(vars(w) if hasattr(w, "__dict__") else w)[:60])
def test_an_effect_predicate_gives_the_answer_of_the_old_code(world):
    for pred in PREDS:
        try:
            want = old._holds(world, pred, _bind)
        except AttributeError:
            continue                                   # a world with no ``holding`` at all: the old hand_empty raised, not a case
        assert verify._holds(world, pred, _bind) is want, (pred, vars(world))


def test_the_effect_regex_is_the_old_one():
    assert verify._PRED.pattern == old._PRED.pattern


def test_effects_check_keeps_its_verdicts():
    step = {"action": "Pick", "object": "cup"}
    assert verify.effects_check(step, WorldState(), ["holding(?inputs)", "not hand_empty()"]) == (True, "")
    ok, why = verify.effects_check({"action": "MoveTo", "object": "a"}, WorldState(), ["arrived(?inputs)", "opened(?inputs)"])
    assert not ok and "opened(?inputs)" in why and "arrived" not in why
    assert verify.effects_check(step, WorldState(), ["fastened(cup)", "closed(cup)", "hand_free()"]) == (True, "")


# ── each consumer keeps its own rule ───────────────────────────────────────────────────────────────────────────────────
def test_a_tree_reads_a_wrong_arity_and_a_name_outside_its_scope_as_false():
    world = WorldState(holding="a", arrived="b", found="a", opened={"a"})
    assert [bt_goal.holds(n, (), world, {}) for n in ("holding", "at", "opened", "hand_free")] == [False, False, False, False]
    assert not bt_goal.holds("hand_free", ("a",), world, {})
    assert [bt_goal.holds(n, ("a",), world, {}) for n in ("hand_empty", "arrived", "found", "on")] == [False] * 4
    assert bt_goal.holds("holding", (None,), WorldState(), {}) is True, "a tree leaf with an empty slot compares with None, as before"


def test_an_effect_reads_a_name_outside_its_scope_as_not_tracked_and_any_arity_as_it_is():
    world = WorldState(holding="a", arrived="b", opened={"a"})
    assert [verify._holds(world, f"{n}(a)", _bind) for n in ("hand_free", "at", "placed", "closed")] == [None] * 4
    assert verify._holds(world, "hand_empty(a, b)", _bind) is False and verify._holds(world, "holding(*)", _bind) is True
    assert verify._holds(world, "not holding(?x)", _bind) is False, "a parameter bound to None is the wildcard"


# ── a pack that declares other names ───────────────────────────────────────────────────────────────────────────────────
def _pack(table, registry):
    return Pack(schema=2, name="t", tables={"predicates": table}, registry=registry)


def test_a_pack_can_declare_a_condition_and_an_effect_the_standard_pack_lacks():
    reg = Registry()
    reg.register("predicate", "t.seated", lambda belief, args: args[0] in (getattr(belief, "facts", None) or ()), module="x")
    reg.register("predicate", "t.unsure", lambda belief, args: None, module="x")
    pack = _pack({"seated": {"eval": "t.seated", "args": ["object"], "scopes": ["tree", "effect"]},
                  "unsure": {"eval": "t.unsure", "args": [], "scopes": ["tree", "effect"]},
                  "ghost": {"eval": "t.ghost", "args": [], "scopes": ["tree", "effect"]}}, reg)
    world = W(facts={"pipe"})
    assert P.names("tree", pack) == ("seated", "unsure", "ghost")
    assert P.holds_in_tree("seated", ("pipe",), world, {}, pack) is True and P.holds_in_tree("seated", ("clip",), world, {}, pack) is False
    assert P.holds_in_tree("seated", (), world, {}, pack) is False, "a tree needs every argument"
    assert P.holds_in_tree("unsure", (), world, {}, pack) is False, "a tree reads cannot-tell as false"
    assert P.holds_in_tree("ghost", (), world, {}, pack) is False, "an evaluator that is not registered"
    assert P.holds_in_effect("seated", "pipe", world, pack) is True and P.holds_in_effect("seated", "clip", world, pack) is False
    assert P.holds_in_effect("unsure", None, world, pack) is None and P.holds_in_effect("ghost", None, world, pack) is None


def test_a_pack_with_no_predicates_has_no_conditions_and_no_tracked_effects():
    pack = _pack({}, Registry())
    assert P.names("tree", pack) == () and P.names("effect", pack) == ()
    assert P.holds_in_tree("hand_free", (), WorldState(), {}, pack) is False
    assert P.holds_in_effect("hand_empty", None, WorldState(), pack) is None


def test_an_evaluator_error_is_a_false_in_a_tree_and_an_error_in_an_effect():
    reg = Registry()
    reg.register("predicate", "t.bad", lambda belief, args: 1 / 0, module="x")
    reg.register("predicate", "t.kind", lambda belief, args: {}[args[0]], module="x")
    pack = _pack({"bad": {"eval": "t.bad", "args": [], "scopes": ["tree", "effect"]},
                  "kind": {"eval": "t.kind", "args": ["object"], "scopes": ["tree"]}}, reg)
    assert P.holds_in_tree("kind", ("x",), W(), {}, pack) is False                 # a KeyError / TypeError is a false, as before
    with pytest.raises(ZeroDivisionError):
        P.holds_in_tree("bad", (), W(), {}, pack)
    with pytest.raises(ZeroDivisionError):
        P.holds_in_effect("bad", None, W(), pack)


def test_the_view_gives_the_worlds_fields_and_the_placed_map():
    view = P.View(W(holding="a"), {"a": "b"})
    assert (view.holding, view.placed, getattr(view, "arrived", None)) == ("a", {"a": "b"}, None)


def test_the_household_evaluators_belong_to_the_standard_pack():
    assert all(n.startswith("std.") for n in P.default().registry.names("predicate"))
