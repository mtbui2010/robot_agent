"""Option ``plan_release`` (default OFF): the held object's own release goes before the Close of its ``in`` goal place, whatever other object the plan
releases later, and the release that splice empties gets its fetch (or goes). Hook-level tests on a made-up pack (skills Stow / Drop / Offer, roles
Walk / Spot / Grab / Shut / Ajar) and loop-level tests of the FINAL state on the test house. Nothing here is a real robot's skill or a real task."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options import plan_complete, precond_repair
from robot_agent.options.plan_release import SPEC, PlanRelease, rules_of

ROLES = {"move": "Walk", "find": "Spot", "pick": "Grab", "close": "Shut", "open": "Ajar", "put_down": "Drop"}
RULES = {"rules": {"Stow": {"achieves": "in"}, "Drop": {"achieves": "on"}, "Offer": {"achieves": "hand"}}, "source": "made-up pack"}
ENV = {"crate@yard": {}, "bench@yard": {}, "pad@lawn": {}, "rack@den": {}}
IN = {"type": "in", "object": "gizmo", "from": "", "to": "crate"}
ON = {"type": "on", "object": "widget", "from": "", "to": "bench"}
CLASS = ("Walk crate@yard", "Ajar crate@yard", "Spot gizmo", "Grab gizmo", "Shut crate@yard", "Walk bench@yard", "Drop bench@yard")


def make(needs=RULES, roles=ROLES, hold=None, at=None, goals=(IN, ON), hands=1, opt=PlanRelease, spec=SPEC):
    svc = NS(role=lambda r: (roles or {}).get(r), hand_action=lambda: None, lab=lambda *a, **k: svc.labs.append((a, k)), labs=[],
             robot=NS(manifest=NS(planner={}), model_capabilities=lambda: NS(hands=hands, release_actions=[])))
    cfg = resolve(spec, {spec.name: "1"}, {}, {"options": {spec.name: {"needs": needs}}} if needs is not None else None)
    run = NS(world=NS(arrived=at, holding=hold), places=Places(ENV), goals=list(goals))
    o = opt(cfg, svc)
    o.on_start(run) if hasattr(o, "on_start") else None
    return o, run, svc


def acts(plan):
    return [f'{s["action"]} {s["object"]}' for s in plan]


def test_next_release_elsewhere_gets_the_put_in_before_the_close_and_the_regression_of_any_later_release():
    opt, run, svc = make()
    got = acts(opt.on_plan(steps(*CLASS), run))
    assert got[:6] == ["Walk crate@yard", "Ajar crate@yard", "Spot gizmo", "Grab gizmo", "Stow crate@yard", "Shut crate@yard"]
    # the class: plan_complete leaves this plan alone because a release (of another object) comes later
    pc = make(needs={"rules": {"Stow": {"achieves": "in"}}, "source": "made-up pack"}, opt=plan_complete.PlanComplete, spec=plan_complete.SPEC)
    assert pc[0].on_plan(steps(*CLASS), pc[1]) is None


def test_next_release_at_the_goal_place_or_a_put_in_already_there_is_left_alone():
    opt, run, _ = make(hold="gizmo", at="crate@yard")
    assert opt.on_plan(steps("Stow crate@yard", "Shut crate@yard"), run) is None
    assert opt.on_plan(steps("Shut crate@yard", "Drop crate@yard"), run) is None      # next release is at the goal place: the gate's swap
    assert opt.on_plan(steps("Shut pad@lawn", "Walk bench@yard", "Drop bench@yard"), run) is None   # not the goal place
    opt, run, _ = make(hold=None, at="crate@yard")
    assert opt.on_plan(steps("Shut crate@yard"), run) is None


def test_next_release_none_before_another_pick_splices_and_keeps_the_rest():
    opt, run, _ = make(hold="gizmo", at="crate@yard", goals=[IN, {"type": "in", "object": "bolt", "from": "", "to": "rack"}])
    got = acts(opt.on_plan(steps("Shut crate@yard", "Walk pad@lawn", "Grab bolt", "Walk rack@den", "Drop rack@den"), run))
    assert got == ["Stow crate@yard", "Shut crate@yard", "Walk pad@lawn", "Grab bolt", "Walk rack@den", "Drop rack@den"]   # Drop -> put-in is the gate's


def test_next_release_matches_the_goal_object_in_other_words_and_never_guesses_between_two():
    opt, run, _ = make(hold="small gizmo", at="crate@yard", goals=[IN])
    assert acts(opt.on_plan(steps("Shut crate@yard", "Walk bench@yard", "Drop bench@yard"), run))[:2] == ["Stow crate@yard", "Shut crate@yard"]
    two = [IN, {"type": "in", "object": "red gizmo case", "from": "", "to": "rack"}]
    opt, run, _ = make(hold="gizmo", at="crate@yard", goals=two)
    assert acts(opt.on_plan(steps("Shut crate@yard"), run)) == ["Stow crate@yard", "Shut crate@yard"]     # the exact name first
    opt, run, _ = make(hold="red gizmo", at="crate@yard", goals=two)
    assert opt.on_plan(steps("Shut crate@yard"), run) is None                                                # two fits: no guess


def test_put_in_spliced_after_an_approach_and_no_swap_of_a_place_into_a_closed_container():
    opt, run, _ = make(hold="gizmo", at="pad@lawn", goals=[IN])
    assert acts(opt.on_plan(steps("Shut crate@yard"), run)) == ["Walk crate@yard", "Stow crate@yard", "Shut crate@yard"]
    plan = steps("Walk pad@lawn", "Spot gizmo", "Grab gizmo", "Walk crate@yard", "Drop crate@yard")
    assert opt.on_plan(plan, run) is None                     # a put-down into the goal container is not swapped (op_putin opens it first)


def test_pick_step_for_the_goal_object_the_plan_never_picked_before_the_move_to_its_place():
    opt, run, svc = make()
    got = acts(opt.on_plan(steps(*CLASS), run))
    assert got[6:] == ["Spot widget", "Grab widget", "Walk bench@yard", "Drop bench@yard"]
    assert [k["fix"] for _a, k in svc.labs] == ["pick_step"]
    opt, run, _ = make(hold="gizmo", at="crate@yard")
    assert acts(opt.on_plan(steps("Shut crate@yard", "Drop bench@yard"), run)) == [
        "Stow crate@yard", "Shut crate@yard", "Spot widget", "Grab widget", "Drop bench@yard"]


def test_pick_step_brings_forward_the_fetch_the_plan_left_after_the_release_and_ends_holding():
    opt, run, svc = make()
    got = acts(opt.on_plan(steps(*CLASS, "Spot widget", "Grab widget"), run))
    assert got == ["Walk crate@yard", "Ajar crate@yard", "Spot gizmo", "Grab gizmo", "Stow crate@yard", "Shut crate@yard",
                   "Spot widget", "Grab widget", "Walk bench@yard", "Drop bench@yard"]
    assert opt.on_plan(steps(*got), run) is None and [k["fix"] for _a, k in svc.labs] == ["pick_step"]


def test_an_emptied_release_whose_object_is_picked_and_released_there_later_is_removed():
    opt, run, svc = make()
    got = acts(opt.on_plan(steps(*CLASS, "Spot widget", "Grab widget", "Drop bench@yard"), run))
    assert got == ["Walk crate@yard", "Ajar crate@yard", "Spot gizmo", "Grab gizmo", "Stow crate@yard", "Shut crate@yard",
                   "Walk bench@yard", "Spot widget", "Grab widget", "Drop bench@yard"]
    assert [k["fix"] for _a, k in svc.labs] == ["removed"]


def test_the_last_release_of_an_unpicked_goal_object_is_never_deleted_and_two_candidates_are_not_guessed():
    two = [IN, ON, {"type": "on", "object": "sprocket", "from": "", "to": "bench"}]
    opt, run, svc = make(goals=two)
    got = acts(opt.on_plan(steps(*CLASS), run))
    assert got[-2:] == ["Walk bench@yard", "Drop bench@yard"] and "Grab widget" not in got and [k["fix"] for _a, k in svc.labs] == ["splice"]
    opt, run, _ = make(goals=[IN, ON])
    got = acts(opt.on_plan(steps(*CLASS, "Spot widget", "Grab widget", "Walk rack@den", "Drop rack@den"), run))
    assert got.count("Drop bench@yard") == 1                  # widget released elsewhere later: its place keeps the release


def test_a_staging_release_of_an_object_picked_again_later_is_left_alone():
    opt, run, _ = make(hold="gizmo", at="crate@yard")
    plan = steps("Shut crate@yard", "Walk pad@lawn", "Drop pad@lawn", "Grab gizmo", "Walk crate@yard", "Stow crate@yard")
    assert opt.on_plan(plan, run) is None


def test_two_hands_or_no_put_in_skill_or_no_roles_change_nothing_empty():
    for kw in ({"hands": 2}, {"needs": {"rules": {"Drop": {"achieves": "on"}}, "source": "made-up pack"}}, {"roles": {"move": "Walk"}}):
        opt, run, _ = make(**kw)
        assert opt.on_plan(steps(*CLASS), run) is None, kw


def test_empty_or_unlabelled_or_foreign_pack_data_does_nothing_empty():
    for needs in (None, {}, {"rules": RULES["rules"]}, {"rules": "bad", "source": "x"}, {"rules": {"Zap": {"near": True}}, "source": "x"}):
        opt, run, _ = make(needs=needs)
        assert opt.on_plan(steps(*CLASS), run) is None, needs
    opt, run, _ = make(goals=[])
    assert opt.on_plan(steps(*CLASS), run) is None and opt.on_plan([], run) is None
    assert rules_of({"rules": RULES["rules"], "assumed": True}) == {"Stow": "in", "Drop": "on", "Offer": "hand"}
    assert not rules_of({"rules": RULES["rules"], "assumed": "yes"})


def _chain(order, plan):
    """The on_plan options in ``order`` over ``plan`` (each sees the previous result), as the loop's transform does."""
    pr_rules = {"rules": {"Grab": {"hand_free": True}, "Drop": {"holding": True, "achieves": "on"}, "Stow": {"holding": True}}, "source": "made-up pack"}
    built = {"precond": make(needs=pr_rules, opt=precond_repair.PrecondRepair, spec=precond_repair.SPEC),
             "complete": make(needs={"rules": {"Stow": {"achieves": "in"}}, "source": "made-up pack"}, opt=plan_complete.PlanComplete, spec=plan_complete.SPEC),
             "release": make()}
    for name in order:
        o, run, _ = built[name]
        plan = o.on_plan(plan, run) or plan
    return plan


