"""Option ``object_words`` (default OFF), through the real AssistLoop: the reader's goal says 'phone', the plan (and the robot) say 'cell phone'.
Everything is done in the world. Off: the belief tables are looked up by the exact name, the goal is believed unmet after the plan, the run
replans and ends failed. On: the lookup is by words and the run ends success with no replan. 'red mug' and 'green mug' stay different."""
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.options.object_words import WordSet, WordTable

DS, DT = "desk@study room", "dining table@kitchen"
TASK = "Bring the phone from the desk to the dining table."
UNDERSTOOD = {"status": "clear", "reason": "", "question": "",
              "goals": [{"type": "on", "object": "phone", "from": DS, "to": DT}]}
PLAN = steps(f"MoveTo {DS}", "Find cell phone", "Pick cell phone", f"MoveTo {DT}", f"Place {DT}")


def go(make_assist, env, **opts):
    robot = FakeRobot(env, {"cell phone": DS})
    planner = ScriptedPlanner(PLAN)
    loop = make_assist(robot, FakeLLM(UNDERSTOOD, default="{}"), planner, detector_miss_rate=0, ask_help=False, **opts)
    return robot, planner, run_task(loop, TASK, max_events=3000)


def test_registered_and_off_by_default_the_exact_name_is_looked_up(make_assist, env):
    assert "object_words" in ASSIST_OPTIONS
    robot, planner, evs = go(make_assist, env)
    assert robot.obj["cell phone"] == DT
    assert [c for c in planner.calls if c[0] == "replan"] and done(evs)["status"] == "failed"      # the bug this option exists for


def test_on_the_goal_holds_by_the_words_no_replan(make_assist, env):
    robot, planner, evs = go(make_assist, env, object_words="1")
    assert done(evs)["status"] == "success", done(evs)
    assert not [c for c in planner.calls if c[0] == "replan"]


def test_the_tables_match_one_entry_by_words_and_keep_different_objects_apart():
    t = WordTable({"cell phone": "grandpa", "red mug": "desk"})
    assert t.get("phone") == "grandpa" and t.get("cell phone") == "grandpa" and "phone" in t
    assert t.get("green mug") is None and t.get("mug") == "desk"          # one fit
    t["blue mug"] = "sofa"
    assert t.get("mug") is None                                           # two fits: unmatched
    assert t.pop("phone") == "grandpa" and "cell phone" not in t
    s = WordSet({"cell phone"})
    assert "phone" in s and "green book" not in s

