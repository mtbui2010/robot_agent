"""Step S14 (data and seam): the recovery of a failed step as the policy the robot pack names (by goal kind, by action).

(1) The default data equals the old constants: the standard pack's ``by_action`` names exactly the six actions of the dict
of AssistLoop._step_failed (read from the live source and from the copy of 3deb5c5), each policy runs exactly the loop
method that dict names, the two STEP_LADDER actions are the two ladder policies, ``by_kind`` is empty, every entry is
labelled and registered.
(2) A differential test: the old selection (copied below from 3deb5c5) and the new ``_recover_by_policy`` give equal
outcomes, equal events and equal calls on the same scripted host, for every action and with or without a refuting check.
(3) The selection order and the builtins (planner, tree, stop); which pack recovers a run. (4) A custom action with a
registered policy of its own pack, and a goal kind that sends every failed step to its tree. Made-up robots only.
The loop does not call the seam yet (tests/test_find_ladder.py pins the dict of _step_failed); (5) checks that."""

import ast
import inspect

import pytest

from robot_agent.contract import STEP_LADDER
from robot_agent.core.pack import goal_semantics, recovery as R
from robot_agent.core.pack.loader import Pack
from robot_agent.core.pack.registry import Registry
from robot_agent.core.planning import assist_loop
from robot_agent.core.planning.loop import _NEXT, _STOP
from robot_agent.core.planning import step_ladder

DEFAULT = goal_semantics._default()
# assist_loop.py:3150-3152 at 3deb5c5: the action -> the loop method that recovered it
OLD = {"Find": "_recover_find", "Pick": "_recover_pick", "MoveTo": "_recover_move", "Open": "_recover_open",
       "Place": "_recover_place", "PutIn": "_recover_place"}
LADDERS = ("std.pick_ladder", "std.move_ladder")
POLICY_METHOD = {"std.find_search": "_recover_find", "std.pick_ladder": "_recover_pick", "std.move_ladder": "_recover_move",
                 "std.open_in_search": "_recover_open", "std.release_refind": "_recover_place"}


