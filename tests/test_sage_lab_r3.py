"""Solution lab round 3 (all flags default OFF): execution integrity (``gate_strict``, ``endstate_check``,
``reset_container``, ``wait_on_robot``, family ``exec_integrity``) and ``inventory_ask`` (name_canon made general),
through the real AssistLoop.run_blocking against the symbolic FakeRobot and a scripted LLM. Synthetic requests written
for these tests (no benchmark text). Off = the earlier events, byte for byte."""
import json
import time
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import exec_integrity as EI, goal_fix
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, AssistLoop

DR, DT, DS = "drawer@kitchen", "dining table@kitchen", "desk@study room"
HOUSE = {"apple": DS, "orange": DT}
FLAGS = EI.FLAGS + ("exec_integrity", "inventory_ask")


def U(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


def dump(evs):
    return json.dumps(scrub(evs), sort_keys=True, default=str)


# ── option plumbing ──
def test_flags_default_off_and_registered(make_assist, monkeypatch, env):
    for k in FLAGS:
        monkeypatch.delenv("ROBOT_AGENT_" + k.upper(), raising=False)
        assert k in ASSIST_OPTIONS and k in PLANNER_KEYS
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert all(mk().cfg[k] is False for k in EI.FLAGS + ("inventory_ask",))
    assert all(mk(**{k: "1"}).cfg[k] is True for k in EI.FLAGS + ("inventory_ask",))
    fam = mk(exec_integrity="1").cfg
    assert all(fam[k] is True for k in EI.FLAGS) and fam["inventory_ask"] is False
    assert mk(exec_integrity="1", wait_on_robot="0").cfg["wait_on_robot"] is True      # the family wins; each flag adds
    monkeypatch.setenv("ROBOT_AGENT_ENDSTATE_CHECK", "1")
    assert mk().cfg["endstate_check"] is True and mk(endstate_check="0").cfg["endstate_check"] is False


def test_manifest_validates_the_new_planner_keys():
    from robot_agent.contract.manifest import _planner
    errs = []
    _planner({"recovery": {"wait": {"action": "Wait", "schedule_s": [2, 4, 8]}}, "canonical_lang": "en",
              "label_tokens": "none"}, errs)
    assert errs == []
    for bad in ({"recovery": {"wait": {"action": "Wait", "schedule_s": []}}}, {"recovery": {"wait": {"schedule_s": [1]}}},
                {"recovery": {"wait": {"action": "Wait", "schedule_s": [0, -1]}}}, {"recovery": "x"},
                {"canonical_lang": ""}):
        errs = []
        _planner(bad, errs)
        assert errs, bad


# ── pure helpers ──
def test_per_goal_findings_counts_each_unmet_goal():
    f = [EI.MISSING_PREFIX + "A; B; C", "other"]
    assert EI.per_goal_findings(f, 3) == ["other", "unmet goal 1", "unmet goal 2", "unmet goal 3"]
    assert EI.per_goal_findings(f, 0)[-1] == "unmet goal 1"
    assert EI.per_goal_findings(["other"], 2) == ["other"]


@pytest.mark.parametrize("obj,goals,tokens,out", [
    ("red book", ["red book"], "words", "red book"),
    ("book", ["big red book"], "words", "big red book"),
    ("red book", ["big red book", "blue cup"], "words", "big red book"),
    ("red book", ["big red book", "tall red book"], "words", "red book"),     # two fit: no guess
    ("red book", ["big red book"], "none", "red book"),                      # a pack whose labels have no word breaks
    ("mug", ["blue cup"], "words", "mug"),
])
def test_bind_subset(obj, goals, tokens, out):
    assert EI.bind_subset(obj, goals, tokens) == out


def test_wait_schedule_reads_only_a_valid_declaration():
    assert EI.wait_schedule({"action": "Wait", "schedule_s": [2, 4]}) == ("Wait", [2.0, 4.0])
    assert EI.wait_schedule(None) == (None, [])
    assert EI.wait_schedule({"action": "Wait", "schedule_s": ["x"]}) == (None, [])
    assert EI.wait_schedule({"schedule_s": [1]}) == (None, [])


def test_bind_subset_in_the_loops_ground_step(make_assist, env, places):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), gate_strict="1")
    goals = [{"type": "on", "object": "big red book", "from": "", "to": DT}]
    st = loop._ground_step({"action": "Find", "object": "red book"}, places, goals, "")
    assert st["object"] == "big red book"
    off = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]))
    assert off._ground_step({"action": "Find", "object": "red book"}, places, goals, "")["object"] == "red book"


