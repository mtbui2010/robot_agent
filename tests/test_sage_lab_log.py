"""``AssistLoop.lab_log``: what the opt-in lab options did in a run (flag name plus a few facts), read by a harness after
the run. Through the real AssistLoop.run_blocking against the symbolic FakeRobot and a scripted LLM; synthetic requests
written for these tests. Off options leave the log empty; the event stream is not touched."""
from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import AssistLoop

DT, DS, DR = "dining table@kitchen", "desk@study room", "drawer@kitchen"
def U(*goals, status="clear", question=""):
    return {"status": status, "reason": "", "question": question,
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def flags(loop):
    return [e["flag"] for e in loop.lab_log]


def play(make_assist, robot, llm, plan, task, **opts):
    loop = make_assist(robot, llm, plan, health_wait_s=0, **opts)
    return loop, run_task(loop, task)


# ── execution integrity ──
BACK = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}", f"MoveTo {DS}")
APPLE_BACK = U(("on", "apple", "desk", "dining table"), ("go", "", "", "desk"))


def test_endstate_check_logs_the_checked_return(make_assist, env):
    fault = [{"skill": "move", "obj": DS, "kind": "fail", "nth": [3], "msg": "move failed: motor stalled"}]
    loop, evs = play(make_assist, FakeRobot(env, {"apple": DS}, faults=fault), FakeLLM(APPLE_BACK),
                     ScriptedPlanner(BACK, replans=[[]]), "Bring the apple from the desk to the dining table, then go back.",
                     endstate_check="1")
    assert done(evs)["status"] == "success"
    assert loop.lab_log == [{"flag": "endstate_check", "place": DS, "ok": True, "tries": 2}]


def test_endstate_check_logs_a_return_that_keeps_failing(make_assist, env):
    fault = [{"skill": "move", "obj": DS, "kind": "fail", "nth": [3, 4, 5], "msg": "move failed: motor stalled"}]
    loop, evs = play(make_assist, FakeRobot(env, {"apple": DS}, faults=fault), FakeLLM(APPLE_BACK),
                     ScriptedPlanner(BACK, replans=[[]]), "Bring the apple from the desk to the dining table, then go back.",
                     endstate_check="1")
    assert loop.lab_log[-1] == {"flag": "endstate_check", "place": DS, "ok": False, "tries": 3}


def test_off_options_leave_the_log_empty(make_assist, env):
    fault = [{"skill": "move", "obj": DS, "kind": "fail", "nth": [3], "msg": "move failed: motor stalled"}]
    loop, evs = play(make_assist, FakeRobot(env, {"apple": DS}, faults=fault), FakeLLM(APPLE_BACK),
                     ScriptedPlanner(BACK, replans=[[]]), "Bring the apple from the desk to the dining table, then go back.")
    assert loop.lab_log == []


def test_reset_container_logs_the_reset(make_assist, env):
    blocked = [{"skill": "place", "obj": DR, "kind": "fail", "nth": 1, "msg": "blocked: no free spot in the drawer"}]
    plan = steps(f"MoveTo {DT}", "Find orange", "Pick orange", f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}")
    loop, evs = play(make_assist, FakeRobot(env, {"orange": DT}, faults=blocked),
                     FakeLLM(U(("in", "orange", "dining table", "kitchen drawer"))), ScriptedPlanner(plan, replans=[[]]),
                     "Put the orange in the kitchen drawer.", reset_container="1")
    assert loop.lab_log == [{"flag": "reset_container", "container": DR, "held": "orange", "source": DT}]


def test_wait_on_robot_logs_the_schedule(make_assist, env, monkeypatch):
    from robot_agent.core.planning.robot import RobotView

    class WaitRobot(FakeRobot):
        def _exec(self, skill, o, params):
            return {"isdone": True} if skill == "wait" else super()._exec(skill, o, params)
    monkeypatch.setattr(RobotView, "health", lambda self, node=None: {"ok": None})
    fault = [{"skill": "find", "obj": "apple", "kind": "fail", "nth": [1], "msg": "find: no image (camera timeout 1 Hz)"}]
    robot = WaitRobot(env, {"apple": DS}, faults=fault)
    loop = make_assist(robot, FakeLLM(U(("on", "apple", "desk", "dining table"))),
                       ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")),
                       health_wait_s=0, wait_on_robot="1")
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {},
                                       recovery={"wait": {"action": "Wait", "schedule_s": [2, 4, 8]}})
    orig = AssistLoop._bind
    loop._bind = lambda st, world: (("wait", {"inputs": st["object"]}) if st["action"] == "Wait" else orig(loop, st, world))
    run_task(loop, "Bring the apple from the desk to the dining table.")
    assert loop.lab_log == [{"flag": "wait_on_robot", "action": "Wait", "schedule_s": [2.0, 4.0, 8.0],
                             "waited_s": [2.0, 4.0, 8.0], "health_ok": None}]


def test_gate_strict_logs_the_bind_and_the_per_goal_repair(make_assist, env, places):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), gate_strict="1")
    goals = [{"type": "on", "object": "big red book", "from": "", "to": DT}]
    loop._ground_step({"action": "Find", "object": "red book"}, places, goals, "")
    assert loop.lab_log == [{"flag": "gate_strict", "part": "bind_subset", "frm": "red book", "to": "big red book"}]

    import types
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), gate_strict="1")
    r = types.SimpleNamespace(steps=["pre"], planner=None, repaired_by_op={})
    loop._gate_repair_run = lambda rr, per_goal=False: (setattr(rr, "steps", ["g" if per_goal else "f"]) or [])
    loop._blocking = lambda rr, st, per_goal=False: [] if st == ["g"] else ["bad"]
    loop._gate_repair(r)
    assert loop.lab_log == [{"flag": "gate_strict", "part": "per_goal_repair", "kept": True}]
