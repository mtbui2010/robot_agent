"""Option ``precond_repair`` (default OFF): skills declare what they need (a held object, a free hand, being at the object) as pack data; a plan
that lacks the step gets it. Hook-level tests on a made-up pack (skills Offer / Grab / Chirp, roles Walk / Scan / Seize / Drop) and one loop-level
test of the default path. Nothing here is a real robot's skill."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.precond_repair import PrecondRepair, SPEC, rules_of

ROLES = {"move": "Walk", "find": "Scan", "pick": "Seize", "put_down": "Drop"}
RULES = {"rules": {"Offer": {"holding": True, "near": True}, "Grab": {"hand_free": True}, "Chirp": {"near": True}}, "source": "made-up pack"}
ENV = {"shelf@hall": {}, "ana@hall": {}, "bin@yard": {}}
GOALS = [{"type": "on", "object": "gizmo", "from": "shelf", "to": "ana", "hand": True}]


def make(needs=RULES, roles=ROLES, hold=None, at=None, goals=GOALS, releases=()):
    svc = NS(role=lambda r: (roles or {}).get(r), hand_action=lambda: None, lab=lambda *a, **k: svc.labs.append((a, k)), labs=[],
             robot=NS(manifest=NS(planner={}), model_capabilities=lambda: NS(release_actions=set(releases))))
    cfg = resolve(SPEC, {"precond_repair": "1"}, {}, {"options": {"precond_repair": {"needs": needs}}} if needs is not None else None)
    run = NS(world=NS(arrived=at, holding=hold), places=Places(ENV), goals=goals)
    return PrecondRepair(cfg, svc), run, svc


def acts(plan):
    return [(s["action"], s["object"]) for s in plan]


def test_missing_pick_is_inserted_before_the_approach():
    opt, run, svc = make()
    got = opt.on_plan(steps("Walk ana@hall", "Offer ana@hall"), run)
    assert acts(got) == [("Scan", "gizmo"), ("Seize", "gizmo"), ("Walk", "ana@hall"), ("Offer", "ana@hall")]
    assert svc.labs and svc.labs[0][1]["need"] == "holding"


def test_missing_free_hand_puts_the_held_object_down_at_its_goal_place():
    goals = [{"type": "on", "object": "toy", "from": "", "to": "bin"}]
    opt, run, _ = make(hold="toy", at="shelf@hall", goals=goals)
    got = opt.on_plan(steps("Grab gizmo"), run)
    assert acts(got) == [("Walk", "bin@yard"), ("Drop", "bin@yard"), ("Grab", "gizmo")]


def test_missing_approach_is_inserted():
    opt, run, _ = make(at="bin@yard")
    assert acts(opt.on_plan(steps("Chirp ana@hall"), run)) == [("Walk", "ana@hall"), ("Chirp", "ana@hall")]


def test_a_precondition_already_true_inserts_nothing():
    opt, run, _ = make(hold="gizmo", at="ana@hall")
    assert opt.on_plan(steps("Offer ana@hall"), run) is None
    opt, run, _ = make(at="shelf@hall")
    assert opt.on_plan(steps("Scan gizmo", "Seize gizmo", "Walk ana@hall", "Offer ana@hall"), run) is None      # the plan's own pick fills the hand


def test_unknown_skills_are_left_alone():
    opt, run, _ = make()
    assert opt.on_plan(steps("Twirl ana@hall", "Spin gizmo"), run) is None


def test_an_ambiguous_object_or_a_missing_goal_is_not_guessed():
    two = GOALS + [{"type": "on", "object": "gadget", "from": "", "to": "ana"}]
    opt, run, _ = make(goals=two)
    assert opt.on_plan(steps("Walk ana@hall", "Offer ana@hall"), run) is None          # two candidates: no Pick is guessed, the approach is there
    opt, run, _ = make(hold="toy", at="shelf@hall", goals=[])
    assert opt.on_plan(steps("Grab gizmo"), run) is None                  # no goal says where the held toy goes


def test_a_role_not_declared_skips_that_repair_empty_roles():
    opt, run, _ = make(roles={"move": "Walk"})
    assert opt.on_plan(steps("Offer ana@hall"), run) == acts_to_steps([("Walk", "ana@hall"), ("Offer", "ana@hall")])
    opt, run, _ = make(roles={})
    assert opt.on_plan(steps("Offer ana@hall"), run) is None


def acts_to_steps(pairs):
    return [{"action": a, "object": o} for a, o in pairs]


def test_empty_or_unlabelled_or_foreign_pack_data_does_nothing_empty():
    plan = steps("Walk ana@hall", "Offer ana@hall")
    for needs in (None, {}, {"rules": RULES["rules"]}, {"rules": "bad", "source": "x"}, {"rules": {"Zap": {"near": True}}, "source": "x"}):
        opt, run, _ = make(needs=needs)
        assert opt.on_plan(plan, run) is None, needs
    assert rules_of({"rules": RULES["rules"], "assumed": True}) and not rules_of({"rules": RULES["rules"], "assumed": "yes"})


def test_registered_and_off_by_default_the_loop_is_unchanged_off(make_assist, env):
    assert "precond_repair" in ASSIST_OPTIONS and REGISTRY.get("precond_repair").key("enabled").default is False
    plan = steps("MoveTo drawer@kitchen", "Find mug", "Pick mug")
    robot = FakeRobot(env, {"mug": "drawer@kitchen"})
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "drawer", "to": "dining table"}]}
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0)
    loop.robot.manifest.planner["options"] = {"precond_repair": {"needs": RULES}}
    run_task(loop, "Put the mug on the dining table.", max_events=3000)
    assert not [e for e in loop.lab_log if e["flag"] == "precond_repair"]


def _loop_run(make_assist, env, on):
    """A plan that puts the mug down without ever picking it; the pack says a put-down needs a held object."""
    from fakes import done
    robot = FakeRobot(env, {"mug": "counter@kitchen"}, start="counter@kitchen")
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "counter", "to": "dining table"}]}
    plan = steps("MoveTo dining table@kitchen", "Place dining table@kitchen")
    opts = {"precond_repair": "1"} if on else {}
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, **opts)
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    pl["options"] = {"precond_repair": {"needs": {"rules": {"Place": {"holding": True}}, "source": "made-up pack"}}}
    return loop, done(run_task(loop, "Put the mug on the dining table.", max_events=3000))


def test_the_loop_completes_the_plan_on_and_does_not_off(make_assist, env):
    loop, d = _loop_run(make_assist, env, on=True)
    assert [e for e in loop.lab_log if e["flag"] == "precond_repair"], "the option did not act"
    loop0, d0 = _loop_run(make_assist, env, on=False)
    assert not [e for e in loop0.lab_log if e["flag"] == "precond_repair"]
    assert d["status"] == "success", d
    assert d0["status"] != "success", d0


def _status(make_assist, env, plan, **opts):
    from fakes import done
    robot = FakeRobot(env, {"mug": "counter@kitchen"}, start="counter@kitchen")
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "counter", "to": "dining table"}]}
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(steps(*plan)), detector_miss_rate=0, **opts)
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    pl["options"] = {"precond_repair": {"needs": {"rules": {"Place": {"holding": True}}, "source": "made-up pack"}}}
    return done(run_task(loop, "Put the mug on the dining table.", max_events=3000))["status"]


def test_gate_complete_on_and_off_with_precond_repair_gives_the_same_result(make_assist, env):
    """Verdict recorded in docs/home/bench/d2_layers.md: ``gate_complete`` neither rescues a plan with a missing step on its own, nor
    changes the outcome when ``precond_repair`` is on; so it is not switched on for D1."""
    bad, good = ["MoveTo dining table@kitchen", "Place dining table@kitchen"], ["Find mug", "Pick mug", "MoveTo dining table@kitchen", "Place dining table@kitchen"]
    assert _status(make_assist, env, bad, gate_complete="1") == _status(make_assist, env, bad) == "failed"
    for plan in (bad, good):
        assert _status(make_assist, env, plan, precond_repair="1", gate_complete="1") == _status(make_assist, env, plan, precond_repair="1") == "success"


# end of plan (class: the plan stops before the step that achieves its goal) ------------------------------------------------------------
ACH = {"rules": {"Offer": {"holding": True, "near": True, "achieves": "hand"}, "Drop": {"holding": True, "achieves": "on"}}, "source": "made-up pack"}


def test_end_of_plan_hand_goal_gets_the_achieving_skill():
    """The plan fetched the object and walked to the person (a spot that is no ENV key) and stops: the pack's hand skill is appended."""
    goals = [{"type": "on", "object": "gizmo", "from": "shelf", "to": "spot near me", "hand": True}]
    opt, run, svc = make(needs=ACH, goals=goals)
    got = opt.on_plan(steps("Walk shelf@hall", "Scan gizmo", "Seize gizmo", "Walk spot near me"), run)
    assert acts(got)[-2:] == [("Walk", "spot near me"), ("Offer", "spot near me")] and len(got) == 5
    assert svc.labs[-1][1]["need"] == "achieves"


