"""SAGE-v2 round 3, step 5 (understanding and clarification): F4.1 a capability refusal checked against the
skill contract and the manifest's limits, F4.2 not heard -> "say it again", F3.3 object mentions only in object
position (subjects, fillers and fragments are not "What should I do with the X?"), F3.5 a room-level go needs no
question, F3.6 places inside a message are not targets, F3.7 a noisy answer grounds through "Did you mean ...?".

Classes (gate30 round 1): C4a over-refusal (EN 001 003), C4b "No task was provided" (EN / KO 014), C3b
fragments (EN 007 016 029), C3d (EN / KO 020), C3f (EN / KO 015), C3e (KO 019 027). Synthetic objects, limits
and sentences written for the test.
"""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.understand import find_issues, fuzzy_place, issue_question

DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"


def understood(*goals, status="clear", reason="", reason_type="", question=""):
    return {"status": status, "reason": reason, "reason_type": reason_type, "question": question,
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


@pytest.fixture
def limits(monkeypatch):
    """The fakebot manifest with body limits and an object-class prior (a 12 kg crate, a 0.3 kg cup)."""
    from robot_agent.contract import manifest as M
    orig = M.Manifest.from_dict.__func__

    def with_limits(cls, d, source=None):
        return orig(cls, {**d, "limits": {"payload_kg": 5, "objects": {"crate": {"weight_kg": 12},
                                                                       "cup": {"weight_kg": 0.3}}}}, source)
    monkeypatch.setattr(M.Manifest, "from_dict", classmethod(with_limits))


# ── F4.1 ──
def test_infeasible_slide_overridden_to_putin(make_assist, env):
    robot = FakeRobot(env, {"plate": DS})
    llm = FakeLLM(understood(("in", "plate", "desk", "drawer"), status="infeasible", reason="I cannot slide things.",
                             reason_type="missing_skill"))
    planner = ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find plate", "Pick plate",
                                    f"MoveTo {DR}", f"PutIn {DR}"))
    evs = run_task(make_assist(robot, llm, planner), "Slide the plate from the desk into the drawer.")
    d = done(evs)
    assert d["status"] == "success", d
    assert any(e["event"] == "clarify" and e.get("kind") == "confirm" for e in evs)      # read back, confirmed
    assert any(e["event"] == "understand_check" and e.get("override") for e in evs)


def test_payload_limit_refuses_heavy_synthetic_object(make_assist, env, limits):
    robot = FakeRobot(env, {"crate": DS})
    llm = FakeLLM(understood(("on", "crate", "desk", "dining table")))
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner([])), "Put the crate on the dining table."))
    assert d["status"] == "refused" and "too heavy" in d["msg"] and robot.trace == []
    # a light object under the same limits goes on
    llm2 = FakeLLM(understood(("on", "cup", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find cup", "Pick cup", f"MoveTo {DT}", f"Place {DT}"))
    assert done(run_task(make_assist(FakeRobot(env, {"cup": DS}), llm2, planner),
                         "Put the cup from the desk on the dining table."))["status"] == "success"


def test_payload_reason_kept_without_weight_data(make_assist, env):
    llm = FakeLLM(understood(("on", "wardrobe", "", "dining table"), status="infeasible", reason="It is too heavy.",
                             reason_type="payload"))
    d = done(run_task(make_assist(FakeRobot(env, {}), llm, ScriptedPlanner([])), "Move the wardrobe to the dining table."))
    assert d["status"] == "refused"


def test_unknown_task_no_goals_refused(make_assist, env):
    llm = FakeLLM(understood(status="infeasible", reason="I cannot cook.", reason_type="unknown_task"))
    d = done(run_task(make_assist(FakeRobot(env, {}), llm, ScriptedPlanner([])), "Make me a cheese omelette for lunch."))
    assert d["status"] == "refused"


def test_missing_action_still_refuses(make_assist, env):
    llm = FakeLLM(understood(("say", "hello", "", ""), status="infeasible", reason="no speaker",
                             reason_type="missing_skill"))
    d = done(run_task(make_assist(FakeRobot(env, {}), llm, ScriptedPlanner([])), "Say hello to the guests."))
    assert d["status"] == "refused"


# ── F4.2 ──
def test_empty_instruction_asks_repeat(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, answers=["Put the apple from the desk on the dining table."])
    llm = FakeLLM(understood(status="infeasible", reason="No task was provided."),
                  understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, llm, planner), "uh the")
    assert robot.asked and robot.asked[0] == "Sorry, I didn't catch that. Could you say it again?"
    assert done(evs)["status"] == "success"


# ── F3.3 ──
def test_subject_noun_not_an_issue(places):
    goals = [G("on", "cups", "", DT)]
    issues = find_issues(goals, "The neighbors are coming, so put the cups on the dining table.", places)
    assert not [i for i in issues if i["kind"] == "object"]
    assert not [i for i in find_issues(goals, "Please put the cups on the dining table, please.", places)
                if i["kind"] == "object"]


def test_fragment_asks_repeat(places):
    goals = [G("on", "cups", "", DT)]
    issues = find_issues(goals, "Put the cups and the zorbles on the dining table.", places)
    obj = [i for i in issues if i["kind"] == "object"]
    assert obj and issue_question(obj, places) == "Sorry, I didn't catch that. Could you say it again?"


def test_near_name_asks_did_you_mean(places):
    goals = [G("on", "reading glasses", "", DT)]
    issues = find_issues(goals, "Put the reding glasses and the readin glases on the dining table.", places)
    obj = [i for i in issues if i["kind"] == "object"]
    assert obj and issue_question(obj, places).startswith("Did you mean the reading glasses?")


# ── F3.5 / F3.6 ──
def test_go_room_no_question(places):
    g, q = AssistLoop._ground_template({"type": "go", "object": "", "from": "", "to": "kitchen"}, places,
                                       "wait for me in the kitchen")
    assert q is None and g["to"] in places.in_room("kitchen") and not places.openable(g["to"])


def test_room_in_message_not_a_target(places):
    goals = [{"type": "say", "object": "please come to the dining table", "from": "", "to": "sofa@living room"}]
    issues = find_issues(goals, "Go to the sofa and tell them please come to the dining table.", places)
    assert not [i for i in issues if i["kind"] == "place" and "dining" in i["what"]]


# ── F3.7 ──
def test_noisy_answer_grounds_with_confirm():
    ps = Places({"dining table@kitchen": {"aliases": ["dining table", "식탁"]},
                 "bookshelf@study": {"aliases": ["bookshelf", "책장"]}})
    assert fuzzy_place(ps, "It's on the dinning table.")[0] == "dining table@kitchen"
    assert fuzzy_place(ps, "식딱에 있어요")[0] == "dining table@kitchen"


def test_far_answer_not_grounded():
    ps = Places({"dining table@kitchen": {"aliases": ["dining table", "식탁"]},
                 "bookshelf@study": {"aliases": ["bookshelf", "책장"]}})
    assert fuzzy_place(ps, "I don't know.")[0] is None
    assert fuzzy_place(ps, "somewhere in the garage")[0] is None


def test_noisy_where_answer_in_the_run(make_assist, env):
    robot = FakeRobot(env, {"apple": CT}, answers=["It's on the kounter.", "yes"])
    llm = FakeLLM(understood(("on", "apple", "", "dining table")), default="{}")
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"),
                              replans=[steps(f"MoveTo {CT}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")])
    evs = run_task(make_assist(robot, llm, planner, search=False, search_max=0), "Put the apple on the dining table.")
    assert any("Did you mean" in q for q in robot.asked), robot.asked
    assert done(evs)["status"] == "success", done(evs)
