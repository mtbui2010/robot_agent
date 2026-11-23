"""Step S14: the recovery policy of a failed step is chosen by name (by goal kind, by action) from the robot's pack.

(1) The default data equals the old constants: the standard pack's ``by_action`` names exactly the six actions of the dict
of the old ``_step_failed`` (tests/legacy_ref/s14_recover.py, copied from assist_loop.py at 6f844de, and the frozen
tools/pack_compat_ref.json), each policy reaches the same ladder method, ``ladder_code`` / ``may_replan`` equal contract
STEP_LADDER, ``by_kind`` and ``codes`` are empty, every entry is labelled and registered. (2) Structure: the five moved methods
are served by the ``RecoverPhase`` mixin with the same syntax trees as the reference copy; ``_step_failed`` stays in
assist_loop.py with the same statements except the choice of the ladder. (3) A differential test of that choice: the old dict
and the new path, over every action and the refuted flag. (4) The selection rules and the builtins on made-up packs. Made-up
robots only."""

import ast
import inspect
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from legacy_ref import s14_recover as old
from robot_agent.contract import STEP_LADDER, FailureCode
from robot_agent.core.options import NEXT, STOP
from robot_agent.core.pack import REGISTRY, goal_semantics, recovery as R
from robot_agent.core.pack.loader import Pack, resolve
from robot_agent.core.pack.registry import Registry
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.phases import PHASE_MIXINS, recover

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = goal_semantics._default()
MOVED = ("_step_succeeded", "_reset_container", "_apply_policy", "_wait_on_robot", "_wait_health")
STD = ("std.find_search", "std.pick_ladder", "std.move_ladder", "std.open_in_search", "std.release_refind")
ACTIONS = ("Find", "Pick", "MoveTo", "Open", "Place", "PutIn", "Close", "Handover", "Say", "Fasten", "Dock", "", None)


def _fn(cls, name):
    return textwrap.dedent(inspect.getsource(getattr(cls, name)))


def _tree(src):
    """The syntax tree of one function; the level of a relative import is dropped (one module deeper now)."""
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom):
            n.level = 0
    return t


def _dict_of(fn_src, target):
    """The dict literal assigned to ``target`` in a function: key -> the method name of its ``self.<name>`` value."""
    for n in ast.walk(ast.parse(fn_src)):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == target for t in n.targets):
            d = n.value if isinstance(n.value, ast.Dict) else next(x for x in ast.walk(n.value) if isinstance(x, ast.Dict))
            return {k.value: v.attr for k, v in zip(d.keys, d.values)}
    raise AssertionError(f"no dict {target}")


OLD_DICT = _dict_of(_fn(old.RefRecover, "_step_failed"), "recover")       # action -> the old ladder method
NEW_LADDERS = _dict_of(_fn(assist_loop.AssistLoop, "_step_failed"), "ladders")   # ladder name -> the method it offers


# -- (1) the default data equals the old constants ---------------------------------------------------------------------------
def test_the_reference_dict_is_the_old_six_names():
    frozen = json.loads((ROOT / "tools" / "pack_compat_ref.json").read_text())["by_action"]
    assert list(OLD_DICT) == frozen == ["Find", "Pick", "MoveTo", "Open", "Place", "PutIn"]
    assert OLD_DICT == {"Find": "_recover_find", "Pick": "_recover_pick", "MoveTo": "_recover_move", "Open": "_recover_open",
                        "Place": "_recover_place", "PutIn": "_recover_place"}


def test_by_action_names_the_old_six_actions_and_nothing_else():
    assert list(DEFAULT.table("recovery.by_action")) == list(OLD_DICT)
    assert DEFAULT.table("recovery.by_kind") == {} and DEFAULT.table("recovery.codes") == {}
    assert sorted(DEFAULT.table("recovery.policies")) == sorted(STD)


def test_every_policy_is_labelled_registered_and_from_the_standard_pack():
    for name, entry in DEFAULT.table("recovery.policies").items():
        assert entry.get("source") and entry["callable"] == name, name
        assert DEFAULT.origin["recovery.policies"][name] == "std"
        fn = DEFAULT.named(R.NAMESPACE, name)
        assert fn is REGISTRY.get(R.NAMESPACE, name) and fn.__module__ == "robot_agent.packs.household.callables.recover"
    assert DEFAULT.problems == []


def test_the_ladder_codes_equal_contract_step_ladder():
    for a in ACTIONS:
        assert R.ladder_of(DEFAULT, a) == STEP_LADDER.get(a), a
    assert all(isinstance(R.ladder_of(DEFAULT, a)[0], FailureCode) for a in STEP_LADDER)


