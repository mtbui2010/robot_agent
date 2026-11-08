"""The option plug-in interface (core/options): specs, key resolution per source, the registry, hook dispatch of the three kinds
(plus generator hooks), services, the null dispatcher and a third-party option that runs through the real AssistLoop."""
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import (HOOKS, NEXT, STOP, Dispatcher, Key, Option, OptionSpec, Registry, REGISTRY, resolve)
from robot_agent.core.options.dispatch import NULL


def spec(name="toy", hooks=(), services=(), order=100, keys=None, enabled="enabled"):
    keys = keys if keys is not None else (Key("enabled", "bool", False, aliases=(name,)),)
    return OptionSpec(name=name, keys=keys, hooks=tuple(hooks), services=tuple(services), order=order, enabled=enabled)


def reg(*items):
    r = Registry(builtin=None, entry_points=None)
    for s, f in items:
        r.register(s, f)
    return r


class Plain(Option):
    pass


# ── declarations ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_spec_rejects_an_unknown_hook_a_duplicate_key_and_a_missing_enabled_key():
    with pytest.raises(ValueError, match="unknown hook"):
        spec(hooks=["on_nothing"])
    with pytest.raises(ValueError, match="duplicate"):
        spec(keys=(Key("a"), Key("a")), enabled="")
    with pytest.raises(ValueError, match="not a key"):
        spec(enabled="missing")
    with pytest.raises(ValueError, match="unknown type"):
        Key("a", "complex")
    with pytest.raises(ValueError, match="unknown source"):
        Key("a", sources=("run", "web"))


def test_the_registry_checks_that_the_class_defines_its_hooks_and_that_names_are_free():
    with pytest.raises(ValueError, match="does not define"):
        reg((spec(hooks=["on_final"]), Plain))
    r = reg((spec("one"), Plain))
    with pytest.raises(ValueError, match="twice"):
        r.register(spec("one"), Plain)
    with pytest.raises(ValueError, match="already used"):
        r.register(OptionSpec("two", (Key("enabled", aliases=("one",)),), enabled="enabled"), Plain)
    assert r.all_keys() == {"one", "one.enabled"}


def test_the_hook_table_has_the_three_kinds():
    assert {k for k, _f in HOOKS.values()} == {"decide", "transform", "observe"}
    assert HOOKS["on_goals"] == ("decide", True) and HOOKS["on_ground"] == ("transform", False)


# ── keys: run option > environment > manifest, per key ───────────────────────────────────────────────────────────────
KEYS = (Key("enabled", "bool", False, aliases=("toy",)), Key("mode", "str", "core", aliases=("toy_mode",), sources=("run", "env")),
        Key("limit", "int", 3, aliases=("toy_limit",)))
S = OptionSpec("toy", KEYS, enabled="enabled")


def test_defaults_and_the_legacy_and_canonical_names():
    c = resolve(S, {}, {}, None)
    assert c == {"enabled": False, "mode": "core", "limit": 3} and not c.on
    assert resolve(S, {"toy": "1"}, {}, None)["enabled"] is True
    assert resolve(S, {"toy.enabled": "on"}, {}, None).on
    assert resolve(S, {"toy_limit": "7"}, {}, None)["limit"] == 7
    assert c.mirror() == {"toy": False, "toy_mode": "core", "toy_limit": 3}


def test_precedence_run_then_env_then_manifest():
    planner = {"toy_limit": 5, "options": {"toy": {"limit": 6}}}
    assert resolve(S, {}, {}, planner)["limit"] == 6                                  # nested manifest form wins over the flat one
    assert resolve(S, {}, {}, {"toy_limit": 5})["limit"] == 5
    assert resolve(S, {}, {"ROBOT_AGENT_TOY_LIMIT": "4"}, planner)["limit"] == 4
    assert resolve(S, {"toy_limit": 2}, {"ROBOT_AGENT_TOY_LIMIT": "4"}, planner)["limit"] == 2
    assert resolve(S, {}, {}, {"toy_limit": ""})["limit"] == 3                       # an empty manifest value is no value


def test_a_key_may_leave_out_the_manifest():
    planner = {"toy_mode": "pack", "options": {"toy": {"mode": "off"}}}
    assert resolve(S, {}, {}, planner)["mode"] == "core"                              # the manifest is not a source of this key
    assert resolve(S, {}, {"ROBOT_AGENT_TOY_MODE": "pack"}, planner)["mode"] == "pack"


def test_bools_are_read_as_the_loop_always_read_its_flags():
    for v, want in (("1", True), ("true", True), ("Yes", True), ("on", True), ("0", False), ("off", False), ("", False), (True, True), (False, False)):
        assert resolve(S, {"toy": v}, {}, None)["enabled"] is want


# ── dispatch ─────────────────────────────────────────────────────────────────────────────────────────────────────────
class Probe(Option):
    log = []

    def on_start(self, run):
        self.log.append(("start", self.spec.name))

    def on_ground(self, mode, places, to):
        return mode + "+" + self.spec.name

    def on_final(self, run):
        return self.spec.name if run == "go" else None

    def on_goals(self, run):
        self.log.append(("goals", self.spec.name))
        yield f"ev-{self.spec.name}"
        return STOP if run == "stop" else None

    def forbids(self, goal):
        return f"no {goal}" if goal == "x" else None


def probe_spec(name, order):
    return spec(name, hooks=["on_start", "on_ground", "on_final", "on_goals"], services=["forbids"], order=order)


