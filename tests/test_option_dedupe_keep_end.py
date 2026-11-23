"""Option ``dedupe_keep_end`` (default OFF): the gate's removal of a repeated fetch keeps the steps after it (the way home).
Made-up objects and places only."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.core.planning import plan_repair
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places

ENV = {"shelf@hall": {}, "bench@hall": {}, "nook@hall": {}}
GOAL = [{"type": "on", "object": "urn", "from": "shelf@hall", "to": "bench@hall"}]


class W:
    holding, arrived = None, None


def cx(keep):
    c = plan_repair._Ctx(W(), Places(ENV), GOAL, NS(hands=1, release_actions=()), {"MoveTo", "Find", "Pick", "Place"}, (), lambda g: False)
    c.keep_end = keep
    return c


def owned(*s):
    return [(0, st) for st in steps(*s)]


REPEAT = ("MoveTo shelf@hall", "Find urn", "Pick urn", "MoveTo bench@hall", "Place bench@hall", "MoveTo shelf@hall", "Find urn", "Pick urn", "MoveTo nook@hall")


def acts(got):
    return [s["action"] for _o, s in got]


def test_registered_and_off_by_default_the_cut_runs_to_the_end_of_the_plan(make_assist, env):
    assert "dedupe_keep_end" in ASSIST_OPTIONS
    off = make_assist(FakeRobot(env, {}), FakeLLM(default={}), ScriptedPlanner(steps("Find urn")))
    assert off.options.query("keep_after_dedupe") is None
    assert len(plan_repair.op_dedupe(owned(*REPEAT), cx(False))) == 5            # the old cut: the final MoveTo goes too


def test_on_the_way_home_after_the_repeat_stays_through_the_loop(make_assist, env):
    on = make_assist(FakeRobot(env, {}), FakeLLM(default={}), ScriptedPlanner(steps("Find urn")), dedupe_keep_end="1")
    keep = on.options.query("keep_after_dedupe")
    assert callable(keep)
    got = plan_repair.op_dedupe(owned(*REPEAT), cx(keep))
    assert acts(got) == ["MoveTo", "Find", "Pick", "MoveTo", "Place", "MoveTo"] and got[-1][1]["object"] == "nook@hall"
    assert acts(got) == acts(plan_repair.op_dedupe(owned(*REPEAT), cx(True)))                  # the function cuts as the plain True did
    assert [e for e in on.lab_log if e["flag"] == "dedupe_keep_end"] == [{"flag": "dedupe_keep_end", "object": "urn", "kept": 1}]


def test_dedupe_keep_end_lab_only_when_the_two_ends_differ_and_a_no_keeps_the_old_cut():
    asked = []
    ask = lambda o, n: asked.append((o, n)) or False                                  # noqa: E731
    assert acts(plan_repair.op_dedupe(owned(*REPEAT), cx(ask))) == acts(plan_repair.op_dedupe(owned(*REPEAT), cx(False)))
    assert asked == [("urn", 1)]
    asked.clear()
    plan_repair.op_dedupe(owned(*REPEAT[:-1]), cx(ask))                            # the repeat ends the plan: both cuts are the same, nothing asked
    assert asked == []


def test_a_repeat_followed_by_a_release_is_cut_the_same_on_and_off():
    plan = REPEAT[:5] + ("MoveTo bench@hall", "Find urn", "Pick urn", "Place bench@hall")
    assert acts(plan_repair.op_dedupe(owned(*plan), cx(True))) == acts(plan_repair.op_dedupe(owned(*plan), cx(False))) == acts(owned(*REPEAT[:5]))


def test_dedupe_keep_end_empty_nothing_to_cut_returns_none():
    assert plan_repair.op_dedupe(owned("MoveTo shelf@hall", "Find urn", "Pick urn"), cx(True)) is None
    assert plan_repair.op_dedupe([], cx(True)) is None
