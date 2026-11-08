"""Round 15, option ``reach_delivery`` (default OFF), through the real AssistLoop.run_blocking against a symbolic robot with a
hand-over skill and a scripted LLM and planner. A person entry of the ENV is a snapshot (where they were last seen and the surface
beside them then); the person moves while the robot works. With the option, a put-down for a person whose snapshot is older than
the pack's ``planner.person_snapshot.trust_s`` is the robot's hand-over instead (and when the person has left the room, the robot
asks where they are); a person entry with a position and no declared surface gets a hand-over; the goal check reads the entry again.
Synthetic flat, people and requests written for these tests (shapes of the round-14 false successes: the person was at the desk
when the ENV was built, the robot fetched the object, the person had moved when it came back)."""
import copy

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import persons, plan_repair, reach_delivery
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, AssistLoop, Places

KC, DK, SO, ST = "counter@kitchen", "desk@study", "sofa@living", "side table@living"
ME = "spot near you"                     # the speaker's own ENV key (a made-up one: nothing in the code names it)
ENV = {
    KC: {"aliases": ["counter"], "loc": {"x": 6.0, "y": 1.0}, "surface": True},
    DK: {"aliases": ["desk", "책상"], "loc": {"x": 0.5, "y": 6.0}, "surface": True},
    SO: {"aliases": ["sofa", "couch", "소파"], "loc": {"x": 1.0, "y": 1.0}, "surface": False},
    ST: {"aliases": ["side table"], "loc": {"x": 1.2, "y": 0.4}, "surface": True},
    ME: {"person": "lena", "speaker": True, "aliases": ["me", "lena", "나한테"], "loc": {"x": 0.5, "y": 6.0}, "seat": DK,
         "spot": DK, "surface": False},
}
PLAN = steps(f"MoveTo {KC}", "Find mug", "Pick mug", f"MoveTo {ME}", f"Place {ME}")


def understood(dest="me"):
    return {"status": "clear", "reason": "", "reason_type": "", "question": "",
            "goals": [{"type": "on", "object": "mug", "from": KC, "to": dest}]}


class HomeRobot(FakeRobot):
    """FakeRobot with a clock (every skill but the sensors takes ``dt`` seconds), a person who is at ``person_at`` until
    ``leaves_at`` seconds and at ``person_then`` after, a Place on the person's key (it goes on the spot of the ENV entry), and a
    Handover that reaches a person in the room the robot is in."""

    def __init__(self, *a, person_then=None, leaves_at=40.0, dt=25.0, **k):
        super().__init__(*a, **k)
        self.t, self.dt, self.leaves_at, self.person_then = 0.0, dt, leaves_at, person_then
        self.person_at = DK
        self.calls = []

    def where_person(self):
        return self.person_then if (self.person_then and self.t >= self.leaves_at) else self.person_at

    def room(self, place):
        if place == ME:                              # a move to the person goes to the spot of their (stale) ENV entry
            place = self.env[ME].get("spot") or DK
        return str(place).split("@")[-1]

    def execute(self, skill, params, node=None, log_fn=None):
        if skill not in ("mobile_pose", "grasp_succeed", "ask"):
            self.t += self.dt
            self.calls.append((skill, (params or {}).get("inputs", "")))
        return super().execute(skill, params, node, log_fn)

    def _exec(self, skill, o, params):
        if skill == "handover":
            if not self.holding:
                return {"isdone": False, "msg": "nothing in the gripper"}
            if self.room(self.at) != self.room(self.where_person()):
                return {"isdone": False, "msg": "cannot reach lena: too far for a handover", "code": "unreachable"}
            self.obj[self.holding] = "person:lena"
            self.holding = None
            return {"isdone": True, "msg": "handed"}
        return super()._exec(skill, o, params)


def run(make_assist, env, robot, goal_to="me", plan=PLAN, task="Bring me the mug.", lang="en", **opts):
    opts = dict(search_legs=1, search_rank="prior", ask_help=False, detector_miss_rate=0, **opts)
    loop = make_assist(robot, FakeLLM(understood(goal_to), default="{}"), ScriptedPlanner(plan), **opts)
    loop.clock = lambda: robot.t
    return loop, run_task(loop, task, lang=lang, max_events=3000)


@pytest.fixture
def env(request):
    return copy.deepcopy(getattr(request.function, "ENV", ENV))


