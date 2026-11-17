"""Option ``shared_instances`` (default OFF): an exact memory name with no instance id that two or more remembered instances share is a
choice, not a resolved name. Pure-function tests on made-up memories (lanterns, a zq-language robot's vorps), hook tests, and loop tests
through AssistLoop.run_blocking against the symbolic FakeRobot. Nothing here is a benchmark's object or a real robot's place."""
import json
import time
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import goal_fix, understand
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.shared_instances import SPEC, SharedInstances, open_choice
from test_sage_lab_r3 import DS, DT, ENV, U, dump

NOW = time.time()


def rec(**kw):
    return {"place": DS, "ts": NOW, "how": "seen", **kw}


SHARED = {"lantern": rec(), "amber lantern": rec(instance="L1"), "teal lantern": rec(instance="L2"),
          "bright amber lantern": rec(instance="L1")}
GOAL = {"type": "on", "object": "lantern", "from": "", "to": DT}


# ── the pure function ──
def test_a_bare_record_shared_by_two_instances_gives_their_groups():
    assert goal_fix.shared_instances("lantern", SHARED) == [["amber lantern", "bright amber lantern"], ["teal lantern"]]


def test_a_bare_record_with_an_id_or_one_instance_or_no_record_is_resolved():
    assert goal_fix.shared_instances("lantern", {**SHARED, "lantern": rec(instance="L3")}) == []      # the exact record has an id
    assert goal_fix.shared_instances("lantern", {"lantern": rec(), "amber lantern": rec(instance="L1")}) == []   # one instance
    assert goal_fix.shared_instances("kettle", SHARED) == [] and goal_fix.shared_instances("", SHARED) == []
    letters = {"lant": rec(), "lantern": rec(instance="L1"), "lanterns": rec(instance="L2")}
    assert goal_fix.shared_instances("lant", letters) == []                                         # whole words, not letters


def test_a_memory_with_no_instance_ids_changes_nothing():
    mem = {"lantern": rec(), "amber lantern": rec(), "teal lantern": rec()}
    assert goal_fix.shared_instances("lantern", mem) == [] and goal_fix.shared_instances("lantern", {}) == []
    assert goal_fix.canon_inventory([GOAL], mem, Places(ENV), shared=goal_fix.shared_instances) == ([GOAL], None, [])


def test_canon_asks_its_which_question_for_a_shared_exact_name_and_keeps_the_goal_off():
    out, which, _ = goal_fix.canon_inventory([GOAL], SHARED, Places(ENV), shared=goal_fix.shared_instances)
    assert out == [GOAL] and which == ("lantern", ["teal lantern", "amber lantern"])   # shortest name first
    assert goal_fix.canon_inventory([GOAL], SHARED, Places(ENV)) == ([GOAL], None, [])                 # off: the exact name is resolved


def test_the_persons_own_words_rename_instead_of_asking():
    out, which, notes = goal_fix.canon_inventory([GOAL], SHARED, Places(ENV), english="put the teal lantern on the table",
                                                 shared=goal_fix.shared_instances)
    assert which is None and out[0]["object"] == "teal lantern" and out[0]["said"] == "lantern" and notes


def test_a_second_robot_in_a_made_up_language_through_its_data():
    """Robot 2: other places, labels of language zq (not the prompt language), its own plural rule ``en`` -> '' (pack data)."""
    places = Places({"plok@zun": {}, "trel@zun": {}})
    mem = {"vorp": {"place": "plok@zun"}, "kel vorp": {"place": "plok@zun", "id": "v1"}, "mib vorp": {"place": "plok@zun", "id": "v2"}}
    g = {"type": "on", "object": "vorp", "from": "", "to": "trel@zun"}
    _out, which, _ = goal_fix.canon_inventory([g], mem, places, "zq", [("en", "")], shared=goal_fix.shared_instances)
    assert which == ("vorp", ["kel vorp", "mib vorp"])
    out, which, _ = goal_fix.canon_inventory([g], mem, places, "zq", [("en", "")], english="mib vorpen", shared=goal_fix.shared_instances)
    assert which is None and out[0]["object"] == "mib vorp"
    assert open_choice("vorp", mem, {"mib", "vorp"}) == [] and len(open_choice("vorp", mem, {"vorp"})) == 2


def test_said_fits_is_the_engines_narrowing_rule():
    groups = [["amber lantern"], ["teal lantern"]]
    assert goal_fix.said_fits(groups, "lantern", {"teal"}, str) == [["teal lantern"]]
    assert goal_fix.said_fits(groups, "lantern", set(), str) == [] and goal_fix.said_fits([["lantern"]], "lantern", {"lantern"}, str) == []


# ── the hook ──
def make(mem, task="put the lantern on the table", dialogue=()):
    svc = NS(lab=lambda *a, **k: svc.labs.append((a, k)), labs=[])
    run = NS(memory=NS(data={"objects": mem}), task=task, dialogue=list(dialogue), lang="en")
    return SharedInstances(resolve(SPEC, {"shared_instances": "1"}, {}, None), svc), run, svc


