"""RunRecord + replay golden tests: record a run of the real AssistLoop against the
fake robot / scripted LLM / scripted planner through the RunManager, then replay
it (``plan`` and ``exec --fake``) from the record alone and get the same thing."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps

from robot_agent.core import run_control
from robot_agent.core.unified_agent import _serialize_result
from robot_agent.interact.run_manager import reset_manager
from robot_agent.observe import record as R
from robot_agent.observe import replay as RP

DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"
HOUSE = {"apple": DS, "mug": DS, "banana": DT, "orange": DT, "sponge": CT, "spoon": DR + "/in",
         "tennis ball": "stool@bedroom"}


def understood(*goals, status="clear", question="", reason=""):
    return {"status": status, "reason": reason, "question": question,
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


@pytest.fixture
def data(tmp_path, monkeypatch, agent_state):
    root = tmp_path / "data"
    monkeypatch.setenv("ROBOT_AGENT_DATA_DIR", str(root))
    monkeypatch.setenv("ROBOT_AGENT_HOST", "test")
    reset_manager()
    yield root
    reset_manager()


def record_run(loop, task, user_id="alice"):
    """What /ws/agent + UnifiedAgent.run do, minus the socket: start a run, note
    the loop, publish every event on the run, finish it."""
    mgr = run_control.manager()
    run = mgr.start(goal=task, user_id=user_id, role="owner").run
    run_control.begin_run()                      # binds this thread to the run
    R.note_loop(R.instrument(loop), task, "en", kwargs={})
    status = None
    for ev in loop.run_blocking(task, "en"):
        ev = run.publish(_serialize_result(ev))
        if ev["event"] == "plan":
            # someone else talks to the robot mid-run (a user event)
            mgr.inject(run.run_id, "how is it going?", user_id="bob", role="member")
        if ev["event"] == "done":
            status = ev["status"]
    mgr.finish(run.run_id, ok=status == "success", outcome=status)
    return run.run_id


def _ambiguous_with_replan(make_assist, env):
    house = dict(HOUSE, mug=CT)                          # not on the desk: a find miss, then a replan
    robot = FakeRobot(env, house, answers=["the dining table"])
    llm = FakeLLM(understood(("on", "mug", "desk", "table")),
                  understood(("on", "mug", "desk", "dining table")))
    planner = ScriptedPlanner(
        steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}"),
        replans=[steps(f"MoveTo {CT}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")])
    return make_assist(robot, llm, planner), robot


def test_record_has_every_part_of_the_run(make_assist, env, data):
    loop, robot = _ambiguous_with_replan(make_assist, env)
    run_id = record_run(loop, "Put the mug from the desk on the table.")
    assert robot.obj["mug"] == DT

    path = data / "run_records" / "test" / run_id / "record.jsonl"
    rows = R.read_rows(path)
    kinds = {r["kind"] for r in rows}
    assert {"header", "context", "event", "llm", "planner", "skill", "step", "health", "user", "end",
            "snapshot"} <= kinds
    head = rows[0]
    assert head["kind"] == "header" and head["run_id"] == run_id and head["user_id"] == "alice"
    assert head["robot_pkg"] == "fakebot"
    # every event carries the RunManager's run_id unless the loop set its own
    evs = [r["event"] for r in rows if r["kind"] == "event"]
    assert all(e.get("run_id") for e in evs)
    # LLM calls: the understanding prompt and its JSON answer, with model and latency
    llm = [r for r in rows if r["kind"] == "llm"]
    assert len(llm) == 2 and llm[0]["source"] == "robot_agent"
    assert "- dining table@kitchen" in llm[0]["request"]["prompt"]
    assert json.loads(llm[0]["response"])["goals"][0]["to"] == "table"
    assert llm[0]["model"] == "scripted" or llm[0]["backend"] == "FakeLLM"
    assert "latency_ms" in llm[0] and "temperature" in llm[0] and "seed" in llm[0]
    # skill rows: SkillResult + world before; the find miss has a failure code
    sk = [r for r in rows if r["kind"] == "skill"]
    miss = next(r for r in sk if r["skill"] == "find" and not r["result"]["ok"])
    assert miss["result"]["code"] == "absent" and "world_before" in miss
    # step rows: world before / after
    st = [r for r in rows if r["kind"] == "step" and r["status"] == "success"]
    assert st and all("world_before" in r and "world_after" in r for r in st)
    picked = next(r for r in st if r["world_after"].get("holding") == "mug")
    assert picked["world_before"].get("holding") is None
    # user event with the user id
    user = [r for r in rows if r["kind"] == "user"]
    assert user and user[0]["user_id"] == "bob" and user[0]["text"] == "how is it going?"
    # context for the replay
    ctx = next(r for r in rows if r["kind"] == "context")
    assert ctx["loop"] == "assist" and ctx["env"] and ctx["cfg"]["generator"]

    # one metrics line
    m = [json.loads(x) for x in (data / "run_records" / "test" / "metrics.jsonl").read_text().splitlines()]
    assert len(m) == 1
    m = m[0]
    assert m["run_id"] == run_id and m["status"] == "success" and m["success"] is True
    assert m["llm_calls"] == 2 and m["planner_calls"] == 2 and m["replans"] == 1
    assert m["failure_codes"] == {"absent": 1}
    assert m["verified_success"] is True
    # one id per run (D26): the loop takes the RunManager's run_id, so there is no second task_runs id
    assert m["task_run_id"] is None
    assert {e.get("run_id") for e in evs} == {run_id}
    assert m["failure_code"] is None


def test_replay_plan_is_identical(make_assist, env, data):
    loop, _ = _ambiguous_with_replan(make_assist, env)
    run_id = record_run(loop, "Put the mug from the desk on the table.")
    res = RP.replay(run_id, "plan", data_dir=data)
    assert res.error is None, res.summary()
    assert res.identical, res.summary()
    assert res.recorded_plan == res.replayed_plan
    assert res.replayed_plan.splitlines()[0] == f"move::{DS}"


def test_replay_exec_fake_is_identical(make_assist, env, data, capsys):
    loop, _ = _ambiguous_with_replan(make_assist, env)
    run_id = record_run(loop, "Put the mug from the desk on the table.")
    res = RP.replay(run_id, "exec", data_dir=data)
    assert res.identical, res.summary()
    assert res.unused_llm == 0 and res.unused_skills == 0
    assert [e["event"] for e in res.events][-1] == "done" and res.events[-1]["status"] == "success"
    # the CLI on the run dir
    rc = RP.main(["exec", str(data / "run_records" / "test" / run_id), "--fake"])
    assert rc == 0 and "IDENTICAL" in capsys.readouterr().out


def test_replay_reports_a_changed_prompt(make_assist, env, data, tmp_path):
    loop, _ = _ambiguous_with_replan(make_assist, env)
    run_id = record_run(loop, "Put the mug from the desk on the table.")
    rec = RP.load(run_id, data_dir=data)
    # the house changed since the run: a place is gone, so the understanding prompt differs
    ctx = rec.context
    ctx["env"] = {k: v for k, v in ctx["env"].items() if k != "side table@living room"}
    res = RP.replay(rec, "plan")
    assert not res.identical and (res.error or "").startswith("ReplayMismatch"), res.summary()
    assert "the prompt differs" in res.error and "side table" in res.error
    assert RP.main(["plan", str(tmp_path / "nope")]) == 2


def test_replay_exec_detects_a_different_outcome(make_assist, env, data):
    loop, _ = _ambiguous_with_replan(make_assist, env)
    run_id = record_run(loop, "Put the mug from the desk on the table.")
    rec = RP.load(run_id, data_dir=data)
    # the recorded place now fails: replay follows the code, the diff shows where it parted
    place = next(r for r in rec.rows if r["kind"] == "skill" and r["skill"] == "place")
    place["raw"] = {"isdone": False, "msg": "nothing in the gripper"}
    res = RP.replay(rec, "exec")
    assert not res.identical
    assert any("event #" in d for d in res.diffs), res.summary()


def test_direct_run_is_recorded_and_replays(env, data, monkeypatch, agent_state):
    """An open-loop direct run (no planner) gets a record too, and replays."""
    import asyncio
    import types
    from robot_agent.core.unified_agent import UnifiedAgent
    from fakes import FakeSkillRegistry

    robot = FakeRobot(env, HOUSE)
    ua = UnifiedAgent(skill_registry=R.RecordingSkills(FakeSkillRegistry(robot)),
                      device_manager=types.SimpleNamespace(_ros_node=None))
    mgr = run_control.manager()
    run = mgr.start(goal="move::desk@study room", user_id="alice").run

    async def go():
        async for ev in ua.run_direct(plan=f"move::{DS}\nfind::apple\npick::apple"):
            run.publish(_serialize_result(ev))
    # run_direct's thread binds itself through begin_run(); the record follows the active run
    asyncio.run(go())
    mgr.finish(run.run_id, ok=True, outcome="success")
    assert robot.holding == "apple"
    rec = RP.load(run.run_id, data_dir=data)
    assert rec.context["loop"] == "direct" and len(rec.skills) == 3
    m = R.metrics_of(rec.rows)
    assert m["success"] is True and m["verified_success"] is False      # nothing checked the steps
    res = RP.replay(rec, "exec")
    assert res.identical, res.summary()
    assert RP.replay(rec, "plan").error.startswith("replay plan needs")


def test_no_record_without_a_data_dir(monkeypatch):
    """No AgentState (a CLI, the API tests): runs are not recorded, nothing breaks."""
    monkeypatch.delenv("ROBOT_AGENT_DATA_DIR", raising=False)
    reset_manager()
    try:
        mgr = run_control.manager()
        run = mgr.start(goal="x").run
        assert R.get_open(run.run_id) is None
        run.publish({"event": "status"})
        mgr.finish(run.run_id)
    finally:
        reset_manager()


def test_recording_off(make_assist, env, data, monkeypatch):
    monkeypatch.setenv("ROBOT_AGENT_RECORD", "0")
    loop, _ = _ambiguous_with_replan(make_assist, env)
    record_run(loop, "Put the mug from the desk on the table.")
    assert not (data / "run_records").exists()