def test_with_precond_repair_and_plan_complete_in_both_orders_no_step_twice_and_idempotent():
    for order in (("precond", "complete", "release"), ("precond", "release", "complete"), ("release", "precond", "complete")):
        once = _chain(order, steps(*CLASS))
        assert acts(once).count("Stow crate@yard") == 1 and acts(once).count("Grab widget") == 1, (order, acts(once))
        assert _chain(order, once) == once, order


def test_lab_event_once_per_distinct_edit_in_a_run():
    opt, run, svc = make()
    for _ in range(5):
        opt.on_plan(steps(*CLASS), run)
    assert len(svc.labs) == 1 and svc.labs[0][1] == {"object": "gizmo", "place": "crate@yard", "fix": "pick_step"}
    opt.on_start(run)
    opt.on_plan(steps(*CLASS), run)
    assert len(svc.labs) == 2                                 # a new run logs again


GOALS = [{"type": "in", "object": "gizmo", "from": "", "to": "drawer"}, {"type": "on", "object": "widget", "from": "", "to": "side table"}]
PLAN = ("MoveTo drawer@kitchen", "Open drawer@kitchen", "Find gizmo", "Pick gizmo", "Close drawer@kitchen",
        "MoveTo side table@living room", "Place side table@living room")


def _loop_run(make_assist, env, on):
    """The class on the test house, with the two older on_plan options on (as in the arm it is added to): the plan picks the gizmo, closes the
    drawer its goal names and puts down at the widget's goal place, a widget it never picked."""
    robot = FakeRobot(env, {"gizmo": "drawer@kitchen", "widget": "drawer@kitchen"}, start="entrance")
    und = {"status": "clear", "reason": "", "question": "", "goals": GOALS}
    older = {"precond_repair": "1", "plan_complete": "1"}
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(steps(*PLAN)), detector_miss_rate=0, **({**older, "plan_release": "1"} if on else older))
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "open": "Open", "close": "Close", "put_down": "Place"}
    pl["options"] = {"plan_release": {"needs": {"rules": {"PutIn": {"achieves": "in"}, "Place": {"achieves": "on"}}, "source": "made-up pack"}},
                     "plan_complete": {"needs": {"rules": {"PutIn": {"achieves": "in"}}, "source": "made-up pack"}},
                     "precond_repair": {"needs": {"rules": {"Pick": {"hand_free": True}, "Place": {"holding": True, "achieves": "on"},
                                                            "PutIn": {"holding": True}}, "source": "made-up pack"}}}
    evs = run_task(loop, "Put the gizmo in the drawer and the widget on the side table.", max_events=3000)
    return loop, robot, evs


def test_registered_and_off_by_default_the_loop_is_unchanged_off(make_assist, env):
    assert "plan_release" in ASSIST_OPTIONS and REGISTRY.get("plan_release").key("enabled").default is False
    loop, robot, evs = _loop_run(make_assist, env, on=False)
    assert not [e for e in loop.lab_log if e["flag"] == "plan_release"]
    assert done(evs)["status"] == "failed" and robot.obj == {"gizmo": "drawer@kitchen", "widget": "drawer@kitchen"}   # the gate gave up, nothing moved


def test_loop_final_state_holds_both_goals_with_the_option_on(make_assist, env):
    loop, robot, evs = _loop_run(make_assist, env, on=True)
    assert [e["fix"] for e in loop.lab_log if e["flag"] == "plan_release"] == ["pick_step"]
    assert robot.obj == {"gizmo": "drawer@kitchen/in", "widget": "side table@living room"} and robot.holding is None
    assert "drawer@kitchen" not in robot.opened and done(evs)["status"] == "success"
    assert [t[0] for t in robot.trace].count("place") == 2                 # one release each, none twice