# -- (2) structure -----------------------------------------------------------------------------------------------------------
def test_the_mixin_is_listed_and_serves_the_moved_names():
    assert recover.RecoverPhase in PHASE_MIXINS
    mro = assist_loop.AssistLoop.__mro__
    assert mro.index(recover.RecoverPhase) < mro.index(assist_loop.WhichOne) < mro.index(assist_loop.StepLadder)
    for n in MOVED + ("_run_policy",):
        assert n not in vars(assist_loop.AssistLoop), n
        assert getattr(assist_loop.AssistLoop, n) is getattr(recover.RecoverPhase, n), n
        for base in mro[1:mro.index(recover.RecoverPhase)]:
            assert n not in vars(base), (n, base)


@pytest.mark.parametrize("name", MOVED)
def test_the_moved_bodies_are_the_same_syntax_trees(name):
    assert ast.dump(_tree(_fn(recover.RecoverPhase, name))) == ast.dump(_tree(_fn(old.RefRecover, name)))


def test_the_moved_methods_read_the_same_globals():
    g_new, g_old = vars(recover), vars(old)
    for name in MOVED:
        code = getattr(recover.RecoverPhase, name).__code__
        names = set(code.co_names) & (set(g_old) | set(g_new))
        for n in names:
            if n in g_old and n in g_new:
                assert g_new[n] is g_old[n], (name, n)
    assert recover._NEXT is assist_loop._NEXT and recover._STOP is assist_loop._STOP


def test_step_failed_keeps_every_statement_but_the_choice_of_the_ladder():
    def body(src):
        return [ast.dump(s) for s in ast.parse(src).body[0].body]
    new, was = body(_fn(assist_loop.AssistLoop, "_step_failed")), body(_fn(old.RefRecover, "_step_failed"))
    k = next(i for i, s in enumerate(was) if "'recover'" in s and "Assign" in s[:10])
    assert new[:k] == was[:k] and new[k + 2:] == was[k + 2:] and len(new) == len(was)
    assert set(NEW_LADDERS.values()) == set(OLD_DICT.values())


# -- (3) differential: the old dict and the new path over every action -------------------------------------------------------
class _Host(recover.RecoverPhase):
    """A loop with only what ``_run_policy`` uses: ``robot`` (its pack) and ``_finish``."""

    def __init__(self, pack=None):
        self.robot = NS(pack=pack) if pack is not None else None
        self.finished = []

    def _finish(self, r, status, **kw):
        self.finished.append((status, kw.get("msg")))
        return {"event": "done", "status": status}


def _spies():
    called = []

    def spy(key):
        def ladder(r, st, reason, rule):
            called.append((key, st["action"], reason, rule))
            yield {"event": "recover", "ladder": key}
            return ("out", key), "hint"
        return ladder
    return {k: spy(k) for k in NEW_LADDERS}, called


def _drive(gen):
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as e:
        return events, e.value


def _old_choice(action, refuted):
    """The old code: ``recover = None if no is not None else {...}.get(a)``; the method name or None (the planner)."""
    return None if refuted else OLD_DICT.get(action)


@pytest.mark.parametrize("refuted", [False, True])
@pytest.mark.parametrize("action", ACTIONS)
def test_the_new_choice_reaches_the_old_ladder(action, refuted):
    want = _old_choice(action, refuted)
    if refuted:                                   # unchanged in _step_failed: a refuted step never reaches a policy
        assert want is None
        return
    ladders, called = _spies()
    r = NS(goals=[{"type": "on"}], i=0)
    events, out = _drive(_Host()._run_policy(r, {"action": action, "object": "x"}, "why", "rule", ladders))
    if want is None:
        assert (events, out, called) == ([], (None, ""), [])
    else:
        key = called[0][0]
        assert NEW_LADDERS[key] == want and called == [(key, action, "why", "rule")]
        assert out == (("out", key), "hint") and events == [{"event": "recover", "ladder": key}]


# -- (4) selection rules and builtins on made-up packs -----------------------------------------------------------------------
def _line(recovery, name="s14_unit"):
    return resolve(None, None, raw={"schema": 2, "name": name, "extends": "household", "assumed": True, "recovery": recovery})


def test_by_kind_wins_over_by_action_and_takes_the_first_kind_of_the_run():
    pack = _line({"by_kind": {"on": "stop", "go": "tree"}, "by_action": {"Pick": "planner"}})
    assert R.policy_name(pack, "Pick", ["on"]) == "stop"
    assert R.policy_name(pack, "Pick", ["go", "on"]) == "tree"
    assert R.policy_name(pack, "Pick", ["in"]) == "planner"
    assert R.policy_name(pack, "MoveTo", ["in"]) == "std.move_ladder"
    assert R.policy_name(pack, "Fasten", []) == "planner"


