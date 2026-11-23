"""Option ``goal_ledger`` (default OFF): a ``go`` goal the goal filter (F4.4 ``drop_invented``) would drop is kept at the drop site when a heard word
starts with its place's name (restored silently) or, with ``named``, kept and named in the read-back; a destination the person took back gets the engine's
where-question (``retract``). Made-up houses (a nook in a den, aliases roost / kruvo), made-up objects (gizmo, widget), a made-up language ``zq``; nothing
here is a real robot's place or a benchmark text."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import goal_fix
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.core.planning.understand import find_issues
from robot_agent.core.safety import load_profile
from robot_agent.options.goal_ledger import SPEC, GoalLedger, cue, evidence, retracted

NOOK, CT, DT, DS, ST = "nook@den", "counter@kitchen", "dining table@kitchen", "desk@study room", "stool@bedroom"
HOUSE = {NOOK: {"aliases": ["roost"]}, "mat@hall": {}, "ledge@hall": {}, "vask@zon": {"aliases": ["kruvo"]},
         "pip@den": {"person": True}, DS: {}, ST: {}}
PLACES = Places(HOUSE)


def goal(typ, to, obj=""):
    return {"type": typ, "object": obj, "from": "", "to": to}


def make(lang="en", planner=None, **keys):
    cfg = resolve(SPEC, {"goal_ledger": "1", **{f"goal_ledger.{k}": v for k, v in keys.items()}}, {}, None)
    svc = NS(robot=NS(manifest=NS(planner=planner or {})), labs=[])
    svc.lab = lambda *a, **k: svc.labs.append((a, k))
    run = NS(places=PLACES, task="", dialogue=[], lang=lang)
    return GoalLedger(cfg, svc), run, svc


def filtered(opt, run, goals, text):
    return goal_fix.drop_invented([dict(g) for g in goals], text, PLACES, opt.goal_keep(run, goals))


# ── registry, default off, empty ──────────────────────────────────────────────────────────────────────────────────────
def test_registered_default_off_and_the_filter_drops_as_before_off(make_assist, env):
    assert "goal_ledger" in ASSIST_OPTIONS and REGISTRY.get("goal_ledger").key("enabled").default is False
    assert not resolve(SPEC, {}, {}, None)["enabled"]
    goals = [goal("on", "mat@hall", "gizmo"), goal("go", NOOK)]
    assert goal_fix.drop_invented([dict(g) for g in goals], "put the gizmo on the mat then go to the roostlo", PLACES) == (goals[:1], goals[1:])
    loop = make_assist(FakeRobot(env, {}), FakeLLM(default={"status": "clear", "goals": []}), ScriptedPlanner([]))
    assert loop.options.query("goal_keep", NS(), []) is None


def test_no_name_heard_no_marker_no_data_does_nothing_empty():
    opt, run, svc = make()
    goals = [goal("on", "mat@hall", "gizmo"), goal("go", "ledge@hall")]
    out, dropped = filtered(opt, run, goals, "put the gizmo on the mat and then go")        # ledge has no alias, nothing heard
    assert out == goals[:1] and dropped == goals[1:]                                       # no sign of the place: the reader's goal
    assert opt.on_issues([], run, out) is None and not svc.labs
    off, run2, _ = make(restore=False)
    assert off.goal_keep(run2, goals) is None                                              # restore and named both off: no predicate
    r4, run3, _ = make(retract=True)
    run3.task = "put the gizmo on the mat"
    assert r4.on_issues([], run3, [goal("on", "mat@hall", "gizmo")]) is None                 # no marker: nothing added


# ── R1 restore ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_restore_a_heard_word_starting_with_the_place_name_keeps_the_go_goal_in_its_place():
    opt, run, svc = make()
    last = [goal("on", "mat@hall", "gizmo"), goal("go", NOOK)]
    out, dropped = filtered(opt, run, last, "put the gizmo on the mat, then go back to roostlo")
    assert out == last and not dropped
    first = [goal("go", NOOK), goal("on", "mat@hall", "gizmo")]
    out, _ = filtered(opt, run, first, "go to roostlo first, then put the gizmo on the mat")
    assert out == first                                                                     # the position is kept (first and last)
    assert [k for _a, k in svc.labs] == [{"rule": "restore", "to": NOOK, "heard": "roostlo"}] * 2


def test_restore_short_alias_decoys_inside_a_word_or_a_small_share_are_no_evidence():
    assert evidence(NOOK, "go to the roostx", PLACES, 0.5) == "roostx"
    assert evidence(NOOK, "go to the reroost", PLACES, 0.5) == ""                           # inside a word, not at its start
    assert evidence(NOOK, "go to the roosterhouse", PLACES, 0.5) == ""                      # the name is under half of the word
    assert evidence(NOOK, "go to the nookz", PLACES, 0.5) == "nookz"                         # the head counts as a name too
    opt, run, _ = make()
    out, dropped = filtered(opt, run, [goal("on", "mat@hall", "gizmo"), goal("go", NOOK)], "put the gizmo on the mat, the roosterhouse")
    assert len(out) == 1 and len(dropped) == 1


def test_restore_settles_the_unsaid_issue_and_the_heard_words_object_issue():
    opt, run, _ = make()
    goals = [goal("on", "mat@hall", "gizmo"), goal("go", NOOK)]
    out, _ = filtered(opt, run, goals, "put the gizmo on the mat, then go back to roostlo")
    issues = find_issues(out, "put the gizmo on the mat, then go back to roostlo", PLACES)
    assert {"kind": "unsaid", "what": "nook", "object": ""} in issues
    obj = {"kind": "object", "what": "roostlo", "near": "roost"}
    assert opt.on_issues(issues + [obj], run, out) == [i for i in issues if i["kind"] != "unsaid"]


def test_restore_in_a_made_up_language_whole_script_aware():
    opt, run, svc = make(lang="zq")
    goals = [goal("on", "mat@hall", "gizmo"), goal("go", "vask@zon")]
    out, _ = filtered(opt, run, goals, "ka gizmo mat lu, kruvomi zu")
    assert out == goals and svc.labs[0][1]["heard"] == "kruvomi"


def test_restore_never_brings_back_a_go_goal_dropped_by_go_steps():
    """go_steps (after the drop site) drops a go to a person as a step; a kept goal still meets it."""
    opt, run, _ = make(named=True)
    goals = [goal("go", "pip@den"), goal("on", "mat@hall", "gizmo")]
    out, _ = filtered(opt, run, goals, "put the gizmo on the mat")
    out, gone = goal_fix.go_steps(out, "put the gizmo on the mat", PLACES)
    assert out == goals[1:] and gone == goals[:1]


# ── R1 named ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_named_no_evidence_keeps_the_goal_and_its_unsaid_issue_stays_for_the_read_back():
    opt, run, svc = make(named=True)
    goals = [goal("on", "mat@hall", "gizmo"), goal("go", NOOK)]
    out, dropped = filtered(opt, run, goals, "put the gizmo on the mat, then head back to your")
    assert out == goals and not dropped
    issues = find_issues(out, "put the gizmo on the mat, then head back to your", PLACES)
    assert opt.on_issues(issues, run, out) is None and {"kind": "unsaid", "what": "nook", "object": ""} in issues
    assert [k["rule"] for _a, k in svc.labs] == ["named"]


# ── R4 retract ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_retract_a_destination_taken_back_gets_the_engines_where_issue():
    gs = [goal("on", "mat@hall", "gizmo")]
    want = [{"kind": "unsaid", "what": "mat", "object": "gizmo"}]
    corr = __import__("re").compile(r"\bno wait\b")
    assert retracted(gs, "put the gizmo on the mat, no wait, on the uh", PLACES, corr) == want
    assert retracted(gs, "put the gizmo on the mat, no wait.", PLACES, corr) == []                 # nothing follows the marker
    two = gs + [goal("on", "ledge@hall", "widget")]
    assert retracted(two, "put the gizmo on the mat, no wait, the widget on the ledge", PLACES, corr) == []   # another goal began
    assert retracted(gs, "put the gizmo on the mat, no wait, on the mat", PLACES, corr) == []       # said again after it


def test_retract_polite_marker_decoy_and_the_option_narrower_markers():
    opt, run, svc = make(retract=True)
    run.task = "put the gizmo on the mat, sorry about the mess"
    gs = [goal("on", "mat@hall", "gizmo")]
    assert opt.on_issues([], run, gs)                                     # the pack's markers read "sorry" as a correction
    narrow, run2, _ = make(retract=True, planner={"options": {"goal_ledger": {"retract_markers": {"en": [r"\bno wait\b"], "assumed": True}}}})
    run2.task = run.task
    assert narrow.on_issues([], run2, gs) is None                         # the option's narrower list: no retraction
    zq, run3, _ = make(lang="zq", retract=True, planner={"options": {"goal_ledger": {"retract_markers": {"zq": [r"\bnu vek\b"], "assumed": True}}}})
    run3.task = "ka gizmo mat lu, nu vek, ledgo"
    assert zq.on_issues([], run3, gs) == [{"kind": "unsaid", "what": "mat", "object": "gizmo"}]


def test_retract_unlabelled_markers_of_the_option_are_not_used_empty():
    """``retract_markers`` with neither ``source`` nor ``assumed: true`` is not used (fail open): the made-up language's words add nothing."""
    gs = [goal("on", "mat@hall", "gizmo")]
    bare, run, svc = make(lang="zq", retract=True, planner={"options": {"goal_ledger": {"retract_markers": {"zq": [r"\bnu vek\b"]}}}})
    run.task = "ka gizmo mat lu, nu vek, ledgo"
    assert bare.on_issues([], run, gs) is None and not svc.labs
    sourced, run2, _ = make(lang="zq", retract=True, planner={"options": {"goal_ledger": {"retract_markers": {"zq": [r"\bnu vek\b"], "source": "a made-up pack"}}}})
    run2.task = run.task
    assert sourced.on_issues([], run2, gs) == [{"kind": "unsaid", "what": "mat", "object": "gizmo"}]


