"""Option ``cond_goal``, the keys of layer cond_answer (each default OFF): ``answer`` (the answer to "is the X at P?" read by itself),
``open_closed`` (a declared openable place the belief holds closed is opened before the look), ``confirm_none`` (every goal dropped on a
decision no look made: asked once more). Made-up cases (a gizmo, a bin, a crate, a desk), on stubs and through the real AssistLoop."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options.config import resolve
from robot_agent.options.cond_goal import _UNKNOWN, SPEC, CondGoal, answer_holds, ask_when, closed_openable, split

BIN, DESK, BENCH = "bin@den", "desk@den", "bench@yard"
ENV = {BIN: {"aliases": ["bin"], "loc": {"x": 2.0, "y": 1.0}, "openable": True},
       DESK: {"aliases": ["desk"], "loc": {"x": 1.0, "y": 1.0}, "openable": False},
       BENCH: {"aliases": ["bench"], "loc": {"x": 6.0, "y": 1.0}, "openable": False}}
GOAL = {"type": "on", "object": "gizmo", "from": "", "to": "bench"}
OTHER = {"type": "on", "object": "widget", "from": "", "to": "bench"}
Q = "Is the {object} at the {place}?"
LAYER = {"cond_goal.answer": "1", "cond_goal.open_closed": "1", "cond_goal.confirm_none": "1"}


def when(at, present=True, **kw):
    return {"object": "gizmo", "at": at, "present": present, **kw}


# ── the pure decision ────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("yes,no,neg,named,unsure,want", [
    (True, False, False, None, False, True),          # "yes"
    (False, True, False, None, False, False),         # "no"
    (False, False, False, BIN, False, True),          # "it is in the bin" (the condition place)
    (False, False, False, DESK, False, False),        # "it is on the desk" (another place)
    (False, False, False, None, False, _UNKNOWN),     # "hmm" (nothing usable)
    (False, False, False, BIN, True, _UNKNOWN),       # "I don't know, maybe the bin"
    (False, True, True, BIN, False, False),           # "it is not in the bin"
    (True, False, False, DESK, False, _UNKNOWN),      # "yes, on the desk": the yes and the place disagree
    (False, True, False, BIN, False, _UNKNOWN),       # "no, in the bin": the no and the place disagree
])
def test_answer_holds_reads_yes_no_negation_and_the_named_place(yes, no, neg, named, unsure, want):
    assert answer_holds(yes, no, neg, named, BIN, unsure) == want


def test_closed_openable_needs_the_field_the_rule_and_a_closed_belief():
    assert closed_openable(ENV[BIN], True, set(), BIN) is True
    assert closed_openable(ENV[BIN], True, {BIN}, BIN) is False
    assert closed_openable(ENV[BIN], False, set(), BIN) is False
    assert closed_openable({"aliases": []}, True, set(), BIN) is False and closed_openable(None, True, set(), BIN) is False


def test_ask_when_and_split():
    assert ask_when({"when": {"present": True, "ask": "Will it snow?"}}) == {"object": "Will it snow?", "at": "", "present": True, "ask": "Will it snow?"}
    assert ask_when({"when": when("bin")}) is None and ask_when({"when": {"present": "yes", "ask": "x"}}) is None
    goals = [{**GOAL, "when": when("bin")}, {**OTHER, "when": when("bin", False)}, dict(GOAL, object="pen")]
    of = lambda g: {"key": "k", "present": g["when"]["present"]} if "when" in g else None       # noqa: E731
    assert split(goals, {"k": True}, of) == ([GOAL, dict(GOAL, object="pen")], [OTHER])


# ── stubs ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def stub(inside=None, answers=(), opts=None, texts=True, open_ok=True):
    """A gizmo that is ``inside`` a place (seen only when the robot stands there and the place is open or not openable)."""
    log = NS(asked=[], steps=[], labs=[])
    run = NS(goals=[], world=NS(arrived="", opened=set()), filtered=[], ev=lambda e, **k: {"event": e, **k}, lang="en",
             places=NS(env=ENV, openable=lambda k: bool(ENV.get(k, {}).get("openable"))))
    replies = list(answers)

    def look_for(r, obj):
        here = r.world.arrived == inside
        return here and (inside in r.world.opened or not ENV[inside]["openable"]), "find"

    def run_step(r, st):
        log.steps.append((st["action"], st["object"]))
        ok = st["action"] != "Open" or open_ok
        if ok:
            r.world.arrived = st["object"] if st["action"] == "MoveTo" else r.world.arrived
            r.world.opened |= {st["object"]} if st["action"] == "Open" else set()
        return NS(ok=ok)

    def ask(r, q):
        log.asked.append(str(q))
        return replies.pop(0) if replies else ""

    def ground(r, t):
        hits = [k for k in ENV if k.split("@")[0] in str(t or "").lower()]
        return hits[0] if len(hits) == 1 else None

    svc = NS(role={"move": "MoveTo", "open": "Open"}.get, lab=lambda f, **k: log.labs.append(k), look_for=look_for, ask=ask,
             ground_place=ground, remembered=lambda r, o: None, run_step=run_step, cancelled=lambda r: None,
             in_lang=lambda t, lang: t, is_unsure=lambda r, t: "know" in t.lower(),
             finish=lambda r, status, **kw: {"event": "done", "status": status, **kw})
    raw = {"cond_goal": "1", "cond_goal.assumed": "1", **({"cond_goal.q_unsure": Q} if texts else {}), **(opts or {})}
    return CondGoal(resolve(SPEC, raw, {}, None), svc), run, log


def drain(gen):
    out = []
    try:
        while True:
            out.append(next(gen))
    except StopIteration as e:
        return out, e.value


def unsure_stub(answer, opts):
    """The look misses and then sees (unsure): the person is asked once."""
    opt, run, log = stub(DESK, [answer], opts)
    seen = iter([False, True])
    opt.svc.look_for = lambda r, o: (next(seen, False), "find")
    run.goals = [{**GOAL, "when": when("desk")}, {**OTHER, "when": when("desk", False)}]
    return opt, run, log


# ── answer ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_cond_goal_answer_off_a_location_answer_reads_as_no_as_before():
    opt, run, log = unsure_stub("It is on the desk.", {})
    drain(opt.on_goals(run))
    assert [g["object"] for g in run.goals] == ["widget"] and len(log.asked) == 1


@pytest.mark.parametrize("answer,kept", [("It is on the desk.", ["gizmo"]), ("I put it in the bin.", ["widget"]),
                                         ("yes", ["gizmo"]), ("no", ["widget"]), ("It is not on the desk.", ["widget"])])
def test_answer_on_reads_the_answer_by_itself(answer, kept):
    opt, run, log = unsure_stub(answer, {"cond_goal.answer": "1"})
    evs, res = drain(opt.on_goals(run))
    assert res is None and [g["object"] for g in run.goals] == kept and log.asked == ["Is the gizmo at the desk@den?"]


@pytest.mark.parametrize("answer", ["I don't know.", "", "hmm"])
def test_answer_on_dont_know_or_silence_is_unknown_and_never_a_default(answer):
    opt, run, log = unsure_stub(answer, {"cond_goal.answer": "1"})
    evs, res = drain(opt.on_goals(run))
    assert res == "stop" and evs[-1]["status"] == "failed" and len(log.asked) == 1 and run.goals[0].get("when")


def test_answer_on_a_question_when_is_asked_and_its_yes_keeps_the_if_branch():
    opt, run, log = stub(DESK, ["Yes, it will."], {"cond_goal.answer": "1"})       # its words beyond the yes are the question's own
    ask = {"present": True, "ask": "Will it snow today?"}
    run.goals = [{**GOAL, "when": ask}, {**OTHER, "when": {**ask, "present": False}}]
    drain(opt.on_goals(run))
    assert log.asked == ["Will it snow today?"] and [g["object"] for g in run.goals] == ["gizmo"] and log.steps == []


def test_cond_goal_answer_off_a_question_when_is_no_condition_nothing_asked():
    opt, run, log = stub(DESK, ["no"], {})
    run.goals = [{**GOAL, "when": {"present": True, "ask": "Will it snow today?"}}]
    assert drain(opt.on_goals(run)) == ([], None) and run.goals == [GOAL] and log.asked == []


# ── open_closed ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_open_closed_on_opens_a_closed_openable_place_before_the_look():
    opt, run, log = stub(BIN, opts={"cond_goal.open_closed": "1"})
    run.goals = [{**GOAL, "when": when("bin")}]
    drain(opt.on_goals(run))
    assert log.steps == [("MoveTo", BIN), ("Open", BIN)] and run.goals == [GOAL] and {"part": "open", "place": BIN, "ok": True} in log.labs


def test_cond_goal_open_closed_off_or_a_surface_or_no_open_role_opens_nothing():
    for opts, at, inside in (({}, "bin", BIN), ({"cond_goal.open_closed": "1"}, "desk", DESK)):
        opt, run, log = stub(inside, opts=opts)
        run.goals = [{**GOAL, "when": when(at)}]
        drain(opt.on_goals(run))
        assert [a for a, _ in log.steps] == ["MoveTo"]
    opt, run, log = stub(BIN, opts={"cond_goal.open_closed": "1"})
    opt.svc.role = {"move": "MoveTo"}.get
    run.goals = [{**GOAL, "when": when("bin")}]
    drain(opt.on_goals(run))
    assert [a for a, _ in log.steps] == ["MoveTo"]


def test_cond_goal_open_closed_empty_no_openable_field_in_the_env_opens_nothing():
    opt, run, log = stub(BIN, opts={"cond_goal.open_closed": "1"})
    run.places = NS(env={BIN: {"aliases": ["bin"]}}, openable=lambda k: True)
    run.goals = [{**GOAL, "when": when("bin")}]
    drain(opt.on_goals(run))
    assert [a for a, _ in log.steps] == ["MoveTo"]


# ── confirm_none ─────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("answer,status,kept", [("It is in the bin.", None, ["gizmo"]), ("no", "success", []), ("", "failed", [])])
def test_confirm_none_a_blind_miss_that_drops_every_goal_is_asked_once(answer, status, kept):
    opt, run, log = stub(BIN, [answer], {"cond_goal.confirm_none": "1"})
    run.goals = [{**GOAL, "when": when("bin")}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == ["Is the gizmo at the bin@den?"] and (evs[-1]["status"] if evs else None) == status
    assert [g["object"] for g in run.goals] == kept or status == "failed"


def test_cond_goal_confirm_none_off_or_an_observed_miss_ends_success_without_a_question():
    for opts, at, inside in (({}, "bin", BIN), ({"cond_goal.confirm_none": "1"}, "desk", "bench@yard")):
        opt, run, log = stub(inside, ["yes"], opts)
        run.goals = [{**GOAL, "when": when(at)}]
        evs, res = drain(opt.on_goals(run))
        assert log.asked == [] and evs[-1]["status"] == "success" and res == "stop"


def test_cond_goal_confirm_none_empty_no_question_text_ends_success_as_before():
    opt, run, log = stub(BIN, ["It is in the bin."], {"cond_goal.confirm_none": "1"}, texts=False)
    run.goals = [{**GOAL, "when": when("bin")}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == [] and evs[-1]["status"] == "success"


def test_confirm_none_an_open_that_failed_is_still_a_blind_miss():
    opt, run, log = stub(BIN, ["yes"], LAYER, open_ok=False)
    run.goals = [{**GOAL, "when": when("bin")}]
    drain(opt.on_goals(run))
    assert log.steps[:2] == [("MoveTo", BIN), ("Open", BIN)] and len(log.asked) == 1 and run.goals == [GOAL]


# ── through the loop: the class (the object is inside a closed container the condition names) ─────────────────────────────────
@pytest.fixture
def env():
    return {k: dict(v) for k, v in ENV.items()}


def _loop(make_assist, opts, answers=()):
    goal = {**GOAL, "from": BIN, "to": BENCH, "when": when("bin")}
    und = {"status": "clear", "reason": "", "question": "", "goals": [goal]}
    robot = FakeRobot(ENV, {"gizmo": BIN + "/in"}, start=BENCH, answers=answers)
    plan = steps(f"MoveTo {BIN}", f"Open {BIN}", "Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}")
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, search_legs=1, health_wait_s=0,
                       **{"cond_goal": "1", "cond_goal.prompt": "A condition is a goal's when.", "cond_goal.q_unsure": Q,
                          "cond_goal.assumed": "1", **opts})
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find", "open": "Open"}
    return robot, run_task(loop, "If the gizmo is in the bin, put it on the bench.", max_events=3000)


def test_loop_off_the_closed_bin_is_read_as_absent_and_the_run_says_success_with_nothing_done(make_assist):
    robot, evs = _loop(make_assist, {})
    assert evs[-1]["status"] == "success" and robot.obj["gizmo"] == BIN + "/in" and not any(t[0] == "open_drawer" for t in robot.trace)


def test_loop_regression_the_layer_opens_the_closed_bin_sees_the_gizmo_and_moves_it(make_assist):
    robot, evs = _loop(make_assist, LAYER)
    assert evs[-1]["status"] == "success" and robot.obj["gizmo"] == BENCH
    assert [t[0] for t in robot.trace if t[0] in ("open_drawer", "find")][:2] == ["open_drawer", "find"]


def test_loop_regression_a_location_answer_after_a_blind_miss_keeps_the_goal(make_assist):
    robot, evs = _loop(make_assist, {"cond_goal.answer": "1", "cond_goal.confirm_none": "1"}, answers=["It is in the bin."])
    assert len(robot.asked) >= 1 and robot.obj["gizmo"] == BENCH and evs[-1]["status"] == "success"
