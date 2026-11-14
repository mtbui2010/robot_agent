"""Option ``replan_guard`` (default OFF): the planner key for the failed block, sub-goals rebuilt after a goal change, declared critical findings of a
replan's suffix through the one refine. Hook-level tests on stubs and made-up worlds, plus loop-level runs of the default path and a goal change."""
from types import SimpleNamespace as NS

import pytest

from embodiplan.action_model import Capabilities, SymbolicState
from fakes import FakeLLM, FakeRobot, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.core.planning.backends.pyplanner_grace import _GRACE_KWARGS, GraceBackend
from robot_agent.interact.run_manager import reset_manager
from robot_agent.options.replan_guard import SPEC, ReplanGuard, codes_of

ENV = {"vat@yard": {}, "rack@yard": {}, "pen@yard": {"openable": True}}
CODES = {"codes": ["place_without_holding", "place_wrong_location"], "source": "made-up pack"}


def make(overrides=None, critical=CODES, goals=(), holding=None):
    labs = []
    world = SymbolicState(holding=holding)
    caps = Capabilities()
    robot = NS(model_capabilities=lambda: caps, plan_actions=["MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close"],
               manifest=NS(planner={"options": {"replan_guard": {"critical": critical}}} if critical is not None else {}))
    svc = NS(robot=robot, lab=lambda *a, **k: labs.append((a, k)), subgoals=lambda run: ["carry the gem to the vat"] if run.goals else None,
             replan_round=lambda run: (len(run.recorded), run.left))
    cfg = resolve(SPEC, {"replan_guard": "1", **(overrides or {})}, {}, robot.manifest.planner)
    run = NS(world=world, places=Places(ENV), goals=list(goals), goal_txt="gem to vat", restated="old words", recorded=[], left=1)
    return ReplanGuard(cfg, svc), run, labs


GOAL = {"type": "on", "object": "gem", "from": "rack@yard", "to": "vat@yard"}


# ── off and empty ──
def test_replan_guard_registered_and_off_by_default(make_assist, env):
    assert "replan_guard" in ASSIST_OPTIONS
    loop = make_assist(FakeRobot(env, {}), FakeLLM(default={}), NS(generate_plan=None))
    assert loop.options.transform("on_planner_kwargs", {}) == {}
    assert loop.options.decide("on_goal_replan", NS(goals=[GOAL])) is None
    assert loop.options.transform("on_replan_findings", ["x"], NS(), []) == ["x"]
    plan = steps("Find gem", "Pick gem", "MoveTo pen@yard", "PutIn pen@yard", "PutIn pen@yard")
    assert loop.options.transform("on_plan", plan, NS(world=SymbolicState(), places=Places(ENV))) == plan      # off: a duplicate release is kept


def test_replan_guard_off_a_goal_change_gives_the_planner_no_sub_goals(make_assist, env):
    planner = Recording()
    start_run()
    evs = run_task(make_assist(robot_for(env), FakeLLM(APPLE), planner), TASK)
    assert done(evs)["status"] == "success"
    assert [kw for c, kw in planner.replans] == [None]
    reset_manager()


def test_replan_guard_empty_pack_adds_no_findings_and_a_goal_less_run_no_sub_goals():
    opt, run, labs = make(critical=None, goals=[GOAL])
    assert opt.on_replan_findings([], run, steps("Place vat@yard")) is None
    opt, run, _ = make(goals=[])
    assert opt.on_goal_replan(run) is None
    opt, run, _ = make(critical={"codes": ["place_without_holding"]})            # no label = no data
    assert codes_of({"codes": ["place_without_holding"]}) == () and opt.on_replan_findings([], run, steps("Place vat@yard")) is None
    assert codes_of({"codes": "place_without_holding", "assumed": True}) == () and codes_of(None) == ()


# ── part: block_fix (the planner key) ──
def test_planner_key_is_added_and_passes_the_backend_whitelist():
    opt, _run, _ = make()
    assert opt.on_planner_kwargs({"safe_refine": True}) == {"safe_refine": True, "replan_block_fix": True}
    assert make({"replan_guard.block_fix": "0"})[0].on_planner_kwargs({}) is None
    assert "replan_block_fix" in _GRACE_KWARGS


