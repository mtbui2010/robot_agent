"""Option ``plan_complete`` (default OFF): a plan that stops short of the step its goal needs is completed from declared pack data. Hook-level tests on a
made-up pack (skills Stow / Offer / Chirp / Shut / Ajar / Halt, roles Walk / Shut / Ajar / Chirp) and one loop-level test of the default path. Nothing here
is a real robot's skill or a real task."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.plan_complete import PlanComplete, SPEC, rules_of

ROLES = {"move": "Walk", "open": "Ajar", "close": "Shut", "say": "Chirp", "pick": "Seize", "put_down": "Drop"}
RULES = {"rules": {"Stow": {"achieves": "in"}, "Offer": {"achieves": "hand", "near": True}, "Chirp": {"near": True}}, "source": "made-up pack"}
ENV = {"crate@yard": {}, "ana@hall": {"person": True}, "bo@lawn": {"person": True}, "pad@yard": {}}
PUT_IN = {"type": "in", "object": "gizmo", "from": "", "to": "crate"}
HAND = {"type": "on", "object": "gizmo", "from": "", "to": "ana", "hand": True}
TELL = {"type": "say", "object": "come here", "from": "", "to": "bo"}
END = {"places": ["pad"], "action": "Halt"}


def make(needs=RULES, roles=ROLES, hold=None, at=None, goals=(PUT_IN,), end=END):
    svc = NS(role=lambda r: (roles or {}).get(r), lab=lambda *a, **k: svc.labs.append((a, k)), labs=[],
             robot=NS(manifest=NS(planner={"end_state": end} if end else {})))
    cfg = resolve(SPEC, {"plan_complete": "1"}, {}, {"options": {"plan_complete": {"needs": needs}}} if needs is not None else None)
    run = NS(world=NS(arrived=at, holding=hold), places=Places(ENV), goals=list(goals))
    return PlanComplete(cfg, svc), run, svc


def acts(plan):
    return [(s["action"], s["object"]) for s in plan]


def test_a_release_is_spliced_before_the_close_of_the_goal_place():
    opt, run, svc = make()
    got = opt.on_plan(steps("Walk crate@yard", "Ajar crate@yard", "Seize gizmo", "Shut crate@yard"), run)
    assert acts(got) == [("Walk", "crate@yard"), ("Ajar", "crate@yard"), ("Seize", "gizmo"), ("Stow", "crate@yard"), ("Shut", "crate@yard")]
    opt, run, svc = make(hold="gizmo", at="crate@yard")
    got = opt.on_plan(steps("Shut crate@yard"), run)
    assert acts(got) == [("Stow", "crate@yard"), ("Shut", "crate@yard")] and svc.labs[0][1]["need"] == "walk"


def test_the_release_goes_after_an_approach_and_after_an_open():
    opt, run, _ = make(hold="gizmo", at="pad@yard")
    assert acts(opt.on_plan(steps("Shut crate@yard"), run)) == [("Walk", "crate@yard"), ("Stow", "crate@yard"), ("Shut", "crate@yard")]
    opt, run, _ = make(hold="gizmo", at="crate@yard")
    assert acts(opt.on_plan(steps("Ajar crate@yard"), run)) == [("Ajar", "crate@yard"), ("Stow", "crate@yard")]


def test_a_release_already_in_the_plan_or_another_place_is_left_alone():
    opt, run, _ = make(hold="gizmo", at="crate@yard")
    assert opt.on_plan(steps("Stow crate@yard", "Shut crate@yard"), run) is None
    assert opt.on_plan(steps("Shut crate@yard", "Stow crate@yard"), run) is None
    opt, run, _ = make(hold="gizmo", at="pad@yard")
    assert opt.on_plan(steps("Shut pad@yard"), run) is None                          # not the goal place
    opt, run, _ = make(hold=None, at="crate@yard")
    assert opt.on_plan(steps("Shut crate@yard"), run) is None                        # nothing held


def test_a_delivery_appended_after_the_way_home_goes_before_it_with_the_approach():
    opt, run, svc = make(hold="gizmo", at="pad@yard", goals=[HAND])
    got = opt.on_plan(steps("Walk pad@yard", "Halt pad@yard", "Offer ana@hall"), run)
    assert acts(got) == [("Walk", "ana@hall"), ("Offer", "ana@hall"), ("Walk", "pad@yard"), ("Halt", "pad@yard")]
    assert acts(got).index(("Offer", "ana@hall")) < acts(got).index(("Halt", "pad@yard"))
    assert acts(got).index(("Walk", "ana@hall")) < acts(got).index(("Offer", "ana@hall"))
    assert any(k.get("need") == "before_home" for _a, k in svc.labs)


def test_a_hand_over_gets_the_move_to_the_person_when_the_robot_is_elsewhere():
    opt, run, _ = make(hold="gizmo", at="pad@yard", goals=[HAND])
    assert acts(opt.on_plan(steps("Offer ana@hall"), run)) == [("Walk", "ana@hall"), ("Offer", "ana@hall")]
    opt, run, _ = make(hold="gizmo", at="ana@hall", goals=[HAND])
    assert opt.on_plan(steps("Offer ana@hall"), run) is None


def test_a_speech_from_across_the_room_is_moved_next_to_the_listener_and_rebuilt():
    opt, run, _ = make(at="pad@yard", goals=[TELL])
    assert acts(opt.on_plan(steps("Chirp come here"), run)) == [("Walk", "bo@lawn"), ("Chirp", "come here")]
    assert acts(opt.on_plan(steps("Chirp bo@lawn"), run)) == [("Walk", "bo@lawn"), ("Chirp", "come here")]
    opt, run, _ = make(at="bo@lawn", goals=[TELL])
    assert opt.on_plan(steps("Chirp come here"), run) is None


def test_two_say_goals_are_not_guessed_for_a_step_that_names_no_listener():
    two = [TELL, {"type": "say", "object": "go away", "from": "", "to": "ana"}]
    opt, run, _ = make(at="pad@yard", goals=two)
    assert opt.on_plan(steps("Chirp bo@lawn"), run) == acts_to_steps([("Walk", "bo@lawn"), ("Chirp", "come here")])
    assert opt.on_plan(steps("Chirp a stranger"), run) is None


def acts_to_steps(pairs):
    return [{"action": a, "object": o} for a, o in pairs]


def test_empty_or_unlabelled_or_foreign_pack_data_does_nothing_empty():
    plan = steps("Shut crate@yard")
    for needs in (None, {}, {"rules": RULES["rules"]}, {"rules": "bad", "source": "x"}, {"rules": {"Zap": {"near": True}}, "source": "x"}):
        opt, run, _ = make(needs=needs, hold="gizmo", at="crate@yard")
        assert opt.on_plan(plan, run) is None, needs
    opt, run, _ = make(roles={}, hold="gizmo", at="crate@yard")
    assert opt.on_plan(plan, run) is None and opt.on_plan([], run) is None
    opt, run, _ = make(hold="gizmo", at="crate@yard", goals=[])
    assert opt.on_plan(plan, run) is None
    assert rules_of({"rules": RULES["rules"], "assumed": True}) and not rules_of({"rules": RULES["rules"], "assumed": "yes"})


def test_no_end_state_data_leaves_the_order_of_the_plan_empty():
    opt, run, _ = make(hold="gizmo", at="ana@hall", goals=[HAND], end=None)
    assert opt.on_plan(steps("Walk pad@yard", "Halt pad@yard", "Offer ana@hall"), run) == acts_to_steps(
        [("Walk", "pad@yard"), ("Halt", "pad@yard"), ("Walk", "ana@hall"), ("Offer", "ana@hall")])    # only the approach is added


def _loop_run(make_assist, env, on):
    """A plan that picks the mug and closes the drawer the goal puts it in, with no release between; the pack declares the release skill."""
    robot = FakeRobot(env, {"mug": "counter@kitchen"}, start="counter@kitchen")
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "in", "object": "mug", "from": "counter", "to": "drawer"}]}
    plan = steps("MoveTo counter@kitchen", "Find mug", "Pick mug", "MoveTo drawer@kitchen", "Close drawer@kitchen")
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, **({"plan_complete": "1"} if on else {}))
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo", "close": "Close", "open": "Open", "pick": "Pick"}
    pl["options"] = {"plan_complete": {"needs": {"rules": {"PutIn": {"achieves": "in"}}, "source": "made-up pack"}}}
    run_task(loop, "Put the mug in the drawer.", max_events=3000)
    return loop


def test_registered_and_off_by_default_the_loop_is_unchanged_off(make_assist, env):
    assert "plan_complete" in ASSIST_OPTIONS and REGISTRY.get("plan_complete").key("enabled").default is False
    assert not [e for e in _loop_run(make_assist, env, on=False).lab_log if e["flag"] == "plan_complete"]
    assert [e for e in _loop_run(make_assist, env, on=True).lab_log if e["flag"] == "plan_complete"], "the option did not act"
