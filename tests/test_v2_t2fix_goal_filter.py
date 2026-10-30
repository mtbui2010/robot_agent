"""T2 fix: understood goals the person's words do not ask for (goal_filter.py): a stated location
("The spoon is in the kitchen drawer. Bring it to the sofa.", T2 H02), a negated object ("but don't
touch the mug", T2 H07), a 'go' to where a move goal goes anyway."""

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

from robot_agent.core.planning import goal_filter as F

DR, DT, DS, SF = "drawer@kitchen", "dining table@kitchen", "desk@study room", "sofa@living room"


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def test_a_stated_location_becomes_the_source(places):
    goals = [G("in", "spoon", "", DR), G("go", "", "", SF), G("on", "spoon", "", SF)]
    out, notes = F.after_grounding(goals, "The spoon is in the kitchen drawer. Bring it to the sofa.", places)
    assert out == [G("on", "spoon", DR, SF)] and len(notes) == 2


def test_a_real_second_destination_is_kept_for_the_validator(places):
    goals = [G("on", "apple", "", DS), G("on", "apple", "", DT)]
    out, notes = F.after_grounding(goals, "Put the apple on the desk and also put the apple on the dining table.",
                                   places)
    assert out == goals and notes == []


def test_a_stated_location_alone_is_left_alone(places):
    goals = [G("in", "spoon", "", DR)]
    assert F.after_grounding(goals, "The spoon is in the kitchen drawer.", places)[0] == goals


def test_a_negated_object_is_not_a_goal():
    goals = [G("on", "apple", "", "dining table"), G("on", "mug", "", "")]
    out, notes = F.before_grounding(goals, "Put the apple on the dining table, but don't touch the mug.")
    assert out == goals[:1] and "mug" in notes[0]
    goals = [G("on", "banana", "dining table", "counter"), G("on", "plate", "dining table", "counter")]
    out, _n = F.before_grounding(goals, "Move everything from the dining table to the counter except the plate.")
    assert [g["object"] for g in out] == ["banana"]


def test_an_object_also_asked_for_is_kept():
    goals = [G("on", "mug", "", "desk")]
    out, _n = F.before_grounding(goals, "Put the mug on the desk, not on the chest.")
    assert out == goals
    assert F.before_grounding([G("on", "mug", "", "")], "Don't touch the mug.")[0] == [G("on", "mug", "", "")]


def test_h02_runs_without_a_question(make_assist, env):
    """H02 replayed: the model read the statement as a goal (+ a 'go'); the robot does not ask."""
    und = {"status": "clear", "reason": "", "question": "",
           "goals": [G("in", "spoon", "", "kitchen drawer"), G("go", "", "", "sofa"), G("on", "spoon", "", "sofa")]}
    plan = steps(f"MoveTo {DR}", f"Open {DR}", "Find spoon", "Pick spoon", f"MoveTo {SF}", f"Place {SF}")
    robot = FakeRobot(env, {"spoon": DR + "/in"}, reply_rules=[{"if": "", "then": ""}])
    evs = run_task(make_assist(robot, FakeLLM(und, default=und), ScriptedPlanner(plan)),
                   "The spoon is in the kitchen drawer. Bring it to the sofa.")
    assert done(evs)["status"] == "success", done(evs)
    u = next(e for e in evs if e["event"] == "understand")
    assert u["goals"] == [G("on", "spoon", DR, SF)] and u["filtered"]
    assert not robot.asked


def test_h07_runs_without_a_question(make_assist, env):
    und = {"status": "clear", "reason": "", "question": "",
           "goals": [G("on", "apple", "", "dining table"), G("on", "mug", "", "")]}
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    robot = FakeRobot(env, {"apple": DS, "mug": DS}, reply_rules=[{"if": "", "then": ""}])
    evs = run_task(make_assist(robot, FakeLLM(und, default=und), ScriptedPlanner(plan)),
                   "Put the apple on the dining table, but don't touch the mug.")
    assert done(evs)["status"] == "success", done(evs)
    assert not robot.asked and not any(s == "pick" and o == "mug" for s, o, *_ in robot.trace)