# ── gate_strict: the repair keeps the per-goal result only if it certifies ──
def _strict_probe(make_assist, env, second_certifies):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), gate_strict="1")
    r = types.SimpleNamespace(steps=["pre"], planner=None, repaired_by_op={})
    calls = []

    def run(rr, per_goal=False):
        calls.append(per_goal)
        rr.steps = ["per-goal"] if per_goal else ["first"]
        return ["note2"] if per_goal else ["note1"]
    loop._gate_repair_run = run
    loop._blocking = lambda rr, steps, per_goal=False: [] if (second_certifies and steps == ["per-goal"]) else ["bad"]
    return loop, r, calls


def test_gate_strict_adopts_a_certifying_per_goal_result(make_assist, env):
    loop, r, calls = _strict_probe(make_assist, env, True)
    notes = loop._gate_repair(r)
    assert calls == [False, True] and r.steps == ["per-goal"] and notes[0] == "note2"


def test_gate_strict_keeps_todays_result_when_it_does_not_certify(make_assist, env):
    loop, r, calls = _strict_probe(make_assist, env, False)
    assert loop._gate_repair(r) == ["note1"] and calls == [False, True] and r.steps == ["first"]


def test_gate_strict_is_one_run_when_todays_repair_certifies(make_assist, env):
    loop, r, calls = _strict_probe(make_assist, env, True)
    loop._blocking = lambda rr, steps, per_goal=False: []
    assert loop._gate_repair(r) == ["note1"] and calls == [False]


# ── endstate_check ──
APPLE_BACK = U(("on", "apple", "desk", "dining table"), ("go", "", "", "desk"))
BACK_PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}", f"MoveTo {DS}")


def _back(make_assist, env, nth, **opts):
    fault = [{"skill": "move", "obj": DS, "kind": "fail", "nth": nth, "msg": "move failed: motor stalled"}]
    robot = FakeRobot(env, HOUSE, faults=fault)
    evs = run_task(make_assist(robot, FakeLLM(APPLE_BACK), ScriptedPlanner(BACK_PLAN, replans=[[]]), health_wait_s=0, **opts),
                   "Bring the apple from the desk to the dining table, then go back to the desk.")
    return evs, robot


def test_endstate_check_off_ignores_the_failed_return(make_assist, env):
    evs, robot = _back(make_assist, env, [3, 4, 5])
    assert done(evs)["status"] == "success"
    assert [s for s, *_ in robot.trace].count("move") == 5 and robot.at == DT         # the return was tried once, it failed


def test_endstate_check_retries_the_return_then_succeeds(make_assist, env):
    evs, robot = _back(make_assist, env, [3], endstate_check="1")
    assert done(evs)["status"] == "success" and robot.at == DS


def test_endstate_check_a_return_that_keeps_failing_is_not_done(make_assist, env):
    evs, robot = _back(make_assist, env, [3, 4, 5], endstate_check="1")
    assert done(evs)["status"] != "success" and [s for s, *_ in robot.trace].count("move") == 7


# ── reset_container ──
IN_DRAWER = U(("in", "orange", "dining table", "kitchen drawer"))
DRAWER_PLAN = steps(f"MoveTo {DT}", "Find orange", "Pick orange", f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}")
BLOCKED = [{"skill": "place", "obj": DR, "kind": "fail", "nth": 1, "msg": "blocked: no free spot in the drawer"}]


def _drawer(make_assist, env, faults=BLOCKED, **opts):
    robot = FakeRobot(env, HOUSE, faults=faults)
    loop = make_assist(robot, FakeLLM(IN_DRAWER), ScriptedPlanner(DRAWER_PLAN, replans=[[]]), health_wait_s=0, **opts)
    return run_task(loop, "Put the orange in the kitchen drawer."), robot