def test_open_only_a_drop_with_a_weak_cue_of_its_place_is_logged_open():
    """Regression shape: every go goal F4.4 dropped with no evidence was logged open, most of them goals the reader invented. Only a weak
    cue of the place (a cut final word, a tail too long) is."""
    assert cue(NOOK, "put the gizmo on the mat then go to the roo", PLACES, 0.5) == "roo"            # the alias cut short (3 of 5)
    assert cue(NOOK, "put the gizmo on the mat then go to the nookaborrel", PLACES, 0.5) == "nookaborrel"   # a tail evidence refuses
    two = Places({"fern stand@hall": {}})                                                          # a name of two words
    assert cue("fern stand@hall", "put the gizmo on the mat then go to the fern sta", two, 0.5) == "fern sta"
    assert cue("fern stand@hall", "put the gizmo on the mat then go to the sta", two, 0.5) == ""      # its first word is not said
    for text in ("put the gizmo on the mat then go to the r", "put the gizmo on the roo and then rest", "put the gizmo on the mat then go"):
        assert cue(NOOK, text, PLACES, 0.5) == "", text                                              # too short, not the last word, nothing
    goals = [goal("on", "mat@hall", "gizmo"), goal("go", NOOK)]
    for text, open_ in (("put the gizmo on the mat then go to the roo", goals[1:]), ("put the gizmo on the mat then rest", None)):
        opt, run, svc = make()
        out, dropped = filtered(opt, run, goals, text)
        assert out == goals[:1] and dropped == goals[1:], text
        assert [k for _a, k in svc.labs] == ([{"rule": "open", "to": NOOK, "heard": "roo"}] if open_ else [])