# -- (1) the default data equals the old constants ------------------------------------------------------------------------------
def _live_dict():
    """The action -> method dict literal of the live AssistLoop._step_failed."""
    tree = ast.parse(inspect.getsource(assist_loop.AssistLoop._step_failed).lstrip())
    found = [n for n in ast.walk(tree) if isinstance(n, ast.Dict) and n.keys
             and all(isinstance(k, ast.Constant) for k in n.keys)
             and all(isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self" for v in n.values)]
    assert len(found) == 1
    return {k.value: v.attr for k, v in zip(found[0].keys, found[0].values)}


def test_the_copied_old_dict_is_the_live_one():
    assert _live_dict() == OLD


def test_by_action_names_exactly_the_six_actions_of_the_old_dict():
    assert set(DEFAULT.table("recovery.by_action")) == set(OLD)
    assert {a: POLICY_METHOD[p] for a, p in DEFAULT.table("recovery.by_action").items()} == OLD
    assert DEFAULT.table("recovery.by_kind") == {}


def test_the_ladder_policies_are_the_step_ladder_actions():
    by_action = DEFAULT.table("recovery.by_action")
    assert {a for a, p in by_action.items() if p in LADDERS} == set(STEP_LADDER)


def test_every_policy_is_labelled_and_registered():
    table = DEFAULT.table("recovery.policies")
    assert set(table) == set(DEFAULT.table("recovery.by_action").values()) == set(POLICY_METHOD)
    for name, entry in table.items():
        assert entry.get("source"), name
        assert R.policy(DEFAULT, name)[0] is not None, name
    assert DEFAULT.problems == []


class Host(step_ladder.StepLadder):
    """The loop as the recovery sees it: the ladders and the planner's hint are scripted, every call is logged."""

    def __init__(self, pack=None, out=None):
        self.robot = type("RobotView", (), {"pack": pack})()
        self.log, self.out = [], out

    def _ladder_call(self, name, r, st, reason, rule):
        self.log.append((name, st["action"], reason, rule))
        yield ("event", name)
        return self.out or (None, f"; hint of {name}")

    def _refuted(self, r, st, no):
        self.log.append(("_refuted", st["action"], no))
        return "" if no is None else "; refuted"

    def _finish(self, r, status, **kw):
        self.log.append(("_finish", status, kw.get("msg")))
        return ("done", status)


for _m in set(OLD.values()):
    setattr(Host, _m, (lambda m: lambda self, r, st, reason, rule: self._ladder_call(m, r, st, reason, rule))(_m))


class Run:
    def __init__(self, goals=()):
        self.goals = list(goals)


def drive(gen):
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as e:
        return events, e.value


def test_each_policy_runs_the_method_the_old_dict_named():
    for action, method in OLD.items():
        h = Host(DEFAULT)
        events, out = drive(h._recover_by_policy(Run(), {"action": action, "object": "cup"}, "why", "rule", None))
        assert events == [("event", method)] and out == (None, f"; hint of {method}"), action
        assert h.log == [(method, action, "why", "rule")], action


def test_a_policy_looks_the_method_up_when_it_runs():
    h = Host(DEFAULT)
    h._recover_pick = lambda r, st, reason, rule: iter(())          # patched on the instance after the pack was loaded
    assert drive(h._recover_by_policy(Run(), {"action": "Pick", "object": "cup"}, "x", None, None)) == ([], None)


# -- (2) differential test against the old selection ----------------------------------------------------------------------------
def old_select(self, r, st, reason, rule, no):
    """assist_loop.py:3149-3153 at 3deb5c5 (AssistLoop._step_failed), the two lines that chose and ran the recovery."""
    a = st["action"]
    recover = None if no is not None else {"Find": self._recover_find, "Pick": self._recover_pick,
                                           "MoveTo": self._recover_move, "Open": self._recover_open, "Place": self._recover_place,
                                           "PutIn": self._recover_place}.get(a)
    out, extra = (yield from recover(r, st, reason, rule)) if recover else (None, self._refuted(r, st, no))
    return out, extra


@pytest.mark.parametrize("action", sorted(OLD) + ["Close", "Say", "Handover", "Fasten", "Dock"])
@pytest.mark.parametrize("no", [None, "refuted by the camera"])
@pytest.mark.parametrize("out", [None, (_NEXT, ""), (_STOP, "")])
@pytest.mark.parametrize("goals", [[], [{"type": "on", "object": "cup", "to": "table"}], [{"type": "go", "to": "door"}, "x"]])
def test_recover_by_policy_equals_the_old_selection(action, no, out, goals):
    st = {"action": action, "object": "cup"}
    old_h, new_h = Host(DEFAULT, out), Host(DEFAULT, out)
    old_ev, old_out = drive(old_select(old_h, Run(goals), st, "reason", "rule", no))
    new_ev, new_out = drive(new_h._recover_by_policy(Run(goals), st, "reason", "rule", no))
    assert (new_ev, new_out, new_h.log) == (old_ev, old_out, old_h.log)


# -- (3) selection order, builtins, which pack ------------------------------------------------------------------------------------
def _pack(chain, tables=None, registry=None):
    return Pack(schema=2, name=chain[-1], chain=tuple(chain), tables=tables or {}, registry=registry)


def test_selection_order_step_then_kind_then_action_then_field_then_planner():
    p = _pack(("core", "line"), {"recovery.by_kind": {"mount": "tree"}, "recovery.by_action": {"Fasten": "stop"},
                                 "actions": {"Fasten": {"recovery": "line.x"}, "Seat": {"recovery": "line.seat"}}})
    assert R.policy_name(p, {"action": "Fasten", "recovery": "line.own"}, ["mount"]) == "line.own"
    assert R.policy_name(p, {"action": "Fasten"}, ["on", "mount"]) == "tree"
    assert R.policy_name(p, {"action": "Fasten"}, ["on"]) == "stop"
    assert R.policy_name(p, {"action": "Seat"}, []) == "line.seat"
    assert R.policy_name(p, {"action": "Weld"}, []) == R.PLANNER
    assert R.policy_name(p, {"object": "x"}, []) == R.PLANNER
    assert R.policy_name(None, {"action": "Pick"}) == R.PLANNER


def test_builtins_and_unknown_names_have_no_callable():
    for name in R.BUILTINS + ("std.nobody", "", None):
        assert R.policy(DEFAULT, name) == (None, None)
    assert R.policy(None, "std.pick_ladder") == (None, None)


def test_recovering_pack():
    assert R.recovering_pack(None) == DEFAULT and R.recovering_pack(DEFAULT) is DEFAULT
    alone = _pack(("core", "line"))                                   # extends: null, no recovery table: the old ladders
    assert R.recovering_pack(alone, DEFAULT) is DEFAULT
    own = _pack(("core", "line"), {"recovery.by_kind": {"mount": "tree"}})
    assert R.recovering_pack(own, DEFAULT) is own
    on_std = _pack(DEFAULT.chain[:-1] + ("line",))                    # extends the standard pack and removes its ladders
    assert R.recovering_pack(on_std, DEFAULT) is on_std and R.policy_name(on_std, {"action": "Pick"}) == R.PLANNER


def test_stop_ends_the_run_at_once():
    p = _pack(("core", "line"), {"recovery.by_action": {"Fasten": "stop"}})
    h = Host(p)
    events, out = drive(h._recover_by_policy(Run(), {"action": "Fasten", "object": "bracket"}, "clamp open", None, None))
    assert out == (_STOP, "") and events == [("done", "failed")]
    assert h.log == [("_finish", "failed", "Fasten bracket failed: clamp open")]


def test_tree_and_planner_give_the_planners_hint():
    for name in ("tree", "planner", "line.not_declared"):
        p = _pack(("core", "line"), {"recovery.by_kind": {"mount": name}})
        h = Host(p)
        events, out = drive(h._recover_by_policy(Run([{"type": "mount"}]), {"action": "Pick", "object": "bracket"}, "r", None, None))
        assert (events, out, h.log) == ([], (None, ""), [("_refuted", "Pick", None)]), name


# -- (4) a custom action with a registered policy; a kind whose steps go to its tree ------------------------------------------------
def test_a_custom_action_runs_its_registered_policy():
    reg = Registry()
    seen = []

    def fasten_retry(run, step, reason, rule, ctx):
        seen.append((step["action"], reason, rule, ctx.loop is h, ctx.run is run, ctx.entry.get("tries")))
        yield ("event", "fasten again")
        return _NEXT, ""
    reg.register("recovery", "line.fasten_retry", fasten_retry, module="tests.made_up_line")
    p = _pack(("core", "line"), {"recovery.policies": {"line.fasten_retry": {"callable": "line.fasten_retry", "tries": 2}},
                                 "recovery.by_action": {"Fasten": "line.fasten_retry"}}, registry=reg)
    h, run = Host(p), Run()
    events, out = drive(h._recover_by_policy(run, {"action": "Fasten", "object": "bracket"}, "clamp open", "rule", None))
    assert out == (_NEXT, "") and events == [("event", "fasten again")]
    assert seen == [("Fasten", "clamp open", "rule", True, True, 2)] and h.log == []


def test_a_refuting_check_still_goes_to_the_planner_whatever_the_policy():
    p = _pack(DEFAULT.chain[:-1] + ("line",), {**DEFAULT.tables, "recovery.by_action": {"Fasten": "stop"}},
              registry=DEFAULT.registry)
    h = Host(p)
    events, out = drive(h._recover_by_policy(Run(), {"action": "Fasten", "object": "b"}, "r", None, "no"))
    assert (events, out, h.log) == ([], (None, "; refuted"), [("_refuted", "Fasten", "no")])


def test_by_kind_wins_over_the_standard_ladder():
    tables = {**DEFAULT.tables, "recovery.by_kind": {"mount": "tree"}}
    p = _pack(DEFAULT.chain[:-1] + ("line",), tables, registry=DEFAULT.registry)
    h = Host(p)
    st = {"action": "Pick", "object": "bracket"}
    assert drive(h._recover_by_policy(Run([{"type": "mount"}]), st, "r", None, None)) == ([], (None, ""))
    assert h.log == [("_refuted", "Pick", None)]
    h2 = Host(p)
    drive(h2._recover_by_policy(Run([{"type": "on"}]), st, "r", None, None))
    assert h2.log[0][0] == "_recover_pick"


# -- (5) not wired yet: the loop still runs its own dict ------------------------------------------------------------------------
def test_the_loop_does_not_call_the_seam_yet():
    src = inspect.getsource(assist_loop.AssistLoop)
    assert "_recover_by_policy" not in src and "pack_recovery" not in src