@pytest.fixture(autouse=True)
def home_pack(agent_state):
    agent_state.robot_pkg = "fakehome"         # fakebot + a Handover skill + planner.person_snapshot {trust_s: 60}


def lab(loop, why=None):
    return [e for e in loop.lab_log if e["flag"] == "reach_delivery" and (why is None or e["why"] == why)]


def test_option_registered_and_off_by_default(make_assist, monkeypatch, env):
    monkeypatch.delenv("ROBOT_AGENT_REACH_DELIVERY", raising=False)
    assert "reach_delivery" in ASSIST_OPTIONS and {"reach_delivery", "person_snapshot"} <= PLANNER_KEYS
    mk = lambda **kw: make_assist(HomeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["reach_delivery"] is False and mk(reach_delivery="1").cfg["reach_delivery"] is True


def test_033_shape_a_stale_snapshot_is_a_handover(make_assist, env):
    """The person was at the desk when the ENV was built; 70 s later (the fetch) they are on the sofa-bed in the same room. The
    put-down at the desk would be a false success; the hand-over reaches them."""
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    loop, evs = run(make_assist, env, robot, reach_delivery="1")
    assert done(evs)["status"] == "success", done(evs)
    assert robot.obj["mug"] == "person:lena"
    assert [e["age_s"] for e in lab(loop, "stale")] and lab(loop, "stale")[0]["person"] == ME
    assert any(e.get("kind") == "reach_delivery" for e in evs if e["event"] == "recover")
    assert ("handover", ME) in robot.calls and not any(c[0] == "place" for c in robot.calls)


def test_033_shape_without_the_option_the_mug_is_put_on_the_stale_spot_and_the_run_says_done(make_assist, env):
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    loop, evs = run(make_assist, env, robot)
    assert done(evs)["status"] == "success" and robot.obj["mug"] == ME           # the false success of round 14
    assert not loop.lab_log


def test_a_fresh_snapshot_is_trusted_and_the_object_is_put_down(make_assist, env):
    robot = HomeRobot(env, {"mug": KC}, start=KC, dt=3.0, person_then="bed@study", leaves_at=1000.0)
    loop, evs = run(make_assist, env, robot, reach_delivery="1")
    assert done(evs)["status"] == "success" and robot.obj["mug"] == ME and not lab(loop)


def test_a_robot_with_no_handover_action_keeps_the_put_down(make_assist, env, agent_state):
    agent_state.robot_pkg = "fakebot"
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    loop = make_assist(robot, FakeLLM(understood("me"), default="{}"), ScriptedPlanner(PLAN), reach_delivery="1", search_legs=1,
                       search_rank="prior", ask_help=False, detector_miss_rate=0)
    loop.robot.manifest.planner["person_snapshot"] = {"trust_s": 60}
    loop.robot.manifest.planner["action_roles"] = {"put_down": "Place", "move": "MoveTo"}
    loop.clock = lambda: robot.t
    evs = run_task(loop, "Bring me the mug.", max_events=3000)
    assert done(evs)["status"] == "success" and robot.obj["mug"] == ME
    assert [e["why"] for e in lab(loop)] == ["stale_no_handover"] and not any(c[0] == "handover" for c in robot.calls)


def test_047_shape_the_person_left_the_room_the_robot_asks_where_and_hands_over_there(make_assist, env):
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then=SO, leaves_at=40.0,
                      answers=["I'm on the sofa in the living room."])
    loop, evs = run(make_assist, env, robot, reach_delivery="1")
    assert done(evs)["status"] == "success", [e for e in evs if e["event"] in ("step_done", "done")][-3:]
    assert robot.obj["mug"] == "person:lena" and len(robot.asked) == 1
    assert [e["place"] for e in lab(loop, "asked_where")] == [SO]
    assert [c for c in robot.calls if c[0] == "move"][-1] == ("move", SO)


def test_047_shape_in_korean(make_assist, env):
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then=SO, leaves_at=40.0, answers=["거실 소파에 있어요."])
    loop, evs = run(make_assist, env, robot, reach_delivery="1", task="머그잔 나한테 갖다 줘요.", lang="ko", goal_to="나한테")
    assert done(evs)["status"] == "success", [e for e in evs if e["event"] in ("step_done", "done")][-3:]
    assert robot.obj["mug"] == "person:lena" and len(robot.asked) == 1


