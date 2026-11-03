"""v2 planner-plugin: the registry loads a planner by name, 'module:Class' or entry point; optional
capabilities are detected by the interface (also through the recording proxy); the core holds no SAGE
names (docs/planners.md)."""

import re
import sys
import types
from pathlib import Path

import pytest

from fakes import FakeLLM, FakeRobot, done, run_task, steps
from robot_agent.core.planning import registry
from robot_agent.core.planning.base import Planner, planner_capabilities, subgoal_repair
from robot_agent.core.planning.registry import (accepted_kwargs, get_planner, is_planner_name,
                                                planner_available, resolve_planner)
from robot_agent.observe.record import RecordingPlanner

ROOT = Path(__file__).resolve().parents[1]
DT, DS = "dining table@kitchen", "desk@study room"
GOOD = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
APPLE = {"status": "clear", "reason": "", "question": "",
         "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}


class MinimalPlanner:
    """Only the required interface; constructor takes two of the core's offered keys."""

    def __init__(self, capabilities=None, actions=None):
        self.capabilities, self.actions = capabilities, actions

    def generate_plan(self, task, obs, visible_objects):
        return [dict(s) for s in GOOD], {"method": "minimal"}

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        return [], {}


class KwPlanner(MinimalPlanner):
    def __init__(self, **cfg):
        self.cfg = cfg


class NotAPlanner:
    pass


@pytest.fixture
def plugin_module(monkeypatch):
    mod = types.ModuleType("my_planners")
    mod.MinimalPlanner, mod.KwPlanner, mod.NotAPlanner = MinimalPlanner, KwPlanner, NotAPlanner
    monkeypatch.setitem(sys.modules, "my_planners", mod)
    return mod


# ── registry ──────────────────────────────────────────────────────────────
def test_module_class_loads_and_is_recorded(plugin_module):
    p = get_planner("my_planners:MinimalPlanner", capabilities={"arms": 0}, host="h", model=None)
    assert isinstance(p, RecordingPlanner) and isinstance(p, Planner)
    assert p.capabilities == {"arms": 0}                 # host is not accepted; None values dropped
    assert p.generate_plan("t", "", [])[1]["method"] == "minimal"


def test_plugin_with_kwargs_gets_every_key(plugin_module):
    p = get_planner("my_planners:KwPlanner", host="h", model=None, robot_pkg="x")
    assert p.cfg == {"host": "h", "robot_pkg": "x"}


def test_not_a_planner_is_a_clear_type_error(plugin_module):
    with pytest.raises(TypeError, match="generate_plan"):
        get_planner("my_planners:NotAPlanner")


@pytest.mark.parametrize("name, msg", [
    ("nope", "available planners: grace, llm_direct"),
    ("no_such_mod_xyz:Cls", "cannot import"),
    ("my_planners:Missing", "has no 'Missing'"),
    ("my_planners:", "expected 'package.module:Class'"),
])
def test_unknown_names_keep_the_clear_error(plugin_module, name, msg):
    with pytest.raises(ValueError, match=re.escape(msg)):
        get_planner(name)


def test_entry_point_group(monkeypatch, plugin_module):
    ep = types.SimpleNamespace(name="Mine", value="my_planners:MinimalPlanner", load=lambda: MinimalPlanner)
    monkeypatch.setattr(registry, "_entry_points", lambda: {"mine": ep})
    assert is_planner_name("mine") and is_planner_name("MINE")
    assert resolve_planner("mine") == (MinimalPlanner, False)
    assert get_planner("Mine").generate_plan("t", "", [])[1]["method"] == "minimal"
    with pytest.raises(ValueError, match="grace, llm_direct, mine"):
        get_planner("other")


def test_builtin_names_unchanged(monkeypatch):
    assert resolve_planner("GRACE")[1] is True and resolve_planner("llm_direct")[1] is True
    assert is_planner_name("grace") and is_planner_name("a.b:C") and not is_planner_name("sage")
    assert not is_planner_name("") and not is_planner_name(None)
    assert planner_available("grace")                    # embodiplan is on the test path
    assert not planner_available("nope")


def test_accepted_kwargs():
    def f(a, *, b=1):
        return a
    assert accepted_kwargs(f, {"a": 1, "b": None, "c": 3}) == {"a": 1}


# ── optional capabilities ─────────────────────────────────────────────────
class _Block:
    def __init__(self, name, steps_):
        self.name, self.steps = name, steps_


class PublicSage:
    """embodiplan's public SAGE editing surface (what GraceBackend adapts)."""

    def __init__(self):
        self.last_blocks = [_Block("a", GOOD[:3]), _Block("b", GOOD[3:])]
        self.examples_calls, self.edits, self.progress = 0, [], None

    def retrieve_examples(self, task):
        self.examples_calls += 1
        return [], "EX"

    def edit_block(self, block, feedback, *, task, state=None, obs="", visible_objects=None, examples_block=None):
        self.edits.append((block.name, feedback, examples_block))
        return [{"action": "Wait", "object": ""}]

    def generate_plan(self, task, obs, visible_objects):
        return list(GOOD), {}

    def replan(self, *a):
        return [], {}


def test_grace_backend_offers_subgoal_repair_through_the_proxy():
    from robot_agent.core.planning.backends.pyplanner_grace import GraceBackend
    inner = PublicSage()
    p = RecordingPlanner(GraceBackend.wrap(inner))
    assert planner_capabilities(p) >= {"subgoal_repair", "progress"}
    rep = subgoal_repair(p)
    blocks = rep.blocks()
    assert blocks is inner.last_blocks
    assert rep.join_blocks(blocks) == GOOD
    assert rep.repair_block(blocks[0], "fb", task="t") == [{"action": "Wait", "object": ""}]
    rep.repair_block(blocks[1], "fb2", task="t")
    assert inner.examples_calls == 1 and inner.edits[-1] == ("b", "fb2", "EX")
    fn = lambda d: None                                  # noqa: E731
    p.progress = fn                                      # the core's progress hook reaches SAGE
    assert inner.progress is fn


def test_minimal_planner_has_no_optional_capabilities():
    assert planner_capabilities(MinimalPlanner()) == set()
    assert subgoal_repair(MinimalPlanner()) is None
    assert subgoal_repair(RecordingPlanner(MinimalPlanner())) is None


def test_gate_without_repair_falls_back_to_one_fresh_plan(make_assist, env):
    """A planner without SubgoalRepair: _repair_plan is (None, []), the gate asks for one refined plan."""
    from fakes import ScriptedPlanner

    class TwoPlans(ScriptedPlanner):
        def generate_plan(self, task, obs, visible_objects):
            self.calls.append(("generate_plan", task, obs))
            n = sum(1 for c in self.calls if c[0] == "generate_plan")
            # the first plan puts the apple down at the wrong place: no symbolic repair can know where it
            # belongs instead (SAGE-v2 plan_repair fixes a missing Find without any LLM call)
            bad = [dict(s) for s in GOOD]
            bad[-2], bad[-1] = {"action": "MoveTo", "object": DS}, {"action": "Place", "object": DS}
            return (bad if n == 1 else list(GOOD)), {}
    robot = FakeRobot(env, {"apple": DS})
    planner = TwoPlans(GOOD)
    loop = make_assist(robot, FakeLLM(default=APPLE), planner)
    assert loop._repair_plan(planner, "t", "", list, None, None, [], "") == (None, [])
    evs = run_task(loop, "Bring the apple from the desk to the dining table.")
    assert done(evs)["status"] == "success"
    gens = [c for c in planner.calls if c[0] == "generate_plan"]
    assert len(gens) == 2 and "A checker rejected the previous plan" in gens[1][2]


def test_plugin_selected_by_name_in_closed_loop_cfg(plugin_module):
    """ClosedLoop._make_planner builds a plug-in from cfg['planner'] with the core's planner config."""
    from robot_agent.core.planning.loop import ClosedLoop
    lp = object.__new__(ClosedLoop)
    lp.cfg = {"planner": "my_planners:MinimalPlanner", "host": None, "model": None, "live_path": "x"}
    lp._llm_cfg, lp.robot_pkg = {}, "pkg"
    lp.robot = types.SimpleNamespace(capabilities=lambda: {"arms": 1}, plan_actions={"MoveTo", "Pick"})
    p = lp._make_planner()
    assert p.capabilities == {"arms": 1} and p.actions == ["MoveTo", "Pick"]


# ── the core holds no SAGE names ─────────────────────────────────────────
def test_core_has_no_sage_private_names():
    core = ROOT / "robot_agent"
    bad = []
    for f in list((core / "core").rglob("*.py")) + list((core / "interact").rglob("*.py")):
        if "core/planning/backends" in f.as_posix():
            continue
        t = f.read_text()
        if re.search(r"edit_block|last_blocks|retrieve_examples|getattr\([a-z_]+, *.\_p.", t):
            bad.append(f.name)
    assert not bad
    for f in core.rglob("*.py"):
        p = f.as_posix()
        if "core/planning/backends/" in p or p.endswith("core/planning/action_model.py"):
            continue
        assert not re.search(r"^\s*(import embodiplan|from embodiplan)", f.read_text(), re.M), p
