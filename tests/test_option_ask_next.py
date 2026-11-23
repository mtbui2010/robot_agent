"""Option ``ask_next`` (default OFF): after a failed Find / Pick / MoveTo used its own retries the robot asks the person what to do next (again
here, somewhere else, stop) and does it. The pure answer reader on small tables of a made-up language ``zq``, the hook on a stub, and loop-level
tests of the FINAL outcome through the real AssistLoop on the test house with a made-up object (``gizmo``). No real robot, task or benchmark id."""
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, load_env, run_task, steps
from robot_agent.core.options import NEXT, REGISTRY, STOP
from robot_agent.core.planning import langdata, speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.options.ask_next import SPEC, Answer, AskNext, pack_of, parse_answer

ZQ = {"dialogue": {"yes": ["vee"], "no": ["nu"], "stop": ["halto"]},
      "answer_unsure": ["mi ne scias"],
      "ask_next": {"retry": ["ree", "ree ree"], "elsewhere": ["alie", "ne tie"], "anywhere": ["ie ajn"], "stop": ["cedu"], "assumed": True},
      "speech": {"q_ask_next_find": "Ne trovis {object}. ree / alie / cedu?", "q_ask_next_pick": "Ne prenis {object}. ree / alie / cedu?",
                 "q_ask_next_move": "Ne iris {object}. ree / cedu?", "q_ask_next_again": "Diru: ree, alie, cedu.",
                 "q_ask_next_again_move": "Diru: ree, cedu.", "q_ask_next_place": "Kie {object}?", "assumed": True}}


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    return "zq"


def drive(gen):
    """Run a flow hook: (events it yielded, its return value)."""
    if not hasattr(gen, "send"):
        return [], gen
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as e:
        return evs, e.value


# ── parse_answer: the pure reader ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,place,want", [
    ("ree", None, Answer("retry")), ("REE!", None, Answer("retry")), ("vee", None, Answer("retry")),          # yes = the first choice (again)
    ("nu", None, Answer("stop")), ("Nu.", None, Answer("stop")), ("cedu", None, Answer("stop")), ("halto!", None, Answer("stop")),
    ("alie", None, Answer("elsewhere")), ("ne tie, alie", None, Answer("elsewhere")), ("ie ajn", None, Answer("elsewhere", anywhere=True)),
    ("alie, la blua ŝranko", "box@den", Answer("elsewhere", "box@den")), ("la blua ŝranko", "box@den", Answer("elsewhere", "box@den")),
    ("nu, ree", None, Answer("retry")), ("cedu, la blua ŝranko", "box@den", Answer("stop")),                # a stop word wins over a place
    ("mi ne scias", None, Answer("unclear")), ("blah", None, Answer("unclear")), ("", None, Answer("unclear")),
    ("reeree", None, Answer("unclear")), ("nuance", None, Answer("unclear")),                                # whole words only
])
def test_parse_answer_table_in_a_made_up_language(zq, text, place, want):
    assert parse_answer(text, pack_of(zq), place) == want


def test_pack_needs_a_labelled_block_with_words_and_joins_the_dialogue_words(zq, monkeypatch):
    p = pack_of(zq)
    assert p["stop"] == ["cedu", "halto"] and p["yes"] == ["vee"] and p["no"] == ["nu"] and p["unsure"] == ["mi ne scias"]
    for block in ({**ZQ["ask_next"], "assumed": False}, {"assumed": True}, {"retry": "ree", "assumed": True}):
        monkeypatch.setitem(langdata._CACHE, "zq", {**ZQ, "ask_next": block})
        assert pack_of("zq") == {}, block


def test_ask_next_reads_nothing_without_pack_words_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", {"dialogue": ZQ["dialogue"]})          # yes / no only: no ask_next words
    assert pack_of("zq") == {} and parse_answer("ree", pack_of("zq")) == Answer("unclear") and parse_answer("vee", {}) == Answer("unclear")
    assert pack_of("xx") == {}                                                           # a language with no pack at all