def test_the_person_cannot_be_found_the_run_does_not_say_done(make_assist, env):
    """The answer names no place: the failure goes the usual way (never a false success)."""
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then=SO, leaves_at=40.0, answers=["somewhere"])
    loop, evs = run(make_assist, env, robot, reach_delivery="1")
    assert done(evs)["status"] != "success" and robot.obj["mug"] != ME


def test_the_question_is_asked_once(make_assist, env):
    """A second failure of the hand-over does not ask again."""
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then=SO, leaves_at=40.0, answers=["somewhere", "somewhere"])
    run(make_assist, env, robot, reach_delivery="1")
    assert len([a for a in robot.asked if "where" in a.lower()]) == 1


# ── a pack that declares nothing, or a robot that cannot hand over: the option does nothing ──
def events_of(evs):
    """What the run did, in order (two runs in one test share the agent state, so the belief the second starts from differs
    in the reconcile event: the steps, their results, the questions and the end are compared)."""
    keep = ("index", "action", "object", "status", "reason", "msg", "question", "say", "kind", "ok", "evidence", "goal")
    return [{k: v for k, v in e.items() if k in keep or k == "event"} for e in evs if e["event"] != "reconcile"]


def test_flag_off_is_the_default_byte_for_byte(make_assist, env):
    a = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    b = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    _l1, e1 = run(make_assist, env, a)
    _l2, e2 = run(make_assist, env, b, reach_delivery="0")
    assert events_of(e1) == events_of(e2) and a.calls == b.calls


def test_a_pack_with_no_declaration_gets_nothing_from_the_option(make_assist, env):
    a = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    b = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    _l1, e1 = run(make_assist, env, a)

    def no_data(loop):
        loop.robot.manifest.planner.pop("person_snapshot", None)
    loop = make_assist(b, FakeLLM(understood("me"), default="{}"), ScriptedPlanner(PLAN), reach_delivery="1", search_legs=1,
                       search_rank="prior", ask_help=False, detector_miss_rate=0)
    no_data(loop)
    loop.clock = lambda: b.t
    e2 = run_task(loop, "Bring me the mug.", max_events=3000)
    assert events_of(e1) == events_of(e2) and a.calls == b.calls and not loop.lab_log


def test_a_made_up_robot_and_language_with_no_data_does_nothing(make_assist, env, agent_state):
    """A pack with no hand-over, no person fields and no declaration, a request in a language the loop has no pack for."""
    agent_state.robot_pkg = "fakebot"
    plain = {k: v for k, v in env.items() if k != ME}
    plain["lena"] = {"aliases": ["lena"], "loc": {"x": 0.5, "y": 6.0}}
    robots = [FakeRobot(plain, {"mug": KC}, start=KC) for _ in range(2)]
    out = []
    for rb, opt in zip(robots, ({}, {"reach_delivery": "1"})):
        loop = make_assist(rb, FakeLLM(understood(DK), default="{}"), ScriptedPlanner(steps(f"MoveTo {KC}", "Find mug", "Pick mug",
                           f"MoveTo {DK}", f"Place {DK}")), search_legs=1, search_rank="prior", ask_help=False,
                           detector_miss_rate=0, **opt)
        out.append((events_of(run_task(loop, "Fetch the mug to the desk.", lang="xx", max_events=3000)), loop.lab_log, rb.trace))
    assert out[0][0] == out[1][0] and out[0][2] == out[1][2] and not out[1][1]


# ── the person has a position and no declared surface ──
def ground(env, lp, goals, task):
    from robot_agent.core.planning.assist_loop import Memory
    return lp._ground_goals(goals, Places(env), Memory("/nonexistent/m.json"), task, latest="")


def bare(robot_has_hand, flag, planner_data=True):
    lp = AssistLoop.__new__(AssistLoop)
    from robot_agent.core.planning.search import SearchConfig

    class R:
        class manifest:
            planner = {"person_snapshot": {"trust_s": 60}, "action_roles": {"put_down": "Place", "move": "MoveTo"}} if planner_data else {}

        plan_actions = {"Place", "MoveTo", "Handover"}

        def release_actions(self):
            return ["Handover"] if robot_has_hand else []
    lp.cfg = {"person_seat_m": 1.0, "person_surface_m": 1.2, "search": SearchConfig(enabled=False), "reach_delivery": flag}
    lp.robot, lp._where_asked = R(), None
    return lp