def test_the_read_back_gets_a_choice_issue_for_a_shared_exact_name():
    opt, run, svc = make(SHARED)
    got = opt.on_issues([], run, [GOAL])
    assert got == [{"kind": "choice", "what": "lantern", "options": ["amber lantern", "teal lantern"]}]
    assert understand.issue_question(got, Places(ENV)) == "Which lantern do you mean: amber lantern or teal lantern?"
    assert svc.labs == [(("shared_instances",), {"at": "issues", "name": "lantern", "n": 2})]


def test_the_read_back_adds_nothing_when_words_narrow_an_id_resolves_or_a_choice_exists():
    for mem, task, issues in ((SHARED, "put the amber lantern there", []), ({**SHARED, "lantern": rec(instance="L9")}, "the lantern", []),
                              (SHARED, "the lantern", [{"kind": "choice", "what": "lantern", "options": ["a", "b"]}])):
        opt, run, svc = make(mem, task)
        assert opt.on_issues(issues, run, [GOAL]) is None and not svc.labs
    opt, run, _ = make(SHARED, "the lantern", [("user", "the teal one")])           # an answer narrows too
    assert opt.on_issues([], run, [GOAL]) is None
    assert opt.on_issues([], run, [{**GOAL, "type": "go"}]) is None


def test_hook_and_service_do_nothing_when_memory_empty():
    for mem in ({}, None):
        opt, run, svc = make(mem)
        assert opt.on_issues([], run, [GOAL]) is None and not svc.labs
        assert opt.shared_names(run)("lantern", mem, None) == [] and not svc.labs


# ── through the loop ──
def play(make_assist, env, tmp_path, memory, answers=(), **opts):
    (tmp_path / "object_memory.json").write_text(json.dumps({"objects": memory}))
    robot = FakeRobot(env, {"amber lantern": DS, "teal lantern": DS}, answers=answers)
    llm = FakeLLM(default=U(("on", "lantern", "", DT)))
    loop = make_assist(robot, llm, ScriptedPlanner(steps(f"MoveTo {DS}")), inventory_ask="1", confirm_task=False, **opts)
    evs = run_task(loop, "Put the lantern on the dining table.")
    return loop, robot, llm, evs


def _und(evs):
    return [e for e in evs if e["event"] == "understand"]


def test_registered_and_default_off(make_assist, env):
    assert "shared_instances" in ASSIST_OPTIONS and REGISTRY.get("shared_instances").key("enabled").default is False
    assert SPEC.enabled == "enabled" and SPEC.services == ("shared_names",)


def test_loop_byte_identical_when_key_off_or_memory_empty(make_assist, env, tmp_path):
    """The key off, and on with no bare shared record: the same events, questions, prompts and lab log as the option not set."""
    def run(memory, **o):
        loop, robot, llm, evs = play(make_assist, env, tmp_path, memory, answers=["the amber lantern"] * 3, **o)
        return dump(evs), robot.asked, [p for p, _f in llm.calls], [x for x in loop.lab_log if x["flag"] != "shared_instances"]
    base = run(SHARED)
    assert run(SHARED, shared_instances="0") == base
    plain = {k: {kk: vv for kk, vv in v.items() if kk != "instance"} for k, v in SHARED.items()}    # no ids anywhere
    assert run(plain, shared_instances="1") == run(plain)
    assert run({}, shared_instances="1") == run({})


def test_regression_an_exact_bare_name_shared_by_two_remembered_objects_is_asked_about(make_assist, env, tmp_path):
    """The class C1: the memory keeps a bare "lantern" for two lanterns at one usual place; the reader names "lantern" exactly. Off: no
    question (the robot takes one of them). On: the which-question names both, and the answer renames the goal."""
    loop, _r, _l, evs = play(make_assist, env, tmp_path, SHARED, answers=["the teal lantern"] * 3)
    assert not _und(evs)[0]["question"] and not any(x["flag"] == "shared_instances" for x in loop.lab_log)
    loop, _r, _l, evs = play(make_assist, env, tmp_path, SHARED, answers=["the teal lantern"] * 3, shared_instances="1")
    u = _und(evs)
    assert "the teal lantern or the amber lantern" in u[0]["question"]
    assert u[-1]["goals"][0]["object"] == "teal lantern"
    assert any(x["flag"] == "shared_instances" and x["at"] == "canon" for x in loop.lab_log)


@pytest.mark.parametrize("ask_focus", ["0", "1"])
def test_the_read_back_asks_when_grounding_does_not(make_assist, env, tmp_path, ask_focus):
    """inventory_ask off (no grounding question): the read-back's choice issue asks, with or without ask_focus."""
    (tmp_path / "object_memory.json").write_text(json.dumps({"objects": SHARED}))
    robot = FakeRobot(env, {"amber lantern": DS, "teal lantern": DS}, answers=["the teal lantern"] * 3)
    loop = make_assist(robot, FakeLLM(default=U(("on", "lantern", "", DT))), ScriptedPlanner(steps(f"MoveTo {DS}")),
                       shared_instances="1", ask_focus=ask_focus, confirm_task=False)
    list(loop.run_blocking("Put the lantern on the dining table."))
    assert any("amber lantern or teal lantern" in str(q) for q in robot.asked)
    assert [x["at"] for x in loop.lab_log if x["flag"] == "shared_instances"][:1] == ["issues"]