# ── the hook on a stub ────────────────────────────────────────────────────────────────────────────────────────────────────
def make(answers=(), ask_skill=True, here="desk@den", ground=None, rooms=None, cap=2):
    labs, asked, calls = [], [], []
    ans = list(answers)

    def ask(run, q):
        asked.append(speech.render(q, "zq"))                                 # what the person hears, in their language
        return ans.pop(0) if ans else ""
    svc = NS(robot=NS(ask_spec=lambda: object() if ask_skill else None, i18n=None), ask=ask, cancelled=lambda run: None,
             lab=lambda flag, **i: labs.append(i), said_of=lambda run, o: o, ground_place=lambda run, t: (ground or {}).get(t),
             room_places=lambda run, t: ("den", (rooms or {}).get(t, [])), first_unsearched=lambda run, o, keys: keys[-1],
             finish=lambda run, status, **kw: {"event": "done", "status": status, **kw},
             retry_here=lambda run, st: calls.append(("retry", st["object"])), note_answer=lambda *a: calls.append(("note",) + a[1:]),
             told_at=lambda run, o, k: calls.append(("told", o, k)) or f"; told {k}",
             search_on=lambda run, o: _search_on(calls, o))
    run = NS(lang="zq", i=2, world=NS(arrived=here), ev=lambda event, **f: {"event": event, **f})
    return AskNext({"max_menus": cap}, svc), run, labs, asked, calls


def _search_on(calls, o):
    calls.append(("search_on", o))
    yield {"event": "recover", "kind": "search_leg"}
    return NEXT, ""


def test_retry_runs_the_step_once_more_in_place(zq):
    opt, run, labs, asked, calls = make(["ree"])
    evs, out = drive(opt.on_ask_help(run, {"action": "Pick", "object": "gizmo"}, "pick"))
    assert out == (NEXT, "") and calls == [("retry", "gizmo")] and asked == ["Ne prenis gizmo. ree / alie / cedu?"]
    assert labs == [{"kind": "pick", "object": "gizmo", "answer": "retry", "place": None}] and evs[0]["kind"] == "ask_next"


def test_elsewhere_with_a_room_takes_its_best_unsearched_place_never_the_place_here(zq):
    opt, run, labs, asked, calls = make(["alie, den"], rooms={"alie, den": ["desk@den", "box@den"]})
    _evs, out = drive(opt.on_ask_help(run, {"action": "Find", "object": "gizmo"}, "where"))
    assert out == (None, "; told box@den") and calls[-1] == ("told", "gizmo", "box@den")
    opt, run, labs, asked, calls = make(["desk@den"], ground={"desk@den": "desk@den"})              # the place it is at: no place, unclear
    assert drive(opt.on_ask_help(run, {"action": "Find", "object": "gizmo"}, "where"))[1] is None and len(asked) == 2


def test_elsewhere_without_a_place_find_leaves_the_engines_where_question_pick_asks_where_once(zq):
    opt, run, labs, asked, calls = make(["alie"])
    assert drive(opt.on_ask_help(run, {"action": "Find", "object": "gizmo"}, "where"))[1] is None and len(asked) == 1
    opt, run, labs, asked, calls = make(["alie", "box@den"], ground={"box@den": "box@den"})
    assert drive(opt.on_ask_help(run, {"action": "Pick", "object": "gizmo"}, "pick"))[1] == (None, "; told box@den")
    assert asked[1] == "Kie gizmo?" and ("told", "gizmo", "box@den") in calls
    opt, run, labs, asked, calls = make(["alie", "mi ne scias"])
    assert drive(opt.on_ask_help(run, {"action": "Pick", "object": "gizmo"}, "pick"))[1] is None and len(asked) == 2


def test_anywhere_on_a_find_lets_the_engines_search_go_on(zq):
    opt, run, labs, asked, calls = make(["ie ajn"])
    evs, out = drive(opt.on_ask_help(run, {"action": "Find", "object": "gizmo"}, "where"))
    assert out == (NEXT, "") and calls == [("search_on", "gizmo")] and evs[-1]["kind"] == "search_leg"


def test_stop_message_ends_aborted_and_no_menu_after_it(zq):
    opt, run, labs, asked, calls = make(["cedu"])
    evs, out = drive(opt.on_ask_help(run, {"action": "MoveTo", "object": "desk@den"}, "move"))
    assert out == (STOP, "") and evs[-1]["status"] == "aborted" and evs[-1]["say"].key == "say_stopped" and calls == []
    assert drive(opt.on_ask_help(run, {"action": "Pick", "object": "widget"}, "pick"))[1] is None and len(asked) == 1


def test_unclear_is_asked_once_more_naming_the_choices_then_the_engines_path(zq):
    opt, run, labs, asked, calls = make(["blah", "blah"])
    assert drive(opt.on_ask_help(run, {"action": "Pick", "object": "gizmo"}, "pick"))[1] is None
    assert asked == ["Ne prenis gizmo. ree / alie / cedu?", "Diru: ree, alie, cedu."] and labs[0]["answer"] == "unclear"
    opt, run, labs, asked, calls = make(["alie", "ree"])                                    # a move offers no other place
    assert drive(opt.on_ask_help(run, {"action": "MoveTo", "object": "desk@den"}, "move"))[1] == (NEXT, "") and asked[1] == "Diru: ree, cedu."