def test_hooks_run_in_order_and_the_three_kinds_combine_as_declared():
    Probe.log = []
    r = reg((probe_spec("b", 20), Probe), (probe_spec("a", 10), Probe), (probe_spec("c", 30), Probe))
    d = Dispatcher(types.SimpleNamespace(), {"a": 1, "b": 1}, {}, None, registry=r)
    assert [o.spec.name for o in d.active] == ["a", "b"]                              # c is off; order 10 < 20
    d.observe("on_start", None)
    assert Probe.log == [("start", "a"), ("start", "b")]
    assert d.transform("on_ground", "m", None, "to") == "m+a+b"
    assert d.decide("on_final", "no") is None and d.decide("on_final", "go") == "a"   # the first non-None result wins
    assert d.query("forbids", "x") == "no x" and d.query("forbids", "y", default=7) == 7
    assert d.provides("on_final") and not d.provides("on_step")


def test_generator_hooks_yield_events_and_stop_the_chain():
    Probe.log = []
    r = reg((probe_spec("a", 10), Probe), (probe_spec("b", 20), Probe))
    d = Dispatcher(None, {"a": 1, "b": 1}, {}, None, registry=r)

    def drive(run):
        out, seen = None, []
        gen = d.decide_flow("on_goals", run)
        try:
            while True:
                seen.append(next(gen))
        except StopIteration as e:
            out = e.value
        return out, seen
    assert drive("stop") == (STOP, ["ev-a"]) and [n for _k, n in Probe.log] == ["a"]  # a stopped the chain: b never ran
    assert drive("go") == (None, ["ev-a", "ev-b"])


def test_mirror_shows_every_registered_option_on_or_off_and_state_is_per_run():
    r = reg((spec("a"), Plain), (spec("b"), Plain))
    d = Dispatcher(None, {"a": 1}, {}, None, registry=r)
    assert d.mirror() == {"a": True, "b": False}
    run = types.SimpleNamespace()
    d.state(run, "a")["n"] = 1
    assert d.state(run, "a") == {"n": 1} and d.state(run, "b") == {} and run.opt["a"]["n"] == 1


def test_the_null_dispatcher_is_neutral():
    assert NULL.decide("on_final", 1) is None and NULL.transform("on_ground", "m") == "m" and NULL.query("forbids", 1, default=[]) == []
    assert list(NULL.decide_flow("on_goals", 1)) == [] and NULL.mirror() == {}


def test_entry_points_register_options_and_a_broken_one_is_skipped(monkeypatch):
    class EP:
        def __init__(self, name, fn):
            self.name, self._fn = name, fn

        def load(self):
            return self._fn

    def good(registry):
        registry.register(spec("plug"), Plain)

    def broken():
        raise RuntimeError("boom")
    import importlib.metadata as md
    monkeypatch.setattr(md, "entry_points", lambda group=None: [EP("bad", lambda r: broken()), EP("good", good)])
    r = Registry(builtin=None, entry_points="robot_agent.options")
    assert r.get("plug") is not None and "plug" in r.all_keys()


def test_the_builtin_options_are_registered_with_their_old_names():
    names = {s.name for s in REGISTRY.specs()}
    assert {"hazard", "reach_delivery", "unapplied_change"} <= names
    assert {"hazard_rules", "hazard_receiver", "reach_delivery", "unapplied_change", "hazard.rules"} <= REGISTRY.all_keys()
    assert [s.order for s in REGISTRY.specs()] == sorted(s.order for s in REGISTRY.specs())


# ── a third-party option through the real AssistLoop ─────────────────────────────────────────────────────────────────
class Toy(Option):
    """Says one more sentence when the goals hold, and stamps the done event: two hooks, no change in the loop."""
    def on_goals_met(self, run):
        return self.svc.finish(run, "success", say="toy says hello", localized=True)

    def on_finish(self, run, fields):
        fields.setdefault("toy", True)


def test_a_registered_option_acts_through_the_real_assist_loop_and_off_it_changes_nothing(make_assist, env, monkeypatch):
    from test_wiring_robot import APPLE, BASIC, HOUSE, TASK
    monkeypatch.setitem(REGISTRY._items, "toy", (spec("toy", hooks=["on_goals_met", "on_finish"], order=90), Toy))
    out = []
    for opt in ({}, {"toy": "1"}):
        robot = FakeRobot(env, HOUSE)
        out.append(done(run_task(make_assist(robot, FakeLLM(APPLE), ScriptedPlanner(BASIC), **opt), TASK)))
    off, on = out
    assert off["status"] == "success" and off["say"] != "toy says hello" and "toy" not in off
    assert on["status"] == "success" and on["say"] == "toy says hello" and on["toy"] is True


# ── the manifest and the environment reach the options of a real loop ───────────────────────────────────────────────
def test_the_environment_turns_an_option_on_by_its_old_name_and_the_cfg_mirror_keeps_the_old_keys(make_assist, env, monkeypatch):
    from test_wiring_robot import HOUSE
    monkeypatch.setenv("ROBOT_AGENT_UNAPPLIED_CHANGE", "1")
    loop = make_assist(FakeRobot(env, HOUSE), FakeLLM("{}"), ScriptedPlanner([]), hazard_rules="pack", **{"hazard.receivers": "yes"})
    assert loop.cfg["unapplied_change"] is True and loop.cfg["reach_delivery"] is False
    assert loop.cfg["hazard_rules"] == "pack" and loop.cfg["hazard_receiver"] is True
    assert {o.spec.name for o in loop.options.active} == {"hazard", "unapplied_change"}


def test_the_options_check_reports_unknown_names_in_a_manifest():
    from robot_agent.core.options.__main__ import unknown_in
    assert unknown_in({"options": {"hazard": {"receivers": True}, "reach_delivery": {"enabled": 1}}}) == []
    bad = unknown_in({"options": {"nothing": {}, "hazard": {"rulez": 1}}})
    assert len(bad) == 2 and "no such option" in bad[0] and "hazard.rulez" in bad[1]
