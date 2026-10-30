"""T2 fix: the search belief's experience prior (search_experience.py): where the robot's past episodes
picked an object up outranks an LLM's commonsense ranking after a miss (T2 S06 / H04 / H07 / M04)."""

import json

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

from robot_agent.core.planning import search_experience as X
from robot_agent.core.planning.search import SearchBelief, SearchConfig

DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"
SF, SD, SK, ST = "sofa@living room", "side table@living room", "shelf@kitchen", "stool@bedroom"
PLACES = [SF, SD, DS, DR, CT, SK, DT, ST]
RANK = r"Rank the places where the apple"


def episode(task, *items, **meta):
    return json.dumps({"task": task, "plan_text": json.dumps({"steps": steps(*items)}), "ts": 1.0, **meta})


def write_live(path, *lines):
    path.write_text("\n".join(lines) + "\n")


def test_picks_follow_the_robot_position():
    st = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}", "MoveTo nowhere", "Pick mug")
    assert X.picks(st, PLACES) == [("apple", DS)]


def test_picked_at_counts_and_skips_unverified(tmp_path):
    p = tmp_path / "live.jsonl"
    write_live(p, episode("a", f"MoveTo {DS}", "Find apple", "Pick apple"),
               episode("b", f"MoveTo {DS}", "Find apple", "Pick apple", verified=True),
               episode("c", f"MoveTo {CT}", "Find apple", "Pick apple", verified=False),
               "not json", json.dumps({"task": "t", "steps": steps(f"MoveTo {ST}", "Find Tennis_Ball", "Pick Tennis_Ball")}))
    got = X.picked_at(str(p), PLACES)
    assert got["apple"] == {DS: 2} and got["tennis ball"] == {ST: 1}
    assert X.picked_at(str(tmp_path / "none.jsonl"), PLACES) == {} and X.picked_at(None, PLACES) == {}
    assert X.lookup(got, "Apple") == {DS: 2} and X.lookup(got, "pear") == {}


def test_experience_outranks_the_top_of_a_commonsense_ranking():
    b = SearchBelief(PLACES, SearchConfig())
    b.learn("apple", {DS: 3, "nowhere": 2})
    b.rank("apple", lambda _p: {"places": [SF, SD, SK]})
    assert b.habit("apple") == {DS: 1.0}
    assert b.order("apple")[:2] == [DS, SF]
    b.missed("apple", CT, ())
    assert b.next_leg("apple", CT).place == DS
    assert "picked the apple up at desk@study room (3 times)" in b.rank_prompt("apple")


def test_experience_is_not_a_sighting_and_survives_to_dict():
    b = SearchBelief(PLACES, SearchConfig())
    b.learn("apple", {DS: 1})
    assert b.prior("apple") == {} and not b.needs_rank_first("apple")
    c = SearchBelief(PLACES, SearchConfig()).load(b.to_dict())
    assert c.habit("apple") == {DS: 1.0}


def test_habit_weight_zero_is_the_v2_search(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOT_AGENT_SEARCH_HABIT_WEIGHT", "0")
    assert SearchConfig.from_env().habit_weight == 0.0
    assert SearchConfig.from_env({"search_habit_weight": 0.3}).habit_weight == 0.3


def test_after_a_miss_the_robot_looks_where_it_picked_the_apple_before(make_assist, env, tmp_path):
    """S06 replayed: the planner looks at the counter, the LLM ranks the living room first and leaves the
    desk out; the live memory has the apple picked up at the desk: the desk is the next look."""
    write_live(tmp_path / "live.jsonl", episode("Bring the apple from the desk to the dining table.",
                                                f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    apple = {"status": "clear", "reason": "", "question": "",
             "goals": [{"type": "on", "object": "apple", "from": "", "to": "dining table"}]}
    counter = steps(f"MoveTo {CT}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    sofa = steps(f"MoveTo {SF}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    planner = ScriptedPlanner(counter, replans=[sofa, sofa, sofa])
    llm = FakeLLM(apple, (RANK, {"places": [SF, SD, SK, DT]}), default=apple)
    robot = FakeRobot(env, {"apple": DS, "mug": DS})
    evs = run_task(make_assist(robot, llm, planner), "Put the apple on the dining table.")
    assert done(evs)["status"] == "success", done(evs)
    rp = [e for e in evs if e["event"] == "replan"]
    assert "Look next at desk@study room" in rp[0]["reason"]