def test_cap_no_repeat_and_no_channel_or_silence_ask_nothing_more(zq):
    opt, run, labs, asked, calls = make(["ree", "ree", "ree"])
    pick = {"action": "Pick", "object": "gizmo"}
    assert drive(opt.on_ask_help(run, pick, "pick"))[1] == (NEXT, "") and drive(opt.on_ask_help(run, pick, "pick"))[1] is None   # once per object and kind
    assert drive(opt.on_ask_help(run, {"action": "Find", "object": "gizmo"}, "where"))[1] == (NEXT, "")
    assert drive(opt.on_ask_help(run, {"action": "Pick", "object": "widget"}, "pick"))[1] is None and len(asked) == 2        # 2 menus per run
    opt, run, labs, asked, calls = make(["ree"], ask_skill=False)
    assert drive(opt.on_ask_help(run, pick, "pick"))[1] is None and asked == [] and labs == []
    opt, run, labs, asked, calls = make([])                                                 # silence: not asked again
    assert drive(opt.on_ask_help(run, pick, "pick"))[1] is None and len(asked) == 1


# ── through the real AssistLoop: the failure class (a failed step that used its retries) ───────────────────────────────────
class StuckBot(FakeRobot):
    """The test house; the grasp of anything fails ``fails`` times; ``stuck``: at that place it always fails; a second one of the object lies
    at ``spare`` (seen and picked there)."""

    def __init__(self, *a, fails=0, stuck=None, spare=None, **k):
        super().__init__(*a, **k)
        self.fails, self.stuck, self.spare = fails, stuck, spare

    def _exec(self, skill, o, params):
        if skill == "find" and self.spare and self.at == self.spare and o in self.obj and self.obj[o] != "robot":
            self.obj[o] = self.spare
        if skill == "pick" and (self.fails > 0 or self.at == self.stuck):
            self.fails -= 1
            return {"isdone": False, "msg": f"grasp failed: {o} slipped"}
        return super()._exec(skill, o, params)


GOALS = [{"type": "on", "object": "gizmo", "from": "", "to": "side table"}]
PLAN = ("MoveTo desk@study room", "Find gizmo", "Pick gizmo", "MoveTo side table@living room", "Place side table@living room")
ELSEWHERE = ("MoveTo counter@kitchen", "Find gizmo", "Pick gizmo", "MoveTo side table@living room", "Place side table@living room")


def _loop(make_assist, robot, on=True, replans=(), **kw):
    und = {"status": "clear", "reason": "", "question": "", "goals": GOALS}
    planner = ScriptedPlanner(steps(*PLAN), replans=[steps(*r) for r in replans])
    loop = make_assist(robot, FakeLLM(default=und), planner, detector_miss_rate=0, **({"ask_next": "1"} if on else {}), **kw)
    evs = run_task(loop, "Put the gizmo on the side table.", max_events=3000)
    return loop, planner, evs


def _bot(answers, **kw):
    return StuckBot(load_env(), {"gizmo": "desk@study room"}, answers=answers, start="entrance", **kw)


def test_registered_and_off_by_default_the_engine_asks_as_today_off(make_assist):
    assert "ask_next" in ASSIST_OPTIONS and REGISTRY.get("ask_next").key("enabled").default is False and SPEC.enabled == "enabled"
    robot = _bot(["no"], fails=2)
    loop, planner, evs = _loop(make_assist, robot, on=False)
    assert not [e for e in loop.lab_log if e["flag"] == "ask_next"] and "move it closer" in robot.asked[0]
    assert not [q for q in robot.asked if "somewhere else" in q]


def test_loop_final_on_without_pack_words_the_engine_asks_as_today_empty(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "en", {k: v for k, v in langdata.get("en").items() if k != "ask_next"})
    robot = _bot(["again"], fails=2)
    loop, planner, evs = _loop(make_assist, robot)
    assert not [e for e in loop.lab_log if e["flag"] == "ask_next"] and len(robot.asked) == 1 and "move it closer" in robot.asked[0]


def test_loop_final_pick_fails_twice_menu_again_and_the_goal_is_reached(make_assist):
    robot = _bot(["Try again, please."], fails=2)
    loop, planner, evs = _loop(make_assist, robot)
    assert robot.asked == ["I couldn't pick up the gizmo. Shall I try again here, look somewhere else, or stop?"]
    assert [t[0] for t in robot.trace].count("pick") == 3 and not [c for c in planner.calls if c[0] == "replan"]
    assert done(evs)["status"] == "success" and robot.obj == {"gizmo": "side table@living room"}