def test_the_planner_is_built_with_the_key_when_the_option_is_on(make_assist, env, monkeypatch):
    import robot_agent.core.planning.loop as LP
    got = []
    monkeypatch.setattr(LP, "get_planner", lambda name, **kw: got.append(kw) or NS())
    for on in (False, True):
        loop = make_assist(FakeRobot(env, {}), FakeLLM(default={}), NS(), **({"replan_guard": "1"} if on else {}))
        loop.cfg["planner"] = "grace"
        loop._make_planner = type(loop)._make_planner.__get__(loop)
        loop._make_planner()
    assert "replan_block_fix" not in got[0] and got[1]["replan_block_fix"] is True


def test_the_backend_hands_the_key_to_the_planner_and_forwards_sub_goals():
    b = GraceBackend(host="http://127.0.0.1:1", model="x", replan_block_fix=True)
    assert b._p.replan_block_fix is True
    assert GraceBackend(host="http://127.0.0.1:1", model="x")._p.replan_block_fix is False
    seen = {}

    class P:
        def replan(self, task, completed, failed, reason, obs, visible, budget=None, subgoals=None):
            seen["sg"] = subgoals
            return [], NS(llm_calls=1)
    GraceBackend.wrap(P()).replan("t", [], {}, "r", "", [], subgoals=["a b"])
    assert seen["sg"] == ["a b"]


# ── part: goal_rebuild ──
def test_goal_change_hook_rebuilds_sub_goals_and_the_task_text_from_the_new_goals():
    opt, run, labs = make(goals=[GOAL])
    assert opt.on_goal_replan(run) == ["carry the gem to the vat"] and run.restated == "gem to vat"
    assert labs and labs[0][1]["part"] == "rebuild"


def test_goal_change_hook_does_nothing_when_the_planner_decomposes_itself_or_the_part_is_off():
    opt, run, _ = make(goals=[GOAL])
    opt.svc.subgoals = lambda r: None
    assert opt.on_goal_replan(run) is None and run.restated == "old words"
    opt, run, _ = make({"replan_guard.goal_rebuild": "0"}, goals=[GOAL])
    assert opt.on_goal_replan(run) is None and run.restated == "old words"


def test_a_goal_change_mid_run_replans_for_the_new_goals_and_is_not_repeated(make_assist, env, tmp_path):
    reset_manager(); run_control._CANCEL.clear()
    run = run_control.manager().start(TASK, user_id="alice", role="member").run
    run_control.begin_run()
    planner = Recording()
    robot = SayAfter(env, HOUSE, say=[("move", "actually, put it on the counter instead", "member")])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner, replan_guard="1"), TASK)
    assert done(evs)["status"] == "success"
    first = planner.replans[0][1]
    assert first and any("counter" in s for s in first)                  # the sub-goals come from the NEW goals
    assert run.pending == []
    reset_manager(); run_control._CANCEL.clear()


# ── part: critical_refine ──
def test_a_release_with_an_empty_hand_is_a_finding_and_a_release_when_holding_is_not():
    opt, run, labs = make(goals=[GOAL])
    got = opt.on_replan_findings([], run, steps("MoveTo vat@yard", "Place vat@yard"))
    assert got and "Place" in got[0] and labs[0][1]["part"] == "critical"
    opt, run, _ = make(goals=[GOAL], holding="gem")
    assert opt.on_replan_findings([], run, steps("MoveTo vat@yard", "Place vat@yard")) is None


def test_engine_findings_are_kept_first_and_an_undeclared_code_is_not_added():
    opt, run, _ = make(goals=[GOAL])
    got = opt.on_replan_findings(["step 9 'X': no"], run, steps("Place vat@yard"))
    assert got[0] == "step 9 'X': no" and len(got) == 3
    opt, run, _ = make({}, critical={"codes": ["already_open"], "assumed": True}, goals=[GOAL])
    assert opt.on_replan_findings([], run, steps("Place vat@yard")) is None


def test_the_part_off_or_an_empty_suffix_adds_nothing():
    opt, run, _ = make({"replan_guard.critical_refine": "0"}, goals=[GOAL])
    assert opt.on_replan_findings([], run, steps("Place vat@yard")) is None
    opt, run, _ = make(goals=[GOAL])
    assert opt.on_replan_findings([], run, []) is None