# ── through the real loop ────────────────────────────────────────────────────────────────────────────────────────────
def _house(env):
    env[NOOK] = {**env[ST], "aliases": ["roost"], "loc": {"x": 3.1, "y": 2.2, "rz": 0}}
    return env


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": [dict(g) for g in goals]}


GIZMO, GO = goal("on", DT, "gizmo"), goal("go", NOOK)
PLAN = steps(f"MoveTo {CT}", "Find gizmo", "Pick gizmo", f"MoveTo {DT}", f"Place {DT}", f"MoveTo {NOOK}")


def loop_run(make_assist, env, task, llm, plan=PLAN, answers=(), **opts):
    robot = FakeRobot(_house(env), {"gizmo": CT}, answers=answers)
    loop = make_assist(robot, llm, ScriptedPlanner(plan), detector_miss_rate=0, ask_help=False, **opts)
    evs = run_task(loop, task, max_events=3000)
    und = [e for e in evs if e["event"] == "understand"]
    return robot, loop, evs, und[-1]["goals"] if und else None


TASK = "Put the gizmo on the dining table, then go back to the roostlo."


def test_restore_loop_final_the_robot_ends_in_the_nook_with_no_question(make_assist, env):
    robot, loop, evs, goals = loop_run(make_assist, env, TASK, FakeLLM(default=understood(GIZMO, GO)), goal_ledger="1")
    assert goals == [GIZMO, GO] and robot.asked == []
    assert done(evs)["status"] == "success" and robot.at == NOOK and robot.obj["gizmo"] == DT


