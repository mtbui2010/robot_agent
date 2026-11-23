"""Round 16: the numbers and action names of the round 12-15 options are pack data (manifest ``planner``), validated at load,
and a part whose data is missing does nothing (fail open). Synthetic ENV and requests written for these tests."""
import copy

import pytest

from fakes import FakeLLM, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.manifest import PLANNER_KEYS, Manifest, ManifestError
from robot_agent.core.planning import action_roles, dialog_ext as DX, request_coverage
from robot_agent.options import reach_delivery
from robot_agent.core.planning.assist_loop import Places
from test_sage_lab_r14_relook import FR, KC as KC14, MissRobot, understood as understood14
from test_sage_lab_r14_relook import ENV as ENV14
from test_sage_lab_r15_reach_delivery import ENV as ENV15, KC, ME, HomeRobot, lab, run


# ── manifest validation ──
def planner(**kw):
    return Manifest.from_dict({"robot": "toy", "planner": kw}).planner


def bad(**kw):
    with pytest.raises(ManifestError) as e:
        Manifest.from_dict({"robot": "toy", "planner": kw})
    return " ".join(e.value.errors)


def test_new_keys_are_known_and_good_values_load():
    assert {"action_roles", "coverage_limits", "agree_guard", "person_snapshot"} <= PLANNER_KEYS
    p = planner(agree_guard={"garble_ratio": 0.6, "particle_chain": 2, "tail_chars": 2}, person_snapshot={"trust_s": 60},
                action_roles={"put_down": "Place", "move": "MoveTo", "open": "Open", "close": "Close"},
                coverage_limits={"max_parts": 2, "max_steps": 40}, room_names={"ko": {"kitchen": ["주방"]}})
    assert p["agree_guard"]["tail_chars"] == 2 and p["action_roles"]["close"] == "Close"
    assert planner() == {} and planner(agree_guard={}, person_snapshot={}, action_roles={}) != {}


@pytest.mark.parametrize("kw,what", [
    ({"agree_guard": {"garble_ratio": 0}}, "garble_ratio"), ({"agree_guard": {"garble_ratio": 1.5}}, "garble_ratio"),
    ({"agree_guard": {"garble_ratio": "x"}}, "garble_ratio"), ({"agree_guard": {"particle_chain": 0}}, "particle_chain"),
    ({"agree_guard": {"tail_chars": True}}, "tail_chars"), ({"agree_guard": {"ratio": 0.5}}, "unknown keys"),
    ({"agree_guard": 0.6}, "mapping"), ({"person_snapshot": {"trust_s": 0}}, "trust_s"),
    ({"person_snapshot": {"trust_s": "60"}}, "trust_s"), ({"person_snapshot": {"trust": 60}}, "unknown keys"),
    ({"person_snapshot": [60]}, "mapping"), ({"action_roles": {"put_down": ""}}, "put_down"),
    ({"action_roles": {"lift": "Lift"}}, "unknown keys"), ({"action_roles": {"move": 3}}, "move"),
    ({"coverage_limits": {"max_parts": 0}}, "max_parts"), ({"coverage_limits": {"max_steps": 1.5}}, "max_steps"),
    ({"coverage_limits": {"cap": 1}}, "unknown keys"), ({"room_names": {"ko": ["주방"]}}, "room_names"),
    ({"room_names": {"ko": {"kitchen": "주방"}}}, "room_names")])
def test_bad_values_are_named_at_load(kw, what):
    assert what in bad(**kw)


# ── action roles ──
def test_a_role_is_the_packs_action_or_none():
    pl = {"action_roles": {"put_down": "Place", "move": "GoTo", "bogus": "X", "open": " "}}
    assert action_roles.declared(pl) == {"put_down": "Place", "move": "GoTo"}
    assert action_roles.action_for(pl, "put_down") == "Place" and action_roles.action_for(pl, "close") is None
    assert action_roles.action_for(pl, "move", {"MoveTo"}) is None and action_roles.action_for(pl, "move", {"GoTo"}) == "GoTo"
    for none in (None, {}, {"action_roles": "Place"}, {"action_roles": []}):
        assert action_roles.action_for(none, "put_down") is None


def test_a_pack_with_another_name_for_the_put_down():
    places = Places(copy.deepcopy(ENV15))
    st, goals = {"action": "Set", "object": ME}, []
    assert reach_delivery.recipient(places, st, goals, "Set") == ME
    assert reach_delivery.recipient(places, st, goals, "Place") is None and reach_delivery.recipient(places, st, goals) is None
    assert reach_delivery.recipient(places, {"action": "Place", "object": ME}, goals) is None       # no role: never a put-down


@pytest.fixture
def home(agent_state):
    agent_state.robot_pkg = "fakehome"


@pytest.fixture
def env(request):                                   # the ENV make_assist builds the loop on: the test's own ``ENV``
    return copy.deepcopy(getattr(request.function, "ENV", ENV15))