def test_an_actions_own_recovery_field_is_used_when_by_action_has_none():
    pack = Pack(schema=2, name="line", chain=("core", "line"),
                tables={"actions": {"Seat": {"recovery": "stop"}}, "recovery.by_action": {"Pick": "tree"}})
    assert R.policy_name(pack, "Seat") == "stop" and R.policy_name(pack, "Pick") == "tree"
    assert R.policy_name(None, "Pick") == "planner"


def test_the_builtins_and_the_names_that_fall_back_to_the_planner():
    reg = Registry()
    reg.register("recovery", "line.again", lambda run, step, reason, rule, ctx: iter(()), module="line_mod")
    pack = Pack(schema=2, name="line", chain=("core", "line"), registry=reg,
                tables={"recovery.policies": {"line.again": {"callable": "line.again"}, "line.lost": {"callable": "line.lost"},
                                              "line.steps": {"ladder": [{"do": "retry"}]}}})
    for b in ("planner", "stop", "tree"):
        assert R.policy(pack, b) == (b, None, {})
    builtin, fn, entry = R.policy(pack, "line.again")
    assert builtin is None and fn is reg.get("recovery", "line.again") and entry == {"callable": "line.again"}
    for name in ("line.lost", "line.steps", "line.none"):      # not registered, a declared ladder (deferred), not declared
        assert R.policy(pack, name) == ("planner", None, {})


def test_stop_ends_the_run_and_tree_and_planner_go_on_to_the_planner():
    pack = _line({"by_kind": {"on": "stop", "go": "tree"}})
    r = NS(goals=[{"type": "on"}], i=0)
    host = _Host(pack)
    events, out = _drive(host._run_policy(r, {"action": "Pick", "object": "x"}, "why", None, _spies()[0]))
    assert out == (STOP, "") and events == [{"event": "done", "status": "failed"}]
    assert host.finished == [("failed", "Pick x failed: why (recovery policy: stop)")]
    for goals in ([{"type": "go"}], []):
        ladders, called = _spies()
        _ev, out = _drive(_Host(pack)._run_policy(NS(goals=goals, i=0), {"action": "Fasten", "object": "x"}, "w", None, ladders))
        assert out == (None, "") and called == []


def test_a_registered_policy_gets_its_entry_the_run_and_the_ladders():
    seen = []

    def again(run, step, reason, rule, ctx):
        seen.append((run, step["action"], reason, rule, ctx.loop, ctx.entry, sorted(ctx.ladders)))
        yield {"event": "recover"}
        return NEXT, ""
    reg = Registry()
    reg.register("recovery", "line.again", again, module="line_mod")
    entry = {"callable": "line.again", "times": 2}
    pack = Pack(schema=2, name="line", chain=("core", "line"), registry=reg,
                tables={"recovery.policies": {"line.again": entry}, "recovery.by_action": {"Fasten": "line.again"}})
    host, r = _Host(pack), NS(goals=[], i=0)
    events, out = _drive(host._run_policy(r, {"action": "Fasten", "object": "x"}, "why", "rule", _spies()[0]))
    assert out == (NEXT, "") and events == [{"event": "recover"}]
    assert seen == [(r, "Fasten", "why", "rule", host, entry, sorted(NEW_LADDERS))]


def test_selecting_pack():
    assert R.selecting_pack(None) == DEFAULT and R.selecting_pack(DEFAULT) is DEFAULT
    alone = Pack(schema=2, name="line", chain=("core", "line"))           # extends: null, no policy: the old ladders
    assert R.selecting_pack(alone) == DEFAULT and R.selecting_pack(alone, DEFAULT) is DEFAULT
    own = Pack(schema=2, name="line", chain=("core", "line"), tables={"recovery.by_kind": {"mount": "tree"}})
    assert R.selecting_pack(own) is own
    based = _line({})
    assert R.selecting_pack(based) is based and R.policy_name(based, "Pick") == "std.pick_ladder"


def test_ctx_copies_its_inputs():
    ladders, entry = {"pick": object()}, {"times": 1}
    c = R.Ctx("loop", "run", entry, ladders)
    assert (c.loop, c.run, c.entry, c.ladders) == ("loop", "run", entry, ladders) and c.ladders is not ladders
    assert R.Ctx("loop", "run", "not a mapping").entry == {} and R.Ctx("l", "r").ladders == {}


def test_an_unknown_ladder_code_falls_back_to_step_ladder():
    pack = Pack(schema=2, name="line", chain=("core", "line"),
                tables={"recovery.by_action": {"Pick": "line.p"}, "recovery.policies": {"line.p": {"ladder_code": "nope"}}})
    assert R.ladder_of(pack, "Pick") is None
    pack.tables["recovery.policies"]["line.p"] = {"ladder_code": "blocked", "may_replan": True}
    assert R.ladder_of(pack, "Pick") == (FailureCode.BLOCKED, True)