def test_no_did_you_mean_loop_final_off_asks_about_the_garbled_word_on_asks_nothing(make_assist, env):
    robot, _l, evs, goals = loop_run(make_assist, env, TASK, FakeLLM(default=understood(GIZMO, GO)))
    assert goals == [GIZMO] and robot.asked and robot.asked[0].startswith("Did you mean")           # the engine's own object check, off
    robot, loop, evs, _g = loop_run(make_assist, env, TASK, FakeLLM(default=understood(GIZMO, GO)), goal_ledger="1")
    assert not [e for e in evs if e["event"] == "clarify"] and robot.asked == []                    # no yes / no question about a guess
    assert not any("mean" in str(q) for q in robot.asked) and done(evs)["status"] == "success"


def test_named_loop_final_the_read_back_names_the_nook_with_the_doubt_and_a_yes_reaches_it(make_assist, env):
    task = "Put the gizmo on the dining table, then head back to your"
    robot, loop, evs, goals = loop_run(make_assist, env, task, FakeLLM(default=understood(GIZMO, GO)), goal_ledger="1", goal_ledger_named="1")
    asks = [e for e in evs if e["event"] == "clarify"]
    assert len(asks) == 1 and asks[0]["kind"] == "confirm" and "nook" in str(asks[0]["question"]).lower()
    assert "not sure" in str(asks[0]["question"]).lower() and "mean" not in str(asks[0]["question"]).lower()
    assert done(evs)["status"] == "success" and robot.at == NOOK and goals == [GIZMO, GO]


def test_restore_loop_final_a_re_sample_for_another_issue_keeps_the_go_goal(make_assist, env):
    """The first reading misses the widget (an object issue): two re-samples agree on a set that holds the kept go goal."""
    task = "Put the gizmo and the widget on the dining table, then go back to the roostlo."
    widget = goal("on", DT, "widget")
    plan = PLAN[:-1] + steps(f"MoveTo {CT}", "Find widget", "Pick widget", f"MoveTo {DT}", f"Place {DT}", f"MoveTo {NOOK}")

    def go(**opts):
        robot = FakeRobot(_house(env), {"gizmo": CT, "widget": CT})
        llm = FakeLLM(understood(GIZMO, GO), default=understood(GIZMO, widget, GO))
        loop = make_assist(robot, llm, ScriptedPlanner(plan), detector_miss_rate=0, ask_help=False, **opts)
        evs = run_task(loop, task, max_events=3000)
        return robot, evs, [e for e in evs if e["event"] == "understand_check" and "samples" in e]
    robot, evs, checks = go(goal_ledger="1")
    assert checks and checks[0]["agreed"] and all(GO in s["goals"] for s in checks[0]["samples"])
    assert done(evs)["status"] == "success" and robot.at == NOOK and robot.obj["gizmo"] == DT and robot.obj["widget"] == DT
    _r, _e, checks = go()
    assert checks and all(GO not in s["goals"] for s in checks[0]["samples"])                       # off: the samples drop it again