def test_reach_delivery_needs_the_declared_put_down_role(make_assist, home, monkeypatch):
    """With the role the stale put-down is a hand-over (as in round 15); without it the option has nothing to recognise."""
    env = copy.deepcopy(ENV15)
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    loop, evs = run(make_assist, env, robot, reach_delivery="1")
    assert done(evs)["status"] == "success" and lab(loop, "stale")
    monkeypatch.setattr(action_roles, "KEY", "not_declared")
    robot = HomeRobot(env, {"mug": KC}, start=KC, person_then="bed@study", leaves_at=40.0)
    loop, evs = run(make_assist, env, robot, reach_delivery="1")
    assert not loop.lab_log and not any(c[0] == "handover" for c in robot.calls) and robot.obj["mug"] == ME


# ── relook reopens with the pack's close / open ──
def relook_run(make_assist, roles):
    plan = steps(f"MoveTo {FR}", f"Open {FR}", "Find mug", "Pick mug", f"MoveTo {KC14}", f"Place {KC14}")
    robot = MissRobot(copy.deepcopy(ENV14), {"mug": FR + "/in"}, miss_at=FR, misses=2, opened=())
    robot.env = ENV14
    loop = make_assist(robot, FakeLLM(understood14(FR), default="{}"), ScriptedPlanner(plan), search_legs=1, search_rank="prior",
                       search_max=2, search_total=6, ask_help=False, relook="1")
    loop.robot.manifest.planner["action_roles"] = roles
    run_task(loop, "Take the mug from the fridge to the counter.", max_events=3000)
    return [e for e in loop.lab_log if e["flag"] == "relook"]


@pytest.mark.parametrize("roles,sense", [
    ({"open": "Open", "close": "Close"}, "reopen"), ({"open": "Open"}, "again"),       # half a declaration: no reopen
    ({}, "again"), ({"open": "Unlatch", "close": "Latch"}, "again")])                 # not the robot's own actions
def test_relook_reopens_only_with_declared_roles(make_assist, roles, sense):
    log = relook_run(make_assist, roles)
    assert log and log[0]["sense"] == sense


test_relook_reopens_only_with_declared_roles.ENV = ENV14


# ── the caps of request_coverage ──
def test_coverage_limits_come_from_the_pack():
    assert request_coverage.limits({"coverage_limits": {"max_parts": 2, "max_steps": 40}}) == {"max_parts": 2, "max_steps": 40}
    assert request_coverage.limits({"coverage_limits": {"max_parts": 0, "max_steps": True, "x": 3}}) == {}
    assert request_coverage.limits(None) == {} and request_coverage.limits({"coverage_limits": [1]}) == {}
    assert request_coverage.parts({"unserved": [{"part": "a b"}, {"part": "c d"}, {"part": "e f"}]}, "a b c d e f") == ["a b", "c d", "e f"]


# ── the guard's numbers ──
PL = Places({"fridge@kitchen": {"aliases": ["fridge", "냉장고"], "loc": {"x": 1, "y": 1}}})


def test_guard_numbers_are_the_manifests_and_a_missing_one_turns_its_part_off():
    ext = DX.build("ko", PL)
    full = DX.guard_for("ko", PL, [], {"agree_guard": {"garble_ratio": 0.6, "particle_chain": 2, "tail_chars": 2}}, ext)
    assert full._tail is not None and full.tail_chars == 2 and full._exact("냉장고에")
    assert "%d}" % 2 in full._tail.pattern
    one = DX.guard_for("ko", PL, [], {"agree_guard": {"garble_ratio": 0.6, "particle_chain": 1, "tail_chars": 1}}, ext)
    assert "{1,1}" in one._tail.pattern and one.tail_chars == 1
    bare = DX.guard_for("ko", PL, [], {"agree_guard": {"garble_ratio": 0.6}}, ext)
    assert bare._tail is None and bare.tail_chars == 0 and not bare._exact("냉장고에") and bare._exact("냉장고")
    assert bare.garbled(["냉장고에 넣어 줘"]) == []                      # nothing declared: no tail judged, nothing garbled
    assert DX.guard_for("ko", PL, [], {"agree_guard": {"garble_ratio": 0.6, "particle_chain": 0, "tail_chars": "2"}}, ext)._tail is None


def test_script_share_is_the_packs_number(monkeypatch):
    from robot_agent.core.planning import langdata
    g = DX.guard_for("ko", PL, [], {"agree_guard": {"garble_ratio": 0.6}}, DX.build("ko", PL))
    mixed = "가나 abcdefg hijk"
    assert g.plausible("냉장고에 넣어 줘") and not g.plausible(mixed)             # ko.json declares min_share
    pack = dict(langdata.get("ko"), script={k: v for k, v in langdata.get("ko")["script"].items() if k != "min_share"})
    monkeypatch.setitem(langdata._CACHE, "ko", pack)
    assert g.plausible(mixed)                                                # no share declared: no share test
    assert not g.plausible("냉장고 ねこ")                                    # the other-script test stays