def test_reset_container_puts_down_closes_opens_and_tries_again(make_assist, env):
    evs, robot = _drawer(make_assist, env, reset_container="1")
    assert done(evs)["status"] == "success" and robot.obj["orange"] == DR + "/in"
    assert [e["kind"] for e in evs if e["event"] == "recover"] == ["reset_container"]
    sk = [s for s, *_ in robot.trace]
    assert "close_drawer" in sk and sk.count("open_drawer") == 2 and sk.count("pick") == 2


def test_reset_container_off_does_not_close_the_drawer(make_assist, env):
    evs, robot = _drawer(make_assist, env)
    assert "close_drawer" not in [s for s, *_ in robot.trace]
    assert "reset_container" not in [e.get("kind") for e in evs if e["event"] == "recover"]


def test_reset_container_only_once_per_container(make_assist, env):
    always = [{"skill": "place", "obj": DR, "kind": "fail", "nth": "all", "msg": "blocked: no free spot"}]
    evs, robot = _drawer(make_assist, env, faults=always, reset_container="1")
    assert [e["kind"] for e in evs if e["event"] == "recover"].count("reset_container") == 1
    assert done(evs)["status"] != "success"


def test_reset_container_ignores_other_failure_codes(make_assist, env):
    other = [{"skill": "place", "obj": DR, "kind": "fail", "nth": 1, "msg": "gripper timeout"}]
    evs, _ = _drawer(make_assist, env, faults=other, reset_container="1")
    assert "reset_container" not in [e.get("kind") for e in evs if e["event"] == "recover"]


# ── wait_on_robot ──
class WaitRobot(FakeRobot):
    def _exec(self, skill, o, params):
        if skill == "wait":
            return {"isdone": True}
        return super()._exec(skill, o, params)


def _camera(make_assist, env, monkeypatch, declare, **opts):
    from robot_agent.core.planning.robot import RobotView
    monkeypatch.setattr(RobotView, "health", lambda self, node=None: {"ok": None})
    robot = WaitRobot(env, {"apple": DS}, faults=[{"skill": "find", "obj": "apple", "kind": "fail", "nth": [1],
                                                    "msg": "find: no image (camera timeout 1 Hz)"}])
    loop = make_assist(robot, FakeLLM(U(("on", "apple", "desk", "dining table"))),
                       ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")),
                       health_wait_s=0, **opts)
    if declare:
        loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, recovery={"wait": declare})
        orig = AssistLoop._bind
        loop._bind = lambda st, world: (("wait", {"inputs": st["object"]}) if st["action"] == "Wait" else orig(loop, st, world))
    return run_task(loop, "Bring the apple from the desk to the dining table."), robot


def test_wait_on_robot_runs_the_declared_schedule(make_assist, env, monkeypatch):
    evs, robot = _camera(make_assist, env, monkeypatch, {"action": "Wait", "schedule_s": [2, 4, 8]}, wait_on_robot="1")
    assert done(evs)["status"] == "success"
    assert [o for s, o, *_ in robot.trace if s == "wait"] == ["2", "4", "8"]


def test_wait_on_robot_without_a_declaration_keeps_the_poll(make_assist, env, monkeypatch):
    evs, robot = _camera(make_assist, env, monkeypatch, None, wait_on_robot="1")
    assert done(evs)["status"] == "success" and "wait" not in [s for s, *_ in robot.trace]


def test_wait_on_robot_off_does_not_wait_on_the_robot(make_assist, env, monkeypatch):
    evs, robot = _camera(make_assist, env, monkeypatch, {"action": "Wait", "schedule_s": [2]})
    assert done(evs)["status"] == "success" and "wait" not in [s for s, *_ in robot.trace]


