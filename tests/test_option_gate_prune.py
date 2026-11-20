"""Option ``gate_prune`` (default OFF): an Open / Close of a place the ENV declares not openable is dropped; no data = kept.
Made-up world: a crate (openable), a ledge and a plinth (declared not openable), a bin with no ``openable`` field."""
from types import SimpleNamespace as NS

from fakes import steps
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.gate_prune import SPEC, GatePrune

ENV = {"crate@hall": {"openable": True}, "ledge@hall": {"openable": False}, "plinth@hall": {"openable": False}, "bin@hall": {}}


def make(roles=("Open", "Close"), goals=(), env=ENV):
    cfg = resolve(SPEC, {"gate_prune": "1"}, {}, None)
    role = {"open": roles[0], "close": roles[1]}.get if roles else (lambda r: None)
    svc = NS(role=role, lab=lambda *a, **k: svc.labs.append(k), labs=[])
    return GatePrune(cfg, svc), NS(places=Places(env), goals=list(goals)), svc


def acts(got):
    return [(s["action"], s["object"]) for s in got]


def test_registered_and_off_by_default():
    assert "gate_prune" in ASSIST_OPTIONS
    assert resolve(SPEC, {}, {}, None)["enabled"] is False


def test_open_and_close_of_a_place_declared_not_openable_are_dropped():
    opt, run, svc = make()
    got = opt.on_plan(steps("MoveTo ledge@hall", "Open ledge@hall", "Find cup", "Close ledge@hall", "MoveTo crate@hall", "Open crate@hall"), run)
    assert acts(got) == [("MoveTo", "ledge@hall"), ("Find", "cup"), ("MoveTo", "crate@hall"), ("Open", "crate@hall")]
    assert [k["place"] for k in svc.labs] == ["ledge@hall", "ledge@hall"]


def test_no_data_keeps_the_step_unknown_place_no_field_goal_named_unknown_action():
    opt, run, _ = make(goals=[{"type": "open", "object": "plinth@hall"}])
    plan = steps("Open bin@hall", "Open nowhere@hall", "Open plinth@hall", "Wiggle ledge@hall")
    assert opt.on_plan(plan, run) is None


def test_gate_prune_empty_pack_without_roles_or_env_does_nothing():
    assert make(roles=None)[0].on_plan(steps("Open ledge@hall"), make(roles=None)[1]) is None
    opt, run, _ = make(env={})
    assert opt.on_plan(steps("Open ledge@hall"), run) is None


def test_gate_prune_off_is_the_default_path_and_on_drops_the_open_through_the_loop(make_assist, env):
    from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task
    env["ledge@kitchen"] = {"openable": False, "aliases": []}
    plan = steps("MoveTo ledge@kitchen", "Open ledge@kitchen", "Find mug", "Pick mug", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    seen = {}
    for name, opts in (("off", {}), ("on", {"gate_prune": "1"})):
        robot = FakeRobot(env, {"mug": "drawer@kitchen"})
        loop = make_assist(robot, FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": [
            {"type": "on", "object": "mug", "from": "drawer", "to": "dining table"}]}), ScriptedPlanner(plan), detector_miss_rate=0, **opts)
        loop.robot.manifest.planner["action_roles"] = {"open": "Open", "close": "Close"}
        run_task(loop, "Put the mug on the dining table.", max_events=3000)
        seen[name] = [e["flag"] for e in loop.lab_log if e["flag"] == "gate_prune"]
    assert seen["off"] == [] and seen["on"] and set(seen["on"]) == {"gate_prune"}