PROFILE = """\
profile: made_up_ledger
version: 1
checks: ['go']
rules:
- id: no_nook
  objects: ['.*']
  places: ['nook']
  say: the nook is closed to robots
  provenance: assumed
"""


def test_safety_gate_loop_final_a_restored_go_goal_meets_the_hazard_gate_and_is_refused(make_assist, env, tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(PROFILE, encoding="utf-8")
    prof, errs = load_profile(f)
    assert not errs
    robot = FakeRobot(_house(env), {"gizmo": CT})
    loop = make_assist(robot, FakeLLM(default=understood(GIZMO, GO)), ScriptedPlanner(PLAN[:-1]), detector_miss_rate=0, ask_help=False, goal_ledger="1")
    loop.robot.manifest.safety_profile = prof
    evs = run_task(loop, TASK, max_events=3000)
    assert [e["rule"] for e in loop.lab_log if e["flag"] == "goal_ledger"] == ["restore"]
    assert [e for e in loop.lab_log if e["flag"] == "hazard"]                                       # the gate saw the kept goal
    d = done(evs)
    assert d["status"] == "success" and robot.at != NOOK and robot.obj["gizmo"] == DT
    assert "nook is closed" in str(d.get("say")) and d.get("refusals")


def test_restore_loop_final_cond_goal_still_decides_a_kept_go_goal_with_a_condition(make_assist, env):
    when = {"object": "mug", "at": "desk", "present": True}
    task = "Put the gizmo on the dining table. If the mug is on the desk, go back to the roostlo."
    robot = FakeRobot(_house(env), {"gizmo": CT, "mug": ST})
    opts = {"goal_ledger": "1", "cond_goal": "1", "cond_goal.prompt": "A condition is a goal's when.", "cond_goal.assumed": "1"}
    loop = make_assist(robot, FakeLLM(default=understood(GIZMO, {**GO, "when": when})), ScriptedPlanner(PLAN[:-1]), detector_miss_rate=0,
                       ask_help=False, **opts)
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    evs = run_task(loop, task, max_events=3000)
    assert [e["rule"] for e in loop.lab_log if e["flag"] == "goal_ledger"][:1] == ["restore"]
    assert [e for e in loop.lab_log if e["flag"] == "cond_goal"]                                    # the condition was looked at
    assert done(evs)["status"] == "success" and robot.at != NOOK and robot.obj["gizmo"] == DT     # the mug is not on the desk: no go


def test_retract_loop_final_the_where_question_is_asked_and_the_answer_places_the_gizmo(make_assist, env):
    task = "Put the gizmo on the desk, no wait, on the uh."
    on_desk = understood(goal("on", DS, "gizmo"))
    llm = FakeLLM((r"table please", understood(GIZMO)), default=on_desk)
    plan = PLAN[:-1]
    robot, loop, evs, goals = loop_run(make_assist, env, task, llm, plan, answers=["the dining table please"], goal_ledger="1",
                                       **{"goal_ledger.retract": "1", "goal_ledger.restore": "0"})
    assert robot.asked and robot.asked[0] == "Where should I put the gizmo?"
    assert goals == [GIZMO] and done(evs)["status"] == "success" and robot.obj["gizmo"] == DT
    robot, loop, evs, goals = loop_run(make_assist, env, task, FakeLLM(default=on_desk), plan)
    assert [(g["object"], g["to"]) for g in goals] == [("gizmo", DS)] and not robot.asked            # off: the taken-back desk is kept, no question


def test_named_loop_final_a_correction_that_removes_the_go_goal_is_not_undone(make_assist, env):
    task = "Put the gizmo on the dining table, then head back to your"
    llm = FakeLLM((r"just the gizmo", understood(GIZMO)), default=understood(GIZMO, GO))
    robot, loop, evs, goals = loop_run(make_assist, env, task, llm, PLAN[:-1], answers=["no, just the gizmo"], goal_ledger="1",
                                       **{"goal_ledger.named": "1"})
    assert "nook" in robot.asked[0].lower()                                  # the first read-back named the kept goal; the person removed it
    assert goals == [GIZMO] and done(evs)["status"] == "success" and robot.at != NOOK and robot.obj["gizmo"] == DT