# ── the family on, nothing to fix = the events of the flags off ──
def test_family_on_changes_nothing_on_a_clean_run(make_assist, env):
    def go(**o):
        robot = FakeRobot(env, HOUSE)
        loop = make_assist(robot, FakeLLM(U(("on", "apple", "desk", "dining table"))),
                           ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")), **o)
        return run_task(loop, "Bring the apple from the desk to the dining table."), robot
    (a, ra), (b, rb) = go(), go(exec_integrity="1", inventory_ask="1")
    assert dump(a) == dump(b) and ra.trace == rb.trace


# ── inventory_ask ──
MEM = {n: {"place": DS, "ts": time.time(), "how": "seen"} for n in ("blue mug", "red mug", "red book")}


def _inv(make_assist, env, tmp_path, task, obj="mug", memory=MEM, answers=(), **opts):
    (tmp_path / "object_memory.json").write_text(json.dumps({"objects": memory}))
    robot = FakeRobot(env, {"red mug": DS}, answers=answers)
    loop = make_assist(robot, FakeLLM(U(("on", obj, "", DT))), ScriptedPlanner(steps(f"MoveTo {DS}")), **opts)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task)]
    return next(e for e in evs if e["event"] == "understand")


def test_inventory_ask_asks_which_when_two_instances_share_a_place(make_assist, env, tmp_path):
    u = _inv(make_assist, env, tmp_path, "Put the mug on the dining table.", inventory_ask="1", confirm_task=False, answers=["the red mug"])
    assert "more than one mug" in u["question"] and "blue mug" in u["question"] and "red mug" in u["question"]


def test_name_canon_alone_still_picks_silently_at_one_place(make_assist, env, tmp_path):
    u = _inv(make_assist, env, tmp_path, "Put the mug on the dining table.", name_canon="1", confirm_task=False)
    assert not u["question"] and u["goals"][0]["object"] in ("blue mug", "red mug")


def test_inventory_ask_uses_the_users_words_first(make_assist, env, tmp_path):
    u = _inv(make_assist, env, tmp_path, "Put the blue mug on the dining table.", inventory_ask="1", confirm_task=False)
    assert not u["question"] and u["goals"][0]["object"] == "blue mug" and u["goals"][0]["said"] == "mug"


def test_inventory_ask_one_instance_is_renamed_without_a_question(make_assist, env, tmp_path):
    u = _inv(make_assist, env, tmp_path, "Put the books on the dining table.", obj="books", inventory_ask="1", confirm_task=False)
    assert not u["question"] and u["goals"][0]["object"] == "red book"


def test_inventory_ask_off_is_the_earlier_reading(make_assist, env, tmp_path):
    u = _inv(make_assist, env, tmp_path, "Put the mug on the dining table.", confirm_task=False)
    assert not u["question"] and u["goals"][0]["object"] == "mug"


def test_inventory_ask_for_a_request_in_a_third_language_asks(make_assist, env, tmp_path):
    u = _inv(make_assist, env, tmp_path, "Đặt cái cốc lên bàn ăn", inventory_ask="1", confirm_task=False, answers=["cái màu xanh"])
    assert "more than one mug" in u["question"]


class _Pack:                                  # a pack that folds a plural of its own canonical language
    i18n = types.SimpleNamespace(phrases={"plural_fold": {"de": "n>"}})


ENV = {DT: {"aliases": ["table"], "loc": {"x": 3.0, "y": 1.0}}, DS: {"aliases": ["desk"], "loc": {"x": 1.0, "y": 1.0}}}


def test_canon_inventory_pack_data_for_another_canonical_language():
    from robot_agent.core.planning.assist_loop import Places
    pl = Places(ENV)
    mem = {"tasse": {"place": DS}, "Tasse-Blau": {"place": DS}}
    goals = [{"type": "on", "object": "tassen", "from": "", "to": DT}]
    rules = goal_fix.fold_rules(_Pack(), "de")
    out, which, _ = goal_fix.canon_inventory(goals, mem, pl, "de", rules)
    assert out[0]["object"] == "tasse" and which is None                      # non-ASCII-safe: no script filter for "de"
    assert goal_fix.canon_inventory(goals, mem, pl, "de", [])[0] == goals     # no fold data: exact or head match only
    assert goal_fix.canon_inventory(goals, mem, pl, "none") == (goals, None, [])   # the pack opts out
    assert goal_fix.fold_rules(None, "de") == []


def test_canon_inventory_english_default_matches_name_canon_on_unique_names():
    from robot_agent.core.planning.assist_loop import Places
    mem = {"mug": {"place": DS}}
    goals = [{"type": "on", "object": "mugs", "from": "", "to": DT}]
    assert goal_fix.canon_inventory(goals, mem, Places(ENV)) == goal_fix.canon_names(goals, mem, Places(ENV))