# ── loop level: a replan suffix with a declared critical finding goes to the one refine (guarded_run off: the old end) ──
def test_a_critical_suffix_is_refined_once_then_the_run_ends_uncertified_or_runs_clean(make_assist, env):
    from fakes import ScriptedPlanner
    HOLD = {"apple": DS}
    bad = steps(f"MoveTo {DT}", f"Place {DT}")                                   # a release with an empty hand
    good = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    for repl, status in (([bad, good], "success"), ([bad, bad], "failed")):
        robot = FakeRobot(env, HOLD, faults=[{"skill": "find", "obj": "apple", "nth": 1, "kind": "fail"}])
        planner = ScriptedPlanner(PLAN, replans=repl)
        evs = run_task(make_assist(robot, FakeLLM(APPLE), planner, replan_guard="1",
                                   **{"replan_guard.critical": CRIT, "replan_guard.guarded_run": "0"}), TASK)
        assert done(evs)["status"] == status, [e["event"] for e in evs]
        assert len([c for c in planner.calls if c[0] == "replan"]) == 2
        assert "rejected" in planner.calls[2][2] and "requires holding" in planner.calls[2][2]     # the one refine is told the finding
    robot = FakeRobot(env, HOLD, faults=[{"skill": "find", "obj": "apple", "nth": 1, "kind": "fail"}])
    planner = ScriptedPlanner(PLAN, replans=[bad, good])                         # off: the first suffix runs as written, no refine round
    run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK)
    assert not any("rejected" in c[2] for c in planner.calls if c[0] == "replan")


# ── helpers (loop level) ──
DS, DT, CT = "desk@study room", "dining table@kitchen", "counter@kitchen"
HOUSE = {"apple": DS, "mug": DS}
TASK = "Bring the apple from the desk to the dining table."
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
APPLE = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
CRIT = {"codes": ["place_without_holding"], "assumed": True}


class Recording:
    """A planner double whose ``replan`` takes ``subgoals`` and records it."""

    def __init__(self):
        self.replans = []

    def generate_plan(self, task, obs, visible_objects):
        return [dict(s) for s in PLAN], {"method": "recording"}

    def replan(self, task, completed, failed_step, reason, obs, visible_objects, subgoals=None):
        self.replans.append((failed_step, subgoals))
        return steps("Find apple", "Pick apple", f"MoveTo {CT}", f"Place {CT}"), {"method": "recording"}


class SayAfter(FakeRobot):
    def __init__(self, *a, say=(), **k):
        super().__init__(*a, **k)
        self.say = list(say)

    def execute(self, skill, params, node=None, log_fn=None):
        out = super().execute(skill, params, node, log_fn)
        if self.say and self.say[0][0] == skill:
            _s, text, role = self.say.pop(0)
            run_control.manager().inject(run_control.current_run().run_id, text, role=role)
        return out


def robot_for(env):
    return SayAfter(env, HOUSE, say=[("move", "actually, put it on the counter instead", "member")])


def start_run():
    reset_manager(); run_control._CANCEL.clear()
    r = run_control.manager().start(TASK, user_id="alice", role="member").run
    run_control.begin_run()
    return r


# ── part: guarded_run (a finding the run-time check refuses itself steers the one refine, never ends the run) ──
WRONG = steps("MoveTo rack@yard", "Find gem", "Pick gem", "Place vat@yard")         # the release away from its place: the guard refuses it before it moves


def test_a_guarded_finding_steers_the_first_call_and_is_left_to_the_run_time_check_in_the_refine():
    opt, run, labs = make(goals=[GOAL])
    first = opt.on_replan_findings([], run, WRONG)
    assert first and "Place" in first[0]                                            # the suffix: the refine is told
    assert opt.on_replan_findings([], run, WRONG) is None                           # the refine of the same replan: the suffix runs
    assert [k["part"] for _a, k in labs] == ["critical", "guarded"]
    run.recorded.append("one replan done")
    assert opt.on_replan_findings([], run, WRONG)                                   # the next replan steers again


def test_a_guarded_finding_is_not_added_when_no_refine_is_left_and_the_part_off_keeps_the_old_end():
    opt, run, labs = make(goals=[GOAL])
    run.left = 0
    assert opt.on_replan_findings(["step 9 'X': no"], run, WRONG) is None and labs[-1][1]["part"] == "guarded"
    opt, run, _ = make({"replan_guard.guarded_run": "0"}, goals=[GOAL])
    assert opt.on_replan_findings([], run, WRONG) and opt.on_replan_findings([], run, WRONG)     # off: both calls carry it (the run ends uncertified)


