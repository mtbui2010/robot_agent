"""Option ``repair_sync`` (default OFF): the gate's symbolic repair sees the steps the ``on_plan`` options added (blocks lined up with the plan the
gate scores), the suffix operator ``finish`` passes over a goal done before the replan, and a suffix whose last release of a goal's object is at
another place gets the finding ``release_off_goal``. Hook-level tests on made-up blocks, objects (gizmo, widget, sprocket) and places, and loop-level
tests of the FINAL state on the test house. Nothing here is a real robot's skill or a real task."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from test_sage_v2_step3_gate import Block, BlockPlanner, understood
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import plan_repair
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.robot import RobotView
from robot_agent.options.repair_sync import FINDING, SPEC, RepairSync, align

DR, DT, CT, DS = "drawer@kitchen", "dining table@kitchen", "counter@kitchen", "desk@study room"


def acts(plan):
    return [f'{s["action"]} {s["object"]}' for s in plan]


def owned(*blocks):
    return [(k, st) for k, b in enumerate(blocks) for st in steps(*b)]


def make(cfg=None, hands=1, goals=(), hold=None, at=None, placed=None):
    caps = RobotView.load("fakebot").model_capabilities()
    svc = NS(lab=lambda *a, **k: svc.labs.append(k), labs=[], robot=NS(model_capabilities=lambda: NS(hands=hands, release_actions=caps.release_actions)
                                                                         if hands != 1 else caps))
    w = WorldState()
    w.update_from_dict({"holding": hold, "arrived": at})
    run = NS(world=w, goals=list(goals), placed=dict(placed or {}))
    return RepairSync(resolve(SPEC, {SPEC.name: "1", **(cfg or {})}, {}, None), svc), run, svc


# ── gate: the blocks lined up with the plan the gate scores ────────────────────────────────────────────────────────────
def test_gate_align_keeps_block_owners_puts_an_added_step_in_the_block_before_it_and_drops_what_the_plan_dropped():
    F = owned(("Walk crate@yard", "Spot gizmo", "Grab gizmo", "Shut crate@yard"), ("Walk bench@yard", "Spot widget", "Spot widget", "Grab widget"))
    plan = steps("Walk crate@yard", "Grab gizmo", "Shut crate@yard", "Walk pad@lawn", "Drop pad@lawn", "Walk bench@yard", "Spot widget", "Grab widget")
    got = align(F, plan)
    assert [s for _o, s in got] == plan
    assert [o for o, _s in got] == [0, 0, 0, 0, 0, 1, 1, 1]           # the two added steps join the Close's block; the dropped Spots are gone
    lead = align(F, steps("Walk dock@yard") + [s for _o, s in F])
    assert lead[0][0] == 0 and len(lead) == len(F) + 1                  # a leading added step joins the first matched step's block


def test_gate_repair_with_sync_moves_the_close_after_the_release_an_option_added_and_writes_the_blocks():
    b1, b2 = Block("fetch", steps(f"MoveTo {DR}", f"Open {DR}", "Find gizmo", "Pick gizmo", f"Close {DR}")), Block("home", steps(f"MoveTo {DS}"))
    plan = steps(f"MoveTo {DR}", f"Open {DR}", "Find gizmo", "Pick gizmo", f"Close {DR}", f"MoveTo {DT}", f"Place {DT}", f"MoveTo {DS}")
    join = lambda bs: [dict(s) for b in bs for s in b.steps]                                             # noqa: E731
    score = lambda s: int(acts(s).index(f"Close {DR}") < acts(s).index(f"Place {DT}")) if f"Place {DT}" in acts(s) else 1   # noqa: E731
    world, caps = WorldState(), RobotView.load("fakebot").model_capabilities()
    places, goals = Places({DR: {}, DT: {}, DS: {}}), [{"type": "on", "object": "gizmo", "from": DR, "to": DT}]
    args = dict(caps=caps, actions={"MoveTo", "Open", "Close", "Find", "Pick", "Place"}, join=join, ground=lambda s: s)
    _s, notes, _c = plan_repair.repair(plan, score, world, places, goals, blocks=[b1, b2], **args)
    assert notes == [] and acts(b1.steps)[-1] == f"Close {DR}"         # off: the blocks have no release, the hoist finds none
    opt, run, svc = make()
    out, notes, counts = plan_repair.repair(plan, score, world, places, goals, blocks=[b1, b2], **args, **opt.repair_kwargs(run))
    assert counts.get("hoist") == 1 and counts.get("synced") == 1 and score(out) == 0 and join([b1, b2]) == out
    assert acts(b1.steps)[-4:] == [f"MoveTo {DT}", f"Place {DT}", f"MoveTo {DR}", f"Close {DR}"] and acts(b2.steps) == [f"MoveTo {DS}"]
    assert svc.labs == [{"part": "sync", "blocks": 6, "plan": 8}]


def test_gate_second_try_only_when_the_blocks_repair_leaves_a_finding_and_kept_only_when_it_certifies():
    calls = []
    join = lambda bs: [dict(s) for b in bs for s in b.steps]                                             # noqa: E731
    args = dict(caps=RobotView.load("fakebot").model_capabilities(), actions={"MoveTo", "Open", "Close", "Find", "Pick", "Place"}, join=join,
                ground=lambda s: s)
    world, places, goals = WorldState(), Places({DR: {}, DT: {}}), [{"type": "on", "object": "gizmo", "from": DR, "to": DT}]
    junk = lambda F, st: calls.append(len(F)) or F + [(0, {"action": "Zap", "object": DT})]               # noqa: E731
    bad = lambda s: sum(x["action"] == "Zap" for x in s) + int("Close" in [x["action"] for x in s][:5])   # noqa: E731
    fine = steps(f"MoveTo {DR}", f"Open {DR}", "Find gizmo", "Pick gizmo", f"Close {DR}", f"MoveTo {DT}", f"Place {DT}")
    b = Block("one", [dict(x) for x in fine])
    out, _n, counts = plan_repair.repair(fine, bad, world, places, goals, blocks=[b], sync=junk, **args)
    assert calls == [] and counts.get("hoist") == 1 and "synced" not in counts       # the blocks' own repair certified: no second try
    stuck = steps("Find sprocket", "Zap sprocket", f"Close {DR}")
    b = Block("one", [dict(x) for x in stuck])
    out, notes, counts = plan_repair.repair(stuck, bad, world, places, goals, blocks=[b], sync=junk, **args)
    assert calls == [3] and notes == [] and acts(b.steps) == acts(stuck)              # tried, did not certify: the first result stands


def test_gate_finish_rest_passes_over_a_goal_done_before_and_delivers_the_pending_one():
    places = Places({"crate@yard": {"openable": True}, "bench@yard": {}, "pad@lawn": {}})
    goals = [{"type": "on", "object": "widget", "from": "", "to": "bench@yard"}, {"type": "on", "object": "gizmo", "from": "", "to": "pad@lawn"}]
    w = WorldState()
    w.update_from_dict({"arrived": "bench@yard"})
    cx = plan_repair._Ctx(w, places, goals, RobotView.load("fakebot").model_capabilities(), {"MoveTo", "Place"}, (), lambda g: False)
    F = owned(("Find gizmo", "Pick gizmo", "MoveTo crate@yard", "Place crate@yard"))
    never = lambda plan, world, gs: False                                                               # noqa: E731
    assert plan_repair.op_finish(F, cx, never) is None                  # off: gives up at the widget (neither held nor let go of here)
    opt, run, svc = make(goals=goals, placed={"widget": "bench@yard"})
    cx.finish_rest = opt.repair_kwargs(run)["finish_rest"]
    assert acts([s for _o, s in plan_repair.op_finish(F, cx, never)]) == ["Find gizmo", "Pick gizmo", "MoveTo pad@lawn", "Place pad@lawn"]
    assert svc.labs == [{"part": "done_goal", "object": "widget"}]
    for placed in ({}, {"widget": "pad@lawn"}):                         # not done in the belief: the search still ends there, as before
        opt, run, _ = make(goals=goals, placed=placed)
        cx.finish_rest = opt.repair_kwargs(run)["finish_rest"]
        assert plan_repair.op_finish(F, cx, never) is None, placed
    opt, run, _ = make(goals=goals, placed={"widget": "bench@yard"}, hold="widget")
    assert opt.done(run, goals[0]) is False                             # placed once, in the hand again: not done


# ── release_off_goal: the coded finding of a suffix ────────────────────────────────────────────────────────────────────
GOALS = [{"type": "in", "object": "gizmo", "from": "", "to": "crate@yard"}, {"type": "on", "object": "widget", "from": "", "to": "bench@yard"}]


def test_release_off_goal_finding_for_the_last_release_of_a_goal_object_elsewhere():
    opt, run, svc = make(goals=GOALS)
    sfx = steps("Find gizmo", "Pick gizmo", "MoveTo pad@lawn", "Place pad@lawn")
    got = opt.on_replan_findings(["engine finding"], run, sfx)
    assert got[0] == "engine finding" and len(got) == 2 and got[1].startswith(FINDING) and "step 4" in got[1] and "crate@yard" in got[1]
    assert svc.labs == [{"part": "off_goal", "findings": 1}]
    assert opt.on_replan_findings(got, run, sfx) is None                # already there: nothing added twice
    opt, run, _ = make(goals=GOALS, hold="widget", at="pad@lawn")
    assert "bench@yard" in opt.on_replan_findings([], run, steps("Place pad@lawn"))[0]     # held from the belief, put down elsewhere


def test_release_off_goal_not_for_a_staging_put_down_picked_again_the_goal_place_or_an_untouched_goal():
    opt, run, _ = make(goals=GOALS)
    staging = steps("Find gizmo", "Pick gizmo", "Place pad@lawn", "Pick gizmo", "MoveTo crate@yard", "PutIn crate@yard")
    assert opt.on_replan_findings([], run, staging) is None
    assert opt.on_replan_findings([], run, steps("Find widget", "Pick widget", "MoveTo bench@yard", "Place bench@yard")) is None
    assert opt.on_replan_findings([], run, steps("Find sprocket", "Pick sprocket", "Place pad@lawn")) is None          # no goal of it
    assert opt.on_replan_findings([], run, steps("Find gizmo", "Pick gizmo", "MoveTo pad@lawn")) is None               # still held: not let go of


def test_release_off_goal_two_hands_or_no_goals_or_parts_off_do_nothing_empty():
    sfx = steps("Find gizmo", "Pick gizmo", "Place pad@lawn")
    for kw in ({"hands": 2}, {"goals": []}, {"cfg": {"repair_sync.off_goal": "0"}}):
        opt, run, _ = make(**{"goals": GOALS, **kw})
        assert opt.on_replan_findings([], run, sfx) is None, kw
    opt, run, _ = make(goals=GOALS)
    assert opt.on_replan_findings([], run, []) is None
    off = make(cfg={"repair_sync.gate_sync": "0", "repair_sync.finish_rest": "0"})[0]
    assert off.repair_kwargs(run) is None
    assert set(make()[0].repair_kwargs(run)) == {"sync", "finish_rest"}
    F = owned(("Walk crate@yard",))
    assert align(F, []) == F and align([], steps("Walk crate@yard")) == []


# ── the real loop ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _gate_run(make_assist, env, on, planner=None):
    """The class on the test house: the planner's block picks the gizmo and closes the drawer it took it from with the hand full; precond_repair
    (made-up data: Place ends an ``on`` goal) appends the delivery after the Close. Only the plan the gate scores has that release."""
    planner = planner or BlockPlanner([Block("fetch", steps(f"MoveTo {DR}", f"Open {DR}", "Find gizmo", "Pick gizmo", f"Close {DR}"))])
    robot = FakeRobot(env, {"gizmo": DR})
    loop = make_assist(robot, FakeLLM(understood(("on", "gizmo", "drawer", "dining table"))), planner,
                       **{"precond_repair": "1", **({"repair_sync": "1"} if on else {})})
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    pl["options"] = {"precond_repair": {"needs": {"rules": {"Place": {"holding": True, "achieves": "on"}}, "source": "made-up pack"}}}
    return loop, robot, run_task(loop, "Put the gizmo from the drawer on the dining table.", max_events=3000), planner


def test_registered_and_off_by_default_the_gate_cannot_repair_the_option_step_off(make_assist, env):
    assert "repair_sync" in ASSIST_OPTIONS and REGISTRY.get("repair_sync").key("enabled").default is False
    loop, robot, evs, planner = _gate_run(make_assist, env, on=False)
    assert loop.options.query("repair_kwargs", None) is None and not [e for e in loop.lab_log if e["flag"] == "repair_sync"]
    assert done(evs)["status"] == "failed" and robot.obj == {"gizmo": DR}                        # the gate gave up, nothing moved
    assert acts(planner.blocks()[0].steps)[-1] == f"Close {DR}"


def test_gate_loop_final_the_close_goes_after_the_option_release_and_the_gizmo_is_delivered(make_assist, env):
    loop, robot, evs, planner = _gate_run(make_assist, env, on=True)
    sym = next(e for e in evs if e["event"] == "gate" and e.get("round") == "symbolic")
    assert sym["problems"] == [] and sym["plan"][-2:] == [f"MoveTo {DR}", f"Close {DR}"]
    assert done(evs)["status"] == "success" and robot.obj == {"gizmo": DT} and robot.holding is None
    assert DR not in robot.opened and [t[0] for t in robot.trace].count("place") == 1               # closed again, one release, none twice
    assert acts(planner.blocks()[0].steps) == sym["plan"] and planner.edits == []                    # written into the block, no model edit


def test_gate_planner_without_blocks_is_left_to_the_old_repair_empty(make_assist, env):
    plan = steps(f"MoveTo {DR}", f"Open {DR}", "Find gizmo", "Pick gizmo", f"Close {DR}")
    loop, robot, evs, _p = _gate_run(make_assist, env, on=True, planner=ScriptedPlanner(plan))
    assert not [e for e in loop.lab_log if e["flag"] == "repair_sync" and e["part"] == "sync"]   # no blocks: nothing to line up


TWO = (("on", "widget", "desk", "dining table"), ("in", "gizmo", "counter", "drawer"))
PLAN = (f"MoveTo {DS}", "Find widget", "Pick widget", f"MoveTo {DT}", f"Place {DT}", f"MoveTo {DR}", f"Open {DR}",
        f"MoveTo {CT}", "Find gizmo", "Pick gizmo", f"MoveTo {DR}", f"PutIn {DR}")
WRONG = ("Find gizmo", "Pick gizmo", f"MoveTo {DT}", f"Place {DT}")              # the suffix puts the gizmo where the widget went
RIGHT = ("Find gizmo", "Pick gizmo", f"MoveTo {DR}", f"PutIn {DR}")


def _suffix_run(make_assist, env, opts, replans=(WRONG, WRONG, WRONG)):
    robot = FakeRobot(env, {"widget": DS, "gizmo": CT}, faults=[{"skill": "pick", "obj": "gizmo", "nth": [1, 2], "kind": "fail"}])
    planner = ScriptedPlanner(steps(*PLAN), replans=[steps(*r) for r in replans])
    loop = make_assist(robot, FakeLLM(understood(*TWO)), planner, **opts)
    return loop, robot, run_task(loop, "Put the widget on the dining table and the gizmo in the drawer.", max_events=3000), planner


def test_release_off_goal_suffix_off_runs_and_leaves_the_gizmo_on_the_wrong_table_off(make_assist, env):
    loop, robot, evs, _p = _suffix_run(make_assist, env, {})
    assert robot.obj["gizmo"] == DT and done(evs)["status"] == "failed"


def test_release_off_goal_loop_final_finish_passes_the_done_goal_and_the_gizmo_ends_in_the_drawer(make_assist, env):
    loop, robot, evs, planner = _suffix_run(make_assist, env, {"repair_sync": "1"})
    rp = [e for e in evs if e["event"] == "replan"]
    assert rp and rp[0]["plan"][-2:] == [f"MoveTo {DR}", f"PutIn {DR}"]
    assert done(evs)["status"] == "success" and robot.obj == {"widget": DT, "gizmo": DR + "/in"}
    assert [c[0] for c in planner.calls].count("replan") == 1


@pytest.mark.parametrize("refine, gizmo", [(RIGHT, DR + "/in"), (WRONG, CT)])
def test_release_off_goal_loop_final_the_finding_reaches_the_refine_and_a_wrong_suffix_never_runs(make_assist, env, refine, gizmo):
    loop, robot, evs, planner = _suffix_run(make_assist, env, {"repair_sync": "1", "repair_sync.finish_rest": "0"}, replans=(WRONG, refine, WRONG))
    calls = [c for c in planner.calls if c[0] == "replan"]
    assert len(calls) >= 2 and FINDING in calls[1][2]                                             # the refine was told
    assert robot.obj["gizmo"] == gizmo and robot.obj["widget"] == DT
    assert done(evs)["status"] == ("success" if refine == RIGHT else "failed")