def test_a_position_without_a_declared_surface_is_a_handover_not_the_nearest_furniture(env):
    e = copy.deepcopy(env)
    e[ME].pop("spot")
    e[ME]["loc"] = {"x": 0.55, "y": 6.0}                 # the desk is 0.05 m away: the old rule would put it there
    task = "Bring me the mug."
    g = [{"type": "on", "object": "mug", "from": KC, "to": "me"}]
    off, _q = ground(e, bare(True, False), copy.deepcopy(g), task)
    on, _q = ground(e, bare(True, True), copy.deepcopy(g), task)
    assert off[0]["to"] == DK and not off[0].get("hand")                              # as before
    assert on[0]["to"] == ME and on[0]["hand"] is True
    nohand, _q = ground(e, bare(False, True), copy.deepcopy(g), task)               # nothing to hand over with: as before
    nodata, _q = ground(e, bare(True, True, planner_data=False), copy.deepcopy(g), task)
    assert nohand[0]["to"] == DK and nodata[0]["to"] == DK


def test_a_declared_spot_is_kept(env):
    on, _q = ground(env, bare(True, True), [{"type": "on", "object": "mug", "from": KC, "to": "me"}], "Bring me the mug.")
    assert on[0]["to"] == ME and not on[0].get("hand")


# ── the goal check reads the entry again ──
def run_with_update(make_assist, env, robot, **opts):
    """The pack updates the person's entry (their reachable surface changes) right after the object was put down."""
    loop = make_assist(robot, FakeLLM(understood("me"), default="{}"), ScriptedPlanner(PLAN), search_legs=1, search_rank="prior",
                       ask_help=False, detector_miss_rate=0, **opts)
    loop.clock = lambda: robot.t
    evs = []
    for ev in loop.run_blocking("Bring me the mug.", lang="en"):
        evs.append({k: v for k, v in ev.items() if k not in ("world", "say_audio")})
        if ev["event"] == "step_done" and ev.get("status") == "success" and robot.obj.get("mug") == ME and env[ME]["spot"] == DK:
            env[ME]["spot"] = ST
        assert len(evs) < 3000
    return loop, evs


def test_the_goal_check_sees_that_the_spot_changed(make_assist, env):
    robot = HomeRobot(env, {"mug": KC}, start=KC, dt=1.0)
    loop, evs = run_with_update(make_assist, env, robot, reach_delivery="1")
    d = done(evs)
    assert d["status"] != "success", d
    assert any(e["event"] == "goal_check" and e["ok"] is False and "not within reach" in e["evidence"] for e in evs)


def test_the_goal_check_is_unchanged_without_the_option(make_assist, env):
    robot = HomeRobot(env, {"mug": KC}, start=KC, dt=1.0)
    _loop, evs = run_with_update(make_assist, env, robot)
    assert done(evs)["status"] == "success"


def test_left_spot_reasons():
    pl = Places({ME: {"person": "p", "spot": ST, "loc": {"x": 0, "y": 0}}, ST: {"loc": {"x": 1, "y": 1}}})
    assert reach_delivery.left_spot(pl, ME, ST) is None and reach_delivery.left_spot(pl, ME, None) is None
    assert "now" in reach_delivery.left_spot(pl, ME, DK)
    pl.env[ME]["spot"] = None
    assert "no reachable surface" in reach_delivery.left_spot(pl, ME, ST)
    del pl.env[ME]["spot"]
    assert reach_delivery.left_spot(pl, ME, ST) is None                      # no such field: nothing to compare


def test_config_reads_only_a_declared_mapping():
    assert reach_delivery.config(None) is None and reach_delivery.config({}) is None
    assert reach_delivery.config({"person_snapshot": {}}) == {"trust_s": None}
    assert reach_delivery.config({"person_snapshot": {"trust_s": "x"}}) == {"trust_s": None}
    assert reach_delivery.config({"person_snapshot": {"trust_s": 30}}) == {"trust_s": 30.0}
    assert reach_delivery.stale({"trust_s": None}, 1e9) is False and reach_delivery.stale({"trust_s": 30.0}, 31) is True
    assert reach_delivery.stale(None, 1e9) is False


# ── the gate: an object the plan holds is not fetched again ──
def _ctx(no_refetch):
    from robot_agent.core.planning import action_model
    from robot_agent.core.planning.base import WorldState
    pl = Places(copy.deepcopy(ENV))
    goals = [{"type": "on", "object": "mug", "from": KC, "to": ME}, {"type": "go", "object": "", "from": "", "to": KC}]
    return pl, goals, WorldState()