def test_a_finding_the_run_time_check_cannot_catch_still_counts_in_the_refine():
    crit = {"codes": ["find_wrong_place", "place_wrong_location"], "assumed": True}
    opt, run, _ = make(critical=crit, goals=[GOAL])                                 # the gem is believed at the rack; the suffix looks at the pen
    plan = steps("MoveTo pen@yard", "Find gem")
    assert opt.on_replan_findings([], run, plan) and opt.on_replan_findings([], run, plan) == [opt.on_replan_findings([], run, plan)[0]]


# ── part: release_dedupe ──
def test_an_empty_hand_release_repeating_the_last_release_is_dropped():
    opt, run, labs = make()
    plan = steps("Find gem", "Pick gem", "MoveTo pen@yard", "PutIn pen@yard", "PutIn pen@yard")
    assert opt.on_plan(plan, run) == plan[:4] and labs[-1][1] == {"part": "dedupe", "dropped": [4]}
    plan = steps("Find gem", "Pick gem", "MoveTo pen@yard", "PutIn pen@yard", "Close pen@yard", "PutIn pen@yard", "MoveTo vat@yard")
    assert opt.on_plan(plan, run) == plan[:5] + plan[6:]                            # a step between that fills no hand: still the same release


def test_release_dedupe_empty_plan_or_no_repeat_changes_nothing():
    opt, run, labs = make()
    assert opt.on_plan([], run) is None and opt.on_plan(steps("MoveTo vat@yard"), NS(world=None)) is None
    again = steps("Find gem", "Pick gem", "MoveTo pen@yard", "PutIn pen@yard", "Find bolt", "Pick bolt", "PutIn pen@yard")
    assert opt.on_plan(again, run) is None                                          # the hand was filled again: a real second release
    other = steps("Find gem", "Pick gem", "MoveTo vat@yard", "Place vat@yard", "Place rack@yard")
    assert opt.on_plan(other, run) is None and not labs                             # another place: not a repeat (the run-time check decides)
    opt, run, _ = make({"replan_guard.release_dedupe": "0"})
    assert opt.on_plan(steps("Find gem", "Pick gem", "MoveTo pen@yard", "PutIn pen@yard", "PutIn pen@yard"), run) is None


# ── loop level (regressions of the class: a suffix the run-time check would have caught ended the run) ──
SPOOL = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "spool", "from": "shelf", "to": "side table"}]}
SH, ST, WA, SI = "shelf@kitchen", "stool@bedroom", "washer@laundry room", "side table@living room"
SPLAN = steps(f"MoveTo {SH}", "Find spool", "Pick spool", f"MoveTo {SI}", f"Place {SI}")
AWAY = steps(f"MoveTo {ST}", "Find spool", "Pick spool", f"Place {SI}")             # no MoveTo before the release
CRIT2 = {"codes": ["place_without_holding", "place_wrong_location"], "assumed": True}


def _spool_run(make_assist, env, at, replans, **opts):
    from fakes import ScriptedPlanner
    robot = FakeRobot(env, {"spool": at})
    planner = ScriptedPlanner(SPLAN, replans=replans)
    loop = make_assist(robot, FakeLLM(SPOOL), planner, detector_miss_rate=0, **opts)
    return loop, robot, planner, done(run_task(loop, "Put the spool on the side table.", max_events=3000))


def test_a_wrong_place_release_after_a_find_of_an_absent_object_runs_and_a_normal_replan_follows(make_assist, env):
    good = steps(f"MoveTo {WA}", "Find spool", "Pick spool", f"MoveTo {SI}", f"Place {SI}")
    loop, robot, planner, d = _spool_run(make_assist, env, WA, [AWAY, AWAY, good], replan_guard="1", **{"replan_guard.critical": CRIT2})
    assert d["status"] == "success", d
    calls = [c for c in planner.calls if c[0] == "replan"]
    assert len(calls) == 3 and "rejected" in calls[1][2] and "Find" in calls[2][1]["action"]       # the refine was steered; the next replan is the Find's
    assert [e for e in loop.lab_log if e.get("part") == "guarded"]
    assert all(ok for sk, _o, ok, _m in robot.trace if sk == "place")


