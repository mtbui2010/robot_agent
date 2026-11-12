"""Option ``ask_source`` (default OFF), through the real AssistLoop: when the request does not say where the object is and the memory does
not know, the robot asks first ("Where is the mug?"); a place in the answer becomes the source and the plan starts there; "I don't know"
leaves the search to the memory and the ranking, and the same question is not asked again after misses; a request that names the source,
or an object the memory still knows, is not asked about; with the option off nothing changes."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

DS, DT = "desk@study room", "dining table@kitchen"
HOUSE = {"mug": DS}
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


def go(make_assist, env, answers=(), frm="", task=TASK, **opts):
    robot = FakeRobot(env, HOUSE, answers=list(answers))
    loop = make_assist(robot, FakeLLM(default=understood(frm)), ScriptedPlanner(PLAN), **opts)
    events = run_task(loop, task, max_events=3000)
    return robot, events


def questions(events):
    return [e.get("question") for e in events if e.get("event") == "clarify"]


def test_off_by_default_the_robot_does_not_ask_first(run, make_assist, env):
    from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
    assert "ask_source" in ASSIST_OPTIONS
    _robot, events = go(make_assist, env)
    assert not any("Where is the mug" in str(q) for q in questions(events))


def test_asks_where_it_is_and_uses_the_place_in_the_answer(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["on the desk in the study room"], ask_source="1")
    qs = questions(events)
    assert qs and "Where is the mug" in str(qs[0])
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_i_do_not_know_is_not_asked_again_after_misses(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I do not know"], ask_source="1")
    assert sum("Where is the mug" in str(q) or "Where is it" in str(q) for q in questions(events)) == 1


def test_a_stated_source_is_not_asked_about(run, make_assist, env):
    _robot, events = go(make_assist, env, frm="desk", task="Put the mug from the desk on the dining table.", ask_source="1")
    assert not any("Where is the mug" in str(q) for q in questions(events))


def test_reask_on_miss_asks_again_when_the_named_place_does_not_show_it(run, make_assist, env):
    answers = ["It is on the kitchen counter.", "on the desk in the study room"]       # the first place is wrong
    _robot, events = go(make_assist, env, answers=answers, ask_source="1", ask_source_reask="1")
    qs = [str(q) for q in questions(events)]
    assert "Where is the mug" in qs[0] and any("couldn't find the mug" in q for q in qs[1:]), qs
    assert sum("couldn't find the mug" in q for q in qs) == 1, qs


def test_reask_off_by_default_the_wrong_place_is_not_asked_about_again(run, make_assist, env):
    answers = ["It is on the kitchen counter.", "on the desk in the study room"]
    _robot, events = go(make_assist, env, answers=answers, ask_source="1")
    assert not any("couldn't find the mug" in str(q) for q in questions(events))


def test_reask_on_miss_is_not_used_after_i_do_not_know(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I do not know"], ask_source="1", ask_source_reask="1")
    assert sum("Where is the mug" in str(q) or "Where is it" in str(q) for q in questions(events)) == 1


def _run_after_first_miss(closed_box):
    """A run whose person named the box: the primed miss has just happened there (the box was closed, or already open)."""
    import types
    from robot_agent.core.planning.search import SearchBelief, SearchConfig
    from robot_agent.core.planning.search_hooks import SearchHooks
    s = SearchBelief(["box@lab", "desk@lab"], SearchConfig(), openable=lambda k: k == "box@lab", can_open=True)
    opened = set() if closed_box else {"box@lab"}
    s.missed("mug", "box@lab", set())                       # the Find saw nothing; the box was closed when it was made
    world = types.SimpleNamespace(arrived="box@lab", opened=opened)
    r = types.SimpleNamespace(find_fails={"mug": 4}, pre_fails={"mug": 3}, search=s, world=world)
    hooks = types.SimpleNamespace(cfg={"search_max": 3})
    return SearchHooks._missed_enough(hooks, r, "mug")


def test_reask_waits_until_the_named_box_was_opened_and_looked_into():
    assert _run_after_first_miss(closed_box=True) is False        # a look into a closed box says nothing: open it first
    assert _run_after_first_miss(closed_box=False) is True        # a whole look that saw nothing: ask once more


# ── answers that name no place: use_room, clarify_unusable, confirm_near (all default off) ──
def says(events):
    return [e.get("say") for e in events if e.get("say") and e.get("event") == "status"]


def test_a_room_only_answer_is_ignored_without_use_room(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["In the study room."], ask_source="1")
    assert not any("look in the study room" in str(s) for s in says(events))


def test_a_room_only_answer_makes_the_robot_say_it_and_look_there_first(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["It is somewhere in the kitchen."], ask_source="1", ask_source_room="1")
    assert any("OK, I will look in the kitchen." in str(s) for s in says(events)), says(events)
    order = next(e["order"] for e in events if e.get("event") == "search")
    assert all("@kitchen" in k for k in order[:3]), order            # the kitchen's places before every other place


def test_use_room_does_not_ask_again_and_keeps_a_fallback_outside_the_room(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["In the study room."], ask_source="1", ask_source_room="1")
    assert sum("Where is the mug" in str(q) or "Where is it" in str(q) for q in questions(events)) == 1
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_unusable_answer_is_not_clarified_by_default(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Over there."], ask_source="1")
    assert len(questions(events)) == 1


def test_unusable_answer_gets_one_clarifying_question_and_its_answer_is_used(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Over there.", "On the desk in the study room."], ask_source="1", ask_source_clarify="1")
    qs = [str(q) for q in questions(events)]
    assert len(qs) == 2 and "Which room, or which piece of furniture" in qs[1], qs
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_still_unusable_after_the_clarifying_question_is_not_asked_a_third_time(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Over there.", "Hmm, around here."], ask_source="1", ask_source_clarify="1")
    qs = [str(q) for q in questions(events)]
    assert sum("Which room, or which piece" in q for q in qs) == 1 and sum("Where is the mug" in q for q in qs) == 1, qs


def test_i_do_not_know_is_not_clarified(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I do not know"], ask_source="1", ask_source_clarify="1")
    assert not any("Which room" in str(q) for q in questions(events))


def test_clarified_answer_may_be_a_room(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Over there.", "In the study room."], ask_source="1", ask_source_clarify="1", ask_source_room="1")
    assert any("look in the study room" in str(s) for s in says(events))


def test_confirm_near_asks_did_you_mean_for_a_cut_name(run, make_assist, env):
    robot = FakeRobot(env, {"mug": "washer@laundry room"}, answers=["the washr", "yes"])
    plan = steps("MoveTo washer@laundry room", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
    loop = make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(plan), ask_source="1", ask_source_near="1")
    events = run_task(loop, TASK, max_events=3000)
    qs = [str(q) for q in questions(events)]
    assert len(qs) == 2 and qs[1].startswith("Did you mean the"), qs


def test_confirm_near_is_off_by_default(run, make_assist, env):
    robot = FakeRobot(env, {"mug": "washer@laundry room"}, answers=["the washr", "yes"])
    plan = steps("MoveTo washer@laundry room", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
    loop = make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(plan), ask_source="1")
    assert not any("Did you mean" in str(q) for q in questions(run_task(loop, TASK, max_events=3000)))


def test_search_belief_prefers_the_named_room_then_falls_back():
    from robot_agent.core.planning.search import SearchBelief, SearchConfig
    s = SearchBelief(["a@x", "b@y", "c@x", "d@y"], SearchConfig(), locs={"a@x": (0, 0), "b@y": (1, 0), "c@x": (9, 0), "d@y": (2, 0)})
    assert s.order("o")[:2] == ["a@x", "b@y"]
    s.prefer("o", ["c@x", "a@x", "zzz"])
    assert s.order("o") == ["a@x", "c@x", "b@y", "d@y"]                  # the room first, the others behind it
    assert s.first_place("o") == "a@x"
    s.missed("o", "a@x", set()); s.missed("o", "c@x", set())
    assert s.next_leg("o", "c@x").place == "b@y"                          # the room is used up: the fallback


def test_a_leading_in_does_not_match_a_place_by_substring(places):
    assert places.candidates("In the study room.", "In the study room.") == ["@study room"]       # "in" was found inside "dining table"
    assert places.candidates("at the kitchen", "at the kitchen") == ["@kitchen"]


def test_a_room_with_an_underscore_is_named_by_its_words():
    from robot_agent.core.planning.assist_loop import Places
    p = Places({"toolbox@tool_crib": {}, "bench@assembly_a": {}})
    assert p.candidates("It is somewhere in the tool crib.", "") == ["@tool_crib"]
    assert p.candidates("tool crib", "") == ["@tool_crib"]
    assert p.in_room("tool_crib") == ["toolbox@tool_crib"]


# ── confirm_near for ROOMS ("Story Room" heard for "study room") ──
def _synthetic_places(**kw):
    from robot_agent.core.planning.assist_loop import Places
    p = Places({"desk@study room": {}, "sofa@living room": {}, "toolbox@tool_crib": {}})
    return p


def test_fuzzy_room_finds_the_room_a_noisy_answer_nearly_names():
    from robot_agent.core.planning.understand import fuzzy_room
    p = _synthetic_places()
    assert fuzzy_room(p, "Story Room") == "study room"
    assert fuzzy_room(p, "it is in the story room.") == "study room"
    assert fuzzy_room(p, "the tool crip") == "tool_crib"                    # an underscored room, by its words
    assert fuzzy_room(p, "Stoney Room") is None                              # 0.27 > 0.25: not near enough at the default
    assert fuzzy_room(p, "Stoney Room", 0.3) == "study room"                 # ... but it is with the key near_threshold


def test_fuzzy_room_does_not_confirm_a_different_word_or_two_equal_rooms():
    from robot_agent.core.planning.assist_loop import Places
    from robot_agent.core.planning.understand import fuzzy_room
    assert fuzzy_room(_synthetic_places(), "over there") is None
    assert fuzzy_room(_synthetic_places(), "the garage") is None
    assert fuzzy_room(Places({"a@room1": {}, "b@room2": {}}), "room3") is None           # equally near both: no guess
    assert fuzzy_room(Places({}), "story room") is None


def test_fuzzy_room_uses_the_names_the_language_layer_declares():
    from robot_agent.core.planning.dialog_ext import Ext
    from robot_agent.core.planning.understand import fuzzy_room
    p = _synthetic_places()
    p.ext = Ext(rooms={"salon": "living room"})
    assert fuzzy_room(p, "the salom") == "living room"


def test_confirm_near_confirms_a_noisy_room_name_and_looks_there_first(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Story Room", "yes"], ask_source="1", ask_source_near="1")
    qs = [str(q) for q in questions(events)]
    assert len(qs) == 2 and qs[1] == "Did you mean the study room?", qs
    assert any("look in the study room" in str(s) for s in says(events)), says(events)
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_a_noisy_room_name_answered_no_is_not_used(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Story Room", "no"], ask_source="1", ask_source_near="1")
    assert not any("look in the study room" in str(s) for s in says(events))


def test_a_different_word_is_not_confirmed_as_a_room(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Over there."], ask_source="1", ask_source_near="1")
    assert len(questions(events)) == 1


def test_noisy_room_name_is_not_confirmed_without_confirm_near(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["Story Room", "yes"], ask_source="1")
    assert not any("Did you mean" in str(q) for q in questions(events))


def test_room_only_answer_to_the_re_asked_where_is_it_is_used_not_dropped(run, make_assist, env, tmp_path):
    """The first answer names a wrong place (the sofa); the first look misses; the robot asks again and the person answers with a ROOM:
    with use_room on the robot says it looks in that room and the replan is told the place in it (the desk); off, the run gave up
    ("could not find the mug") without using the answer."""
    class Capture(ScriptedPlanner):
        def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
            self.reasons = getattr(self, "reasons", []) + [failure_reason]
            return super().replan(task, completed, failed_step, failure_reason, obs, visible_objects)

    def go2(tag, **opts):
        robot = FakeRobot(env, HOUSE, answers=["It is on the sofa.", "in the study room"])
        planner = Capture(PLAN, replans=[PLAN])
        loop = make_assist(robot, FakeLLM(default=understood("")), planner, memory_path=str(tmp_path / f"memory_{tag}.json"), **opts)
        return planner, run_task(loop, TASK, max_events=3000)

    planner, events = go2("on", ask_source="1", ask_source_reask="1", ask_source_room="1")
    assert any("look in the" in str(e.get("say", "")) for e in events), [e for e in events if e.get("event") == "status"]
    assert any(DS in r for r in getattr(planner, "reasons", [])), getattr(planner, "reasons", None)
    planner, events = go2("off", ask_source="1", ask_source_reask="1")
    assert any("could not find the mug" in str(e.get("msg", "")) for e in events if e.get("event") == "done"), \
        [e for e in events if e.get("event") == "done"]



def _washer_run(make_assist, env, answers, **opts):
    robot = FakeRobot(env, {"mug": "washer@laundry room"}, answers=list(answers))
    plan = steps("MoveTo washer@laundry room", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
    loop = make_assist(robot, FakeLLM(default=understood()), ScriptedPlanner(plan), ask_source="1", **opts)
    return [str(q) for q in questions(run_task(loop, TASK, max_events=3000))]


def test_hedged_answer_with_a_noisy_place_is_confirmed_when_the_key_is_on(run, make_assist, env):
    qs = _washer_run(make_assist, env, ["I am not sure, maybe near the washr", "yes"], ask_source_hedged="1")
    assert len(qs) == 2 and qs[1].startswith("Did you mean the"), qs


def test_hedged_answer_is_dropped_as_i_do_not_know_by_default(run, make_assist, env):
    qs = _washer_run(make_assist, env, ["I am not sure, maybe near the washr", "yes"])
    assert len(qs) == 1, qs


def test_hedged_bare_i_do_not_know_is_still_an_unknown(run, make_assist, env):
    qs = _washer_run(make_assist, env, ["I am not sure", "yes"], ask_source_hedged="1")
    assert len(qs) == 1, qs


def test_hedged_answer_naming_a_word_that_is_no_place_is_not_confirmed(run, make_assist, env):
    qs = _washer_run(make_assist, env, ["I am not sure, maybe over there", "yes"], ask_source_hedged="1")
    assert len(qs) == 1, qs


def test_hedged_answer_with_an_exact_place_is_used_without_a_question(run, make_assist, env):
    _robot, events = go(make_assist, env, answers=["I am not sure, maybe on the desk in the study room"], ask_source="1", ask_source_hedged="1")
    assert len(questions(events)) == 1
    assert any(e.get("event") == "done" and e.get("status") == "success" for e in events)


def test_hedged_plural_of_a_place_name_is_a_near_match():
    from robot_agent.core.planning.assist_loop import Places
    from robot_agent.core.planning.understand import fuzzy_place
    P = Places({"bench@workshop": {"aliases": ["workbench"]}})
    assert fuzzy_place(P, "I am not sure, maybe near the workbenches")[0] == "bench@workshop"
