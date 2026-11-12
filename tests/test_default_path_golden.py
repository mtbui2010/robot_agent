"""The default path (every option off) must behave exactly as before: the events of four scripted runs are compared with a golden file.

The benchmark runs the engine with its options off, so any change that alters these events changes benchmark results. Regenerate the golden
file only for a change that is MEANT to alter the default behaviour: `UPDATE_GOLDEN=1 pytest tests/test_default_path_golden.py`."""
import json
import os
from pathlib import Path

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

GOLDEN = Path(__file__).parent / "golden" / "default_path.json"
DS, DT, CT, DR = "desk@study room", "dining table@kitchen", "counter@kitchen", "drawer@kitchen"
PLAN = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
KEEP = ("event", "status", "msg", "question", "action", "object", "reason", "kind", "code", "round")


def understood(frm=""):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "on", "object": "mug", "from": frm, "to": "dining table"}]}


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start("t", user_id="alice", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


def digest(events):
    return [{k: e[k] for k in KEEP if k in e and e[k] not in (None, "")} for e in events if e.get("event") not in ("status", "world")]


def scenario(make_assist, env, name, objects, faults=(), answers=(), plan=PLAN, replans=None, task="Put the mug on the dining table.", **opts):
    robot = FakeRobot(env, objects, faults=list(faults), answers=list(answers))
    planner = ScriptedPlanner(plan, replans=replans or [])
    loop = make_assist(robot, FakeLLM(default=understood()), planner, **opts)
    return {"name": name, "events": digest(run_task(loop, task, max_events=3000)), "trace": [list(t[:3]) for t in robot.trace]}


def all_scenarios(make_assist, env):
    return [
        scenario(make_assist, env, "success", {"mug": DS}),
        scenario(make_assist, env, "find_miss_then_replan", {"mug": DS}, faults=[{"skill": "find", "obj": "mug", "kind": "fail", "nth": 1}],
                 replans=[PLAN]),
        scenario(make_assist, env, "pick_fails_twice", {"mug": DS}, faults=[{"skill": "pick", "kind": "fail", "nth": [1, 2]}], replans=[PLAN]),
        scenario(make_assist, env, "object_not_there", {"mug": CT}, replans=[PLAN, PLAN]),
    ]


def test_the_default_path_is_unchanged(run, make_assist, env):
    got = all_scenarios(make_assist, env)
    if os.environ.get("UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        pytest.skip("golden file written")
    want = json.loads(GOLDEN.read_text())
    assert json.loads(json.dumps(got, sort_keys=True)) == want, "the default path changed: events differ from tests/golden/default_path.json"