def test_guarded_run_off_the_same_suffix_ends_the_run_uncertified_as_before(make_assist, env):
    good = steps(f"MoveTo {WA}", "Find spool", "Pick spool", f"MoveTo {SI}", f"Place {SI}")
    _l, robot, planner, d = _spool_run(make_assist, env, WA, [AWAY, AWAY, good], replan_guard="1",
                                       **{"replan_guard.critical": CRIT2, "replan_guard.guarded_run": "0"})
    assert d["status"] == "failed" and "did not pass my checks" in d["msg"]
    assert len([c for c in planner.calls if c[0] == "replan"]) == 2 and [sk for sk, *_r in robot.trace] == ["move", "find"]


def test_a_wrong_place_release_reached_sends_no_release_to_the_robot_and_one_replan_follows(make_assist, env):
    back = steps(f"MoveTo {SI}", f"Place {SI}")
    loop, robot, planner, d = _spool_run(make_assist, env, ST, [AWAY, AWAY, back], replan_guard="1", **{"replan_guard.critical": CRIT2})
    assert d["status"] == "success", d
    calls = [c for c in planner.calls if c[0] == "replan"]
    assert len(calls) == 3 and "precondition" in calls[2][2] and calls[2][1]["action"] == "Place"
    assert [(sk, o, ok) for sk, o, ok, _m in robot.trace if sk == "place"] == [("place", SI, True)]


def test_the_declared_near_need_gives_the_release_its_move_and_no_refine_is_needed(make_assist, env):
    from fakes import ScriptedPlanner
    robot = FakeRobot(env, {"spool": ST})
    planner = ScriptedPlanner(SPLAN, replans=[AWAY])
    loop = make_assist(robot, FakeLLM(SPOOL), planner, detector_miss_rate=0, replan_guard="1", precond_repair="1", **{"replan_guard.critical": CRIT2})
    pl = loop.robot.manifest.planner
    pl["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    pl["options"] = {"precond_repair": {"needs": {"rules": {"Place": {"holding": True, "near": True}}, "source": "made-up pack: place needs arrived"}}}
    d = done(run_task(loop, "Put the spool on the side table.", max_events=3000))
    assert d["status"] == "success", d
    assert len([c for c in planner.calls if c[0] == "replan"]) == 1                # the suffix was repaired before any refine
    assert [e for e in loop.lab_log if e.get("flag") == "precond_repair" and e.get("need") == "near"]
    assert not [e for e in loop.lab_log if e.get("flag") == "replan_guard" and e.get("part") == "critical"]


TWO = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "spool", "from": "shelf", "to": "side table"},
                                                                    {"type": "on", "object": "cork", "from": "stool", "to": "sofa"}]}
SO = "sofa@living room"
CORK = [f"MoveTo {ST}", "Find cork", "Pick cork", f"MoveTo {SO}", f"Place {SO}"]


def _two_run(make_assist, env, dedupe):
    from fakes import ScriptedPlanner
    twice = steps(f"MoveTo {WA}", "Find spool", "Pick spool", f"MoveTo {SI}", f"Place {SI}", f"Place {SI}", *CORK)
    robot = FakeRobot(env, {"spool": WA, "cork": ST})
    planner = ScriptedPlanner(steps(*[f"MoveTo {SH}", "Find spool", "Pick spool", f"MoveTo {SI}", f"Place {SI}"], *CORK), replans=[twice, twice])
    loop = make_assist(robot, FakeLLM(TWO), planner, detector_miss_rate=0, replan_guard="1",
                       **{"replan_guard.critical": CRIT2, "replan_guard.guarded_run": "0", "replan_guard.release_dedupe": dedupe})
    return loop, robot, planner, done(run_task(loop, "Put the spool on the side table and the cork on the sofa.", max_events=3000))


def test_a_replan_suffix_with_a_repeated_empty_hand_release_runs_without_it_and_without_a_refine(make_assist, env):
    loop, robot, planner, d = _two_run(make_assist, env, "1")
    assert d["status"] == "success", d
    assert len([c for c in planner.calls if c[0] == "replan"]) == 1
    assert [e for e in loop.lab_log if e.get("part") == "dedupe"] and [sk for sk, *_r in robot.trace].count("place") == 2


def test_release_dedupe_off_the_repeated_release_is_a_critical_finding_as_before(make_assist, env):
    loop, robot, planner, d = _two_run(make_assist, env, "0")
    assert d["status"] == "failed" and "did not pass my checks" in d["msg"]
    assert len([c for c in planner.calls if c[0] == "replan"]) == 2 and not [e for e in loop.lab_log if e.get("part") == "dedupe"]
