"""Option ``partial_end`` (default OFF): a run whose goals hold while an item of the request stays open (service ``ledger_open``, read through the
engine service ``open_items``) ends ``partial``, naming the done goals and the missing items with the language pack's phrase ``say_partial``; a
complete success is never changed. Made-up houses (a nook in a den with the alias roost, a mat and a ledge in a hall), made-up objects (gizmo,
sprocket), a made-up language ``zq``; nothing here is a real robot's place or a benchmark text."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options import partial_end as P
from robot_agent.options.partial_end import SPEC, PartialEnd, missing

NOOK, MAT, LEDGE = "nook@den", "mat@hall", "ledge@hall"
PLACES = Places({NOOK: {"aliases": ["roost"]}, MAT: {}, LEDGE: {}})
ZQ = {"say_partial": {"zq": "ka {done} lu; ne {missing} lu."}, "rb_on": {"zq": "{object} {place} ko"}, "rb_go": {"zq": "{place} wu"},
      "sep_then": {"zq": " pa "}}


def goal(typ, to, obj=""):
    return {"type": typ, "object": obj, "from": "", "to": to}


def phrase_of(g):
    if g["type"] == "go":
        return speech.LocalText("rb_go", {"place": g["to"]}, text=f"go to the {g['to']}")
    return speech.LocalText("rb_on", {"object": g["object"], "place": g["to"]}, text=f"put the {g['object']} on the {g['to']}")


def make(open_items=(), phrases=None, goals=(), at="", placed=None, lang="en"):
    """The option with a stub of the engine services; ``finish`` returns the fields of the ``done`` event it would send."""
    cfg = resolve(SPEC, {"partial_end": "1"}, {}, None)
    svc = NS(robot=NS(i18n=NS(phrases=phrases or {})), labs=[])
    svc.lab = lambda *a, **k: svc.labs.append((a, k))
    svc.open_items = lambda run: [dict(g) for g in open_items]
    svc.goal_phrase = lambda g, places: phrase_of(g)
    svc.finish = lambda run, status, **kw: {"status": status, **kw}
    run = NS(goals=[dict(g) for g in goals], places=PLACES, world=NS(arrived=at), placed=dict(placed or {}), lang=lang)
    return PartialEnd(cfg, svc), run, svc


GIZMO = goal("on", MAT, "gizmo")


# ── registry, default off, empty ──────────────────────────────────────────────────────────────────────────────────────
def test_registered_default_off_and_the_service_answers_nothing_off(make_assist, env):
    assert "partial_end" in ASSIST_OPTIONS and REGISTRY.get("partial_end").key("enabled").default is False
    assert not resolve(SPEC, {}, {}, None)["enabled"]
    assert SPEC.order > REGISTRY.get("hazard").order                    # hazard's own partial-refusal sentence answers first
    loop = make_assist(FakeRobot(env, {}), FakeLLM(default={"status": "clear", "goals": []}), ScriptedPlanner([]))
    assert loop.options.option("partial_end") is None and loop.options.collect("ledger_open", NS()) == []


def test_no_open_item_no_done_goal_no_phrase_does_nothing_empty(monkeypatch):
    opt, run, svc = make(goals=[GIZMO])
    assert opt.on_goals_met(run) is None                                # no open item: the run ends as before
    opt, run, svc = make(open_items=[goal("go", NOOK)])
    assert opt.on_goals_met(run) is None                                # no done goal
    opt, run, svc = make(open_items=[goal("go", NOOK)], goals=[GIZMO], lang="zq")
    monkeypatch.setattr(speech.langdata, "block", lambda lang, name: {})
    assert speech.phrase(svc.robot, P.PHRASE, "en") is None
    assert opt.on_goals_met(run) is None and not svc.labs              # no phrase in any pack: nothing said, nothing changed


# ── the behaviour ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_partial_an_open_go_item_turns_the_end_into_partial():
    opt, run, svc = make(open_items=[goal("go", NOOK)], goals=[GIZMO], at=MAT, placed={"gizmo": MAT})
    out = opt.on_goals_met(run)
    assert out["status"] == "partial" and out["partial"] == {"done": ["on gizmo to mat@hall"], "missing": ["go to nook@den"]}
    assert str(out["say"]) == "I did only part of it. Done: put the gizmo on the mat@hall. Not done: go to the nook@den."
    assert svc.labs == [(("partial_end",), out["partial"])]


def test_named_in_a_made_up_language_the_robot_phrase_names_done_and_missing_items():
    two = [GIZMO, goal("on", LEDGE, "sprocket")]
    opt, run, _svc = make(open_items=[goal("go", NOOK), goal("go", NOOK)], goals=two, phrases=ZQ, lang="zq")
    out = opt.on_goals_met(run)
    said = speech.render(out["say"], "zq", NS(i18n=NS(phrases=ZQ)), PLACES)
    assert said == "ka gizmo mat (hall) ko pa sprocket ledge (hall) ko lu; ne roost wu lu."   # the pack's words, ENV keys said as names, the item once
    assert out["partial"]["missing"] == ["go to nook@den"]


def test_complete_success_unchanged_when_the_end_state_satisfies_the_item():
    assert missing([goal("go", NOOK)], NS(goals=[], world=NS(arrived=NOOK), placed={})) == []            # it stands there
    assert missing([goal("on", LEDGE, "sprocket")], NS(goals=[], world=NS(arrived=""), placed={"sprocket": LEDGE})) == []
    assert missing([dict(GIZMO, **{"from": None})], NS(goals=[GIZMO], world=NS(arrived=""), placed={})) == []   # one of the goals
    opt, run, svc = make(open_items=[goal("go", NOOK)], goals=[GIZMO], at=NOOK)
    assert opt.on_goals_met(run) is None and not svc.labs


# ── through the real loop ────────────────────────────────────────────────────────────────────────────────────────────
CT, DT = "counter@kitchen", "dining table@kitchen"
TABLE, GO = goal("on", DT, "gizmo"), goal("go", NOOK)
PLAN = steps(f"MoveTo {CT}", "Find gizmo", "Pick gizmo", f"MoveTo {DT}", f"Place {DT}")


def loop_run(make_assist, env, task, answers=(), **opts):
    env[NOOK] = {**env["stool@bedroom"], "aliases": ["roost"], "loc": {"x": 3.1, "y": 2.2, "rz": 0}}
    robot = FakeRobot(env, {"gizmo": CT}, answers=list(answers))
    llm = FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": [dict(TABLE), dict(GO)]})
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN + steps(f"MoveTo {NOOK}")), detector_miss_rate=0, ask_help=False, **opts)
    return robot, loop, run_task(loop, task, max_events=3000)


GARBLED = "Put the gizmo on the dining table, then head back to the roostlomaxi"  # the place's name with a tail too long to restore it
AGAIN = [GARBLED] * 3                                                               # asked to say it again, the person says the same
INVENTED = "Put the gizmo on the dining table, then have a rest"                  # no sign of the place: the reader's go goal is its own


def test_partial_loop_final_a_go_goal_dropped_with_a_weak_cue_of_its_place_ends_partial_and_names_it(make_assist, env):
    robot, loop, evs = loop_run(make_assist, env, GARBLED, AGAIN, goal_ledger="1")
    assert done(evs)["status"] == "success" and robot.at != NOOK and robot.obj["gizmo"] == DT     # off: the false success of the class
    robot, loop, evs = loop_run(make_assist, env, GARBLED, AGAIN, goal_ledger="1", partial_end="1")
    d = done(evs)
    assert d["status"] == "partial" and robot.obj["gizmo"] == DT and robot.at != NOOK
    assert d["partial"] == {"done": [f"on gizmo to {DT}"], "missing": [f"go to {NOOK}"]}
    assert "Done: put the gizmo on the dining table" in d["say"] and "Not done: go to the" in d["say"] and "nook" in d["say"]
    assert "@" not in d["say"] and [e for e in loop.lab_log if e["flag"] == "partial_end"]


def test_complete_loop_final_a_restored_go_goal_is_a_success_said_as_before(make_assist, env):
    task = "Put the gizmo on the dining table, then go back to the roostlo."
    robot, _l, off = loop_run(make_assist, env, task, goal_ledger="1")
    robot, loop, on = loop_run(make_assist, env, task, goal_ledger="1", partial_end="1")
    assert done(on)["status"] == done(off)["status"] == "success" and robot.at == NOOK and robot.obj["gizmo"] == DT
    assert done(on)["say"] == done(off)["say"] and "partial" not in done(on)
    assert not [e for e in loop.lab_log if e["flag"] == "partial_end"]


def test_complete_loop_final_a_go_goal_the_reader_invented_never_turns_a_success_into_partial(make_assist, env):
    """Regression shape: every go goal the goal filter dropped with no evidence was an open item, so a complete success ended 'partial'."""
    robot, _l, off = loop_run(make_assist, env, INVENTED, goal_ledger="1")
    robot, loop, on = loop_run(make_assist, env, INVENTED, goal_ledger="1", partial_end="1")
    assert done(on)["status"] == done(off)["status"] == "success" and robot.obj["gizmo"] == DT and "partial" not in done(on)
    assert done(on)["say"] == done(off)["say"] and not [e for e in loop.lab_log if e["flag"] in ("partial_end", "goal_ledger")]


def test_partial_loop_final_without_the_ledger_nothing_is_reported_open_and_the_end_is_unchanged(make_assist, env):
    robot, loop, evs = loop_run(make_assist, env, GARBLED, AGAIN, partial_end="1")
    assert done(evs)["status"] != "partial" and not [e for e in loop.lab_log if e["flag"] == "partial_end"]