def test_loop_final_look_elsewhere_with_a_place_goes_there_and_finds_it(make_assist):
    robot = _bot(["Look somewhere else: the counter in the kitchen."], stuck="desk@study room", spare="counter@kitchen")
    loop, planner, evs = _loop(make_assist, robot, replans=[ELSEWHERE])
    replan = [c for c in planner.calls if c[0] == "replan"]
    assert len(robot.asked) == 1 and replan and "counter@kitchen" in replan[0][2]
    moves = [t[1] for t in robot.trace if t[0] == "move"]
    there = max(i for i, t in enumerate(robot.trace) if t[:2] == ("move", "counter@kitchen"))
    assert moves[-2:] == ["counter@kitchen", "side table@living room"] and ("pick", "gizmo", True) in [t[:3] for t in robot.trace[there:]]
    assert done(evs)["status"] == "success" and robot.obj == {"gizmo": "side table@living room"}


def test_loop_final_stop_ends_the_run_aborted_with_no_further_motion_and_no_blind_replan(make_assist):
    for word in ("stop", "No, give up."):
        robot = _bot([word], stuck="desk@study room")
        loop, planner, evs = _loop(make_assist, robot)
        d = done(evs)
        assert d["status"] == "aborted" and d["say"] == "Okay, I stopped." and not [c for c in planner.calls if c[0] == "replan"], word
        assert [t[0] for t in robot.trace if t[0] != "ask"][-1] == "pick" and robot.obj == {"gizmo": "desk@study room"}, word


def test_loop_final_unclear_is_asked_once_more_then_todays_question(make_assist):
    robot = _bot(["blah", "blah", "no"], stuck="desk@study room")
    loop, planner, evs = _loop(make_assist, robot)
    assert robot.asked[1] == "Please say one of: again, somewhere else, or stop." and "move it closer" in robot.asked[2]
    assert len(robot.asked) == 3 and done(evs)["status"] != "success"


def test_loop_final_find_fails_menu_again_finds_it_here(make_assist):
    robot = _bot(["again"], faults=[{"skill": "find", "obj": "gizmo", "nth": 1, "kind": "fail", "msg": "gizmo not found"}])
    loop, planner, evs = _loop(make_assist, robot, search_max=0)
    assert robot.asked == ["I couldn't find the gizmo. Shall I look again here, look somewhere else, or stop?"]
    assert done(evs)["status"] == "success" and robot.obj == {"gizmo": "side table@living room"}


def test_loop_final_regression_a_retry_that_fails_again_gets_no_second_menu(make_assist):
    """The class seen on the robot: after "yes" the robot retried once and failed the same way. With the option the person chose again; the
    menu is not asked twice for the same object and failure, so the engine's own path (replan or end) follows, without a second menu."""
    robot = _bot(["again", "no"], stuck="desk@study room")
    loop, planner, evs = _loop(make_assist, robot)
    assert sum("Shall I try again here" in q for q in robot.asked) == 1
    assert [e["answer"] for e in loop.lab_log if e["flag"] == "ask_next"] == ["retry"] and done(evs)["status"] != "success"


def test_loop_final_with_trace_on_the_menu_is_recorded_as_ask_and_the_outcome_is_as_without_it(make_assist, tmp_path):
    """Interplay of two watch / decide options on the same failure: ``trace`` (order 0, sees the failure first) must not change what
    ``ask_next`` does, and its file shows the recovery that asked the menu and the menu's lab entry."""
    plain = _bot(["Try again, please."], fails=2)
    _l, _p, evs_off = _loop(make_assist, plain)
    robot = _bot(["Try again, please."], fails=2)
    _l, _p, evs = _loop(make_assist, robot, trace="1", trace_dir=str(tmp_path))
    rs = [json.loads(x) for f in tmp_path.rglob("trace.jsonl") for x in f.read_text(encoding="utf-8").splitlines()]
    asked = [r for r in rs if r["k"] == "recovery" and r["chose"] == "ask"]
    assert len(asked) == 1 and (asked[0]["action"], asked[0]["object"]) == ("Pick", "gizmo")
    lab = [r["data"] for r in rs if r["k"] == "lab" and r["flag"] == "ask_next"]
    assert lab == [{"kind": "pick", "object": "gizmo", "answer": "retry", "place": None}]
    assert (done(evs)["status"], robot.obj, robot.asked) == (done(evs_off)["status"], plain.obj, plain.asked) == (
        "success", {"gizmo": "side table@living room"}, plain.asked)