def test_end_of_plan_surface_goal_walks_then_puts_down():
    goals = [{"type": "on", "object": "toy", "from": "", "to": "bin"}]
    opt, run, _ = make(needs=ACH, goals=goals, hold="toy", at="shelf@hall")
    assert acts(opt.on_plan(steps("Walk shelf@hall"), run)) == [("Walk", "shelf@hall"), ("Walk", "bin@yard"), ("Drop", "bin@yard")]
    opt, run, _ = make(needs=ACH, goals=goals, hold="toy", at="bin@yard")
    assert acts(opt.on_plan(steps("Walk bin@yard"), run)) == [("Walk", "bin@yard"), ("Drop", "bin@yard")]


def test_end_of_plan_nothing_when_released_unheld_undeclared_or_foreign():
    goals = [{"type": "on", "object": "toy", "from": "", "to": "bin"}]
    opt, run, _ = make(needs=ACH, goals=goals, hold="toy", at="bin@yard")
    assert opt.on_plan(steps("Drop bin@yard"), run) is None                        # already put down
    opt, run, _ = make(needs=ACH, goals=goals, hold="other", at="bin@yard")
    assert opt.on_plan(steps("Walk bin@yard"), run) is None                        # holds an object no goal wants
    opt, run, _ = make(needs={"rules": {"Drop": {"holding": True}}, "source": "x"}, goals=goals, hold="toy", at="bin@yard")
    assert opt.on_plan(steps("Walk bin@yard", "Drop bin@yard"), run) is None       # no `achieves` declared: nothing
    opt, run, _ = make(needs={"rules": {"Offer": {"achieves": "hand"}}, "source": "x"}, goals=goals, hold="toy", at="bin@yard")
    assert opt.on_plan(steps("Walk bin@yard", "Offer ana@hall"), run) is None      # surface goal, only a hand skill declared
    assert rules_of({"rules": {"Drop": {"achieves": "bogus"}}, "assumed": True}) == {"Drop": {}}


def test_end_of_plan_two_goals_the_first_is_dropped_off_and_the_second_is_completed():
    """Shape of the failure class: goal 1 is put down by the plan, goal 2 is fetched and approached; only goal 2 lacks its last step, once."""
    goals = [{"type": "on", "object": "toy", "from": "shelf", "to": "bin"}, {"type": "on", "object": "gizmo", "from": "shelf", "to": "spot near me", "hand": True}]
    plan = steps("Walk shelf@hall", "Scan toy", "Seize toy", "Walk bin@yard", "Drop bin@yard", "Walk shelf@hall", "Scan gizmo", "Seize gizmo", "Walk spot near me")
    opt, run, _ = make(needs=ACH, goals=goals)
    got = opt.on_plan(plan, run)
    assert acts(got)[:-1] == acts(plan) and acts(got)[-1] == ("Offer", "spot near me")
    run2 = NS(world=run.world, places=run.places, goals=goals)
    assert opt.on_plan(got, run2) is None                                           # idempotent