def test_goal_filter_off(make_assist, env):
    und = {"status": "clear", "reason": "", "question": "",
           "goals": [G("on", "apple", "", "dining table"), G("on", "mug", "", "")]}
    robot = FakeRobot(env, {"apple": DS, "mug": DS}, reply_rules=[{"if": "", "then": ""}])
    evs = run_task(make_assist(robot, FakeLLM(und, default=und), ScriptedPlanner([]), goal_filter=False),
                   "Put the apple on the dining table, but don't touch the mug.")
    assert robot.asked and "mug" in robot.asked[0]


CT = "counter@kitchen"


def test_a_source_made_a_goal_is_dropped(places):
    """M03 / H09 / V09: 'take the spoon out of the drawer' read as a goal 'in spoon -> drawer'."""
    goals = [G("on", "spoon", DR, DT), G("in", "spoon", "", DR), G("close", DR, "", DR)]
    out, notes = F.after_grounding(
        goals, "Take the spoon out of the kitchen drawer, put it on the dining table, then close the drawer.", places)
    assert out == [G("on", "spoon", DR, DT), G("close", DR, "", DR)] and notes
    out, _n = F.after_grounding([G("in", "spoon", "", DR), G("on", "spoon", DR, CT)],
                                "Move the plate to the counter, open the kitchen drawer, take out the spoon and put it "
                                "on the counter.", places)
    assert out == [G("on", "spoon", DR, CT)]


def test_put_back_keeps_both_for_the_validator(places):
    goals = [G("on", "spoon", DR, DT), G("in", "spoon", "", DR)]
    out, _n = F.after_grounding(goals, "Take the spoon out of the drawer, put it on the dining table, then put it "
                                       "back in the drawer.", places)
    assert out == goals


def test_a_stated_location_alone_beside_a_go_is_not_a_request(places):
    """H02 (final T2 run): [in spoon -> drawer, go sofa] was planned and 'succeeded' with the spoon
    back in the drawer (a false success). The stated location is dropped; the read-back sees the spoon in
    no goal."""
    goals = [G("in", "spoon", "", DR), G("go", "", "", SF)]
    out, notes = F.after_grounding(goals, "The spoon is in the kitchen drawer. Bring it to the sofa.", places)
    assert out == [G("go", "", "", SF)] and "not a request" in notes[0]


def test_h02_go_misreading_is_never_a_success(make_assist, env):
    und = {"status": "clear", "reason": "", "question": "",
           "goals": [G("in", "spoon", "", "kitchen drawer"), G("go", "", "", "sofa")]}
    right = {"status": "clear", "reason": "", "question": "",
             "goals": [G("on", "spoon", "kitchen drawer", "sofa")]}
    plan = steps(f"MoveTo {DR}", f"Open {DR}", "Find spoon", "Pick spoon", f"MoveTo {SF}", f"Place {SF}")
    robot = FakeRobot(env, {"spoon": DR + "/in"}, reply_rules=[{"if": "", "then": ""}])
    evs = run_task(make_assist(robot, FakeLLM(und, right, right, default=right), ScriptedPlanner(plan)),
                   "The spoon is in the kitchen drawer. Bring it to the sofa.")
    assert done(evs)["status"] == "success", done(evs)
    ck = next(e for e in evs if e["event"] == "understand_check")
    assert ck["agreed"] and robot.obj["spoon"] == SF
    robot = FakeRobot(env, {"spoon": DR + "/in"}, reply_rules=[{"if": "", "then": ""}])
    evs = run_task(make_assist(robot, FakeLLM(und, default=und), ScriptedPlanner(plan)),
                   "The spoon is in the kitchen drawer. Bring it to the sofa.")
    assert done(evs)["status"] != "success" and robot.asked