def test_033_shape_the_gate_does_not_walk_back_to_the_source_of_a_held_object():
    pl, goals, world = _ctx(False)
    plan = steps(f"MoveTo {KC}", "Find mug", "Pick mug", f"MoveTo {ME}", f"MoveTo {DK}")
    reached = lambda st, w, g: any(s["action"] == "Place" for s in st)       # noqa: E731
    out = {}
    for flag in (False, True):
        F, _n, _c = plan_repair.repair(plan, lambda s: 0 if any(x["action"] == "Place" for x in s) else 1, world, pl, goals,
                                       caps=None, actions=["MoveTo", "Find", "Pick", "Place"], complete=True, reached=reached,
                                       no_refetch=flag)
        out[flag] = [f"{s['action']} {s['object']}" for s in F]
    assert out[False][-5:] == [f"MoveTo {KC}", "Find mug", "Pick mug", f"MoveTo {ME}", f"Place {ME}"]
    assert out[True][-2:] == [f"MoveTo {ME}", f"Place {ME}"] and out[True].count(f"MoveTo {KC}") == 1


# ── two objects for the same person who moved (047 shape): ask once, reuse the answered place ──
PLAN2 = steps(f"MoveTo {KC}", "Find mug", "Pick mug", f"MoveTo {ME}", f"Place {ME}",
              f"MoveTo {KC}", "Find book", "Pick book", f"MoveTo {ME}", f"Place {ME}")


def understood2(dest="me"):
    u = understood(dest)
    u["goals"] = u["goals"] + [{"type": "on", "object": "book", "from": KC, "to": dest}]
    return u


def run2(make_assist, env, robot, lang="en", dest="me", task="Bring me the mug and the book.", **opts):
    opts = dict(search_legs=1, search_rank="prior", ask_help=False, detector_miss_rate=0, **opts)
    loop = make_assist(robot, FakeLLM(understood2(dest), default="{}"), ScriptedPlanner(PLAN2), **opts)
    loop.clock = lambda: robot.t
    return loop, run_task(loop, task, lang=lang, max_events=4000)


def failed_handovers(robot):
    return [t for t in robot.trace if t[0] == "handover" and not t[2]]


@pytest.mark.parametrize("lang,ans,dest,task", [
    ("en", "I'm on the sofa in the living room.", "me", "Bring me the mug and the book."),
    ("ko", "거실 소파에 있어요.", "나한테", "머그잔이랑 책 나한테 갖다 줘요."),
])
def test_047_shape_two_objects_one_question_and_no_repeated_unreachable_handovers(make_assist, env, lang, ans, dest, task):
    robot = HomeRobot(env, {"mug": KC, "book": KC}, start=KC, person_then=SO, leaves_at=40.0, answers=[ans, ans])
    loop, evs = run2(make_assist, env, robot, lang=lang, dest=dest, task=task, reach_delivery="1")
    assert done(evs)["status"] == "success", [e for e in evs if e["event"] in ("step_done", "done")][-3:]
    assert robot.obj["mug"] == "person:lena" and robot.obj["book"] == "person:lena"
    assert len(robot.asked) == 1, robot.asked
    assert len(failed_handovers(robot)) == 2            # one per object: never the same hand-over three times in a row
    assert [e["place"] for e in lab(loop, "asked_where")] == [SO]
    assert [e["place"] for e in lab(loop, "known_where")] == [SO]


def test_unanswered_where_does_not_retry_the_handover_in_place(make_assist, env):
    robot = HomeRobot(env, {"mug": KC, "book": KC}, start=KC, person_then=SO, leaves_at=40.0, answers=["somewhere", "somewhere"])
    loop, evs = run2(make_assist, env, robot, reach_delivery="1")
    assert done(evs)["status"] != "success"
    assert len(failed_handovers(robot)) == 1 and len(robot.asked) == 1


def test_two_objects_flag_off_is_identical_to_reach_delivery_zero(make_assist, env):
    a = HomeRobot(env, {"mug": KC, "book": KC}, start=KC, person_then=SO, leaves_at=40.0)
    b = HomeRobot(env, {"mug": KC, "book": KC}, start=KC, person_then=SO, leaves_at=40.0)
    _l1, e1 = run2(make_assist, env, a)
    _l2, e2 = run2(make_assist, env, b, reach_delivery="0")
    assert events_of(e1) == events_of(e2) and a.calls == b.calls
