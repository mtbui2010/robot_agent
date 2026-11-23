"""Option ``look_then_ask`` (default ON), through the real AssistLoop: a goal with no source is looked for where the robot stands first;
seen = no question; not seen = ONE question "I don't see the mug here. Where is it?"; a place in the answer is the source; "I don't know"
leaves the search to the memory and the ranking and the question is not asked again; a stated source, a remembered object, or the option
off asks nothing here."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

DS, DT = "desk@study room", "dining table@kitchen"
TASK = "Put the mug on the dining table."


def understood(frm=""):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "on", "object": "mug", "from": frm, "to": "dining table"}]}


PLAN = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start(TASK, user_id="alice", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


def go(make_assist, env, answers=(), frm="", task=TASK, house=None, start="entrance", **opts):
    robot = FakeRobot(env, {"mug": DS} if house is None else house, answers=list(answers), start=start)
    loop = make_assist(robot, FakeLLM(default=understood(frm)), ScriptedPlanner(PLAN), look_then_ask="1", **opts)
    return robot, run_task(loop, task, max_events=3000)


def questions(events):
    return [str(e.get("question")) for e in events if e.get("event") == "clarify"]


def test_not_seen_here_asks_once_and_uses_the_place_in_the_answer(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["on the desk in the study room"])
    qs = questions(events)
    assert qs and "I don't see the mug here. Where is it?" in qs[0]
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_i_do_not_know_is_not_asked_again_after_misses(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I do not know"])
    assert sum("Where is it?" in q or "Where is the mug" in q for q in questions(events)) == 1


def test_seen_here_asks_nothing(run, make_assist, env):
    _robot, events = go(make_assist, env, house={"mug": DS}, start=DS)
    assert not any("Where is" in q for q in questions(events))


def test_a_stated_source_is_not_asked_about(run, make_assist, env):
    _robot, events = go(make_assist, env, frm="desk", task="Put the mug from the desk on the dining table.")
    assert not any("I don't see" in q for q in questions(events))


def test_the_robot_does_not_look_first_or_ask_off(run, make_assist, env):
    robot = FakeRobot(env, {"mug": DS}, answers=[], start="entrance")
    loop = make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(PLAN), look_then_ask="0")
    events = run_task(loop, TASK, max_events=3000)
    assert not any("I don't see" in q for q in questions(events))


def test_ask_source_does_not_ask_the_same_object_again(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I do not know", "I do not know"], ask_source="1")
    assert sum("Where is the mug" in q or "I don't see the mug" in q for q in questions(events)) == 1


# ── an answer the map does not match (key offer_near): made-up flat (den / loft), made-up objects ──
from robot_agent.core.planning.assist_loop import Places                               # noqa: E402
from robot_agent.core.planning.option_services import LoopServices                     # noqa: E402
from robot_agent.options import look_then_ask as LTA                                   # noqa: E402

BENCH, SOFA, RACK, TD, TL = "bench@den", "sofa@den", "rack@loft", "table@den", "table@loft"
FLAT = {BENCH: {"aliases": ["bench"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
        SOFA: {"aliases": ["sofa"], "loc": {"x": 2.0, "y": 1.0}, "surface": True},
        RACK: {"aliases": ["rack"], "loc": {"x": 5.0, "y": 4.0}, "surface": True},
        TD: {"aliases": ["low table"], "loc": {"x": 2.0, "y": 2.0}, "surface": True},
        TL: {"aliases": ["reading table"], "loc": {"x": 6.0, "y": 4.0}, "surface": True},
        "door": {"aliases": ["front door"], "loc": {"x": 0.0, "y": 0.0}, "search": False}}
FLAT_TASK = "Put the gizmo on the sofa."


def flat_und(obj="gizmo", to="sofa"):
    return {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": obj, "from": "", "to": to}]}


def flat_go(make_assist, answers, house=None, to="sofa", plan=None, task=FLAT_TASK, obj="gizmo", **opts):
    robot = FakeRobot(FLAT, house or {"gizmo": BENCH}, answers=list(answers), start="door")
    plan = plan or steps(f"MoveTo {BENCH}", "Find gizmo", "Pick gizmo", f"MoveTo {SOFA}", f"Place {SOFA}")
    loop = make_assist(robot, FakeLLM(default=flat_und(obj, to)), ScriptedPlanner(plan), look_then_ask="1", **opts)
    return loop, run_task(loop, task, max_events=3000)


def kinds(events):
    return [e.get("kind") for e in events if e.get("event") == "clarify"]


def ltas(loop):
    return [x for x in getattr(loop, "lab_log", []) if x.get("flag") == "look_then_ask"]


def near(text, env=FLAT):
    return LoopServices.near_places(type("R", (), {"places": Places(env)})(), text)


@pytest.fixture
def flat(make_assist, monkeypatch):
    import robot_agent.skill_configs as sc
    monkeypatch.setattr(sc, "ENV", dict(FLAT))
    reset_manager()
    run_control._CANCEL.clear()
    run_control.manager().start(FLAT_TASK, user_id="alice", role="member")
    run_control.begin_run()
    yield make_assist
    reset_manager()
    run_control._CANCEL.clear()


def test_offered_drops_the_destination_and_keeps_three():
    assert LTA.offered(["a@x", "b@x", "c@y", "d@y"], "b@x") == ["a@x", "c@y", "d@y"]
    assert LTA.offered(["a@x"], "a@x") == []


def test_offered_empty():
    assert LTA.offered([], "a@x") == [] and LTA.offered([], None) == []


def test_near_places_ranks_a_typo_and_a_longer_name_first():
    assert near("on the benhc") == [BENCH]
    assert near("on the readng table")[0] == TL                     # the typo'd longer name beats the bare word "table"
    assert set(near("on the readng table")) == {TL, TD}


def test_near_places_none_near_or_unsearchable_empty():
    assert near("") == [] and near("zorblat quux") == [] and near("anything", env={}) == []
    assert near("by the front dor") == []                           # search: false is never offered as a source


def test_an_unmatched_answer_gets_one_follow_up_and_its_answer_is_the_source(flat):
    loop, events = flat_go(flat, ["on the benhc", "the bench in the den"])
    qs = [q for q in questions(events) if "could not find" in q]
    assert kinds(events)[:2] == ["where_source", "where_source_near"]
    assert len(qs) == 1 and "'on the benhc'" in qs[0] and "bench" in qs[0]
    lab = ltas(loop)[-1]
    assert lab["place"] == BENCH and lab["answered_unmatched"] and lab["second_answer"]
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_unmatched_twice_asks_no_third_question(flat):
    loop, events = flat_go(flat, ["on the benhc", "on the zorblat", "on the rack"], house={"gizmo": RACK})   # not where the plan looks
    where = [k for k in kinds(events) if k and k.startswith("where_source")]
    assert where == ["where_source", "where_source_near"]
    assert not any("Where is" in q for q in questions(events)[2:])     # the search after the miss does not ask again
    lab = ltas(loop)[-1]
    assert lab["place"] == "" and lab["answered_unmatched"] and lab["second_answer"]


class StubSvc(LoopServices):
    """The engine services the option uses, over a made-up run (no loop): answers from a list, the grounding helpers the real ones."""

    def __init__(self, answers, cancel_after=None):
        super().__init__(None)
        self.answers, self.cancel_after, self.labs = list(answers), cancel_after, []

    def remembered(self, run, obj): return None
    def look_for(self, run, obj): return False, "Find"
    def said_of(self, run, obj): return obj
    def ask(self, run, q): run.asked += 1; return self.answers.pop(0) if self.answers else ""
    def cancelled(self, run): return {"event": "done", "status": "cancelled"} if self.cancel_after == run.asked else None
    def lab(self, flag, **info): self.labs.append(info)
    def is_unsure(self, run, text): return False
    def say_name(self, places, key): return key
    def note_answer(self, run, question, answer, obj, place): run.noted = (obj, place)


def drive(answers, cancel_after=None, to=SOFA):
    from types import SimpleNamespace
    from robot_agent.core.options import OptionConfig
    run = SimpleNamespace(goals=[{"type": "on", "object": "gizmo", "from": "", "to": to}], asked=0, places=Places(FLAT), lang="en",
                          world=None, ev=lambda e, **k: {"event": e, **k})
    svc = StubSvc(answers, cancel_after)
    opt = LTA.LookThenAsk(OptionConfig(LTA.SPEC, {"enabled": True, "offer_near": True}), svc)
    gen, evs = opt.on_goals(run), []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as e:
        return run, svc, evs, e.value


def test_unmatched_twice_ends_as_today_no_place_no_third_question():
    run, svc, evs, out = drive(["on the benhc", "on the zorblat"])
    assert [e.get("kind") for e in evs if e["event"] == "clarify"] == ["where_source", "where_source_near"]
    assert out is None and not run.goals[0]["from"] and ("where", "gizmo") in run.pre_helped
    assert svc.labs[-1]["answered_unmatched"] and svc.labs[-1]["second_answer"] and svc.labs[-1]["place"] == ""


def test_the_second_answer_is_the_source_and_a_yes_takes_the_one_place_offered():
    run, _svc, _evs, _out = drive(["on the benhc", "the bench"])
    assert run.goals[0]["from"] == BENCH and run.noted == ("gizmo", BENCH)
    run, _svc, _evs, _out = drive(["on the benhc", "yes"])
    assert run.goals[0]["from"] == BENCH


def test_a_stop_during_the_follow_up_stops_and_a_spoken_stop_is_only_an_answer():
    from robot_agent.core.options import STOP
    _run, _svc, evs, out = drive(["on the benhc", "the bench"], cancel_after=2)
    assert out == STOP and evs[-1]["status"] == "cancelled"
    run, _svc, _evs, out = drive(["stop", ""])
    assert out is None and not run.goals[0]["from"]


def test_i_do_not_know_gets_no_follow_up(flat):
    loop, events = flat_go(flat, ["I don't know, maybe the benhc"])     # names a near place, but says it does not know
    assert "where_source_near" not in kinds(events) and not ltas(loop)[-1]["second_answer"]


def test_a_matched_answer_gets_no_follow_up(flat):
    loop, events = flat_go(flat, ["on the bench"])
    assert "where_source_near" not in kinds(events)
    assert ltas(loop)[-1]["place"] == BENCH and not ltas(loop)[-1]["answered_unmatched"]


def test_an_unmatched_answer_is_dropped_as_before_offer_near_off(flat):
    loop, events = flat_go(flat, ["on the benhc"], **{"look_then_ask.offer_near": "0"})
    assert "where_source_near" not in kinds(events)
    assert ltas(loop)[-1]["place"] == "" and ltas(loop)[-1]["answered_unmatched"]


def test_only_the_destination_near_gets_no_follow_up(flat):
    _loop, events = flat_go(flat, ["on the sofaa"])
    assert "where_source_near" not in kinds(events)


def test_loop_final_a_typo_of_a_shared_name_is_offered_without_the_destination(flat):
    """The class (real robot): "on the dinnig table" said for the one table whose alias it misspells, several tables on the map
    (one of them the destination): the answer was dropped silently and the robot looked for the object at the destination."""
    plan = steps(f"MoveTo {TL}", "Find widget", "Pick widget", f"MoveTo {TD}", f"Place {TD}")
    loop, events = flat_go(flat, ["on the readng table", "yes"], house={"widget": TL}, to="low table", plan=plan,
                           task="Put the widget on the low table.", obj="widget")
    robot_q = [q for q in questions(events) if "could not find" in q]
    assert len(robot_q) == 1 and "loft" in robot_q[0] and "den" not in robot_q[0]
    assert ltas(loop)[-1]["place"] == TL
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)
