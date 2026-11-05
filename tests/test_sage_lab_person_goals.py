"""Solution lab round 1, proposal 1 (flag ``person_goals``, default OFF): a request that mixes go / say goals with
moves keeps the template goals as the loop's own steps, a hand-over goes to the person its goal names, a repeated
goal is dropped, an Open of what cannot be opened is removed. Synthetic ENV and sentences written for the test."""
import re
import types

import pytest

from fakes import FakeLLM, FakeRobot, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import goals as goal_kinds, plan_repair, subgoal_repair
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState

ENV = {
    "sofa@living room": {"aliases": ["sofa"], "loc": {"x": 1.0, "y": 1.0}},
    "table@kitchen": {"aliases": ["table"], "loc": {"x": 3.0, "y": 1.0}},
    "fridge@kitchen": {"aliases": ["fridge"], "openable": True, "loc": {"x": 4.0, "y": 1.0}},
    "shelf@hall": {"aliases": ["shelf"], "loc": {"x": 6.0, "y": 1.0}},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna"], "loc": {"x": 1.2, "y": 1.0}},
    "ben@kitchen": {"person": True, "aliases": ["ben"], "loc": {"x": 3.2, "y": 1.0}},
}
HAND = "Handover"


def G(t, o="", f="", to="", **kw):
    return {"type": t, "object": o, "from": f, "to": to, **kw}


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


def wrong_recipient_count(goal_to):
    return lambda plan: sum(1 for s in plan if s.get("action") == HAND and s.get("object") != goal_to)


def repair(plan, goals, score, **kw):
    return plan_repair.repair(plan, score, W(), Places(ENV), goals, caps={"hands": 1, "release_actions": [HAND]},
                              actions={"MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close", HAND, "Say"},
                              release=[HAND], hand_goal=lambda g: bool(g.get("hand")), **kw)


# ── gate operators ──
def test_recipient_replaces_the_wrong_person_and_goes_there():
    goals = [G("on", "cup", "table@kitchen", "ben@kitchen", hand=True)]
    plan = steps("MoveTo table@kitchen", "Find cup", "Pick cup", "MoveTo anna@living room", "Handover anna@living room")
    out, _n, counts = repair(plan, goals, wrong_recipient_count("ben@kitchen"), person_goals=True)
    assert counts.get("recipient") == 1
    assert [(s["action"], s["object"]) for s in out][-2:] == [("MoveTo", "ben@kitchen"), (HAND, "ben@kitchen")]


def test_recipient_empty_object_gets_a_moveto_first():
    goals = [G("on", "cup", "table@kitchen", "ben@kitchen", hand=True)]
    plan = steps("MoveTo table@kitchen", "Find cup", "Pick cup", "Handover")
    out, _n, counts = repair(plan, goals, wrong_recipient_count("ben@kitchen"), person_goals=True)
    assert counts.get("recipient") == 1
    assert [(s["action"], s["object"]) for s in out][-2:] == [("MoveTo", "ben@kitchen"), (HAND, "ben@kitchen")]


def test_recipient_not_for_a_plain_put_down_goal():
    goals = [G("on", "cup", "table@kitchen", "ben@kitchen")]                      # no hand flag
    plan = steps("MoveTo table@kitchen", "Find cup", "Pick cup", "Handover anna@living room")
    out, _n, counts = repair(plan, goals, wrong_recipient_count("ben@kitchen"), person_goals=True)
    assert out == plan and not counts.get("recipient")


def test_flag_off_the_operators_do_not_run():
    goals = [G("on", "cup", "table@kitchen", "ben@kitchen", hand=True)]
    plan = steps("MoveTo table@kitchen", "Find cup", "Pick cup", "Handover anna@living room")
    out, _n, counts = repair(plan, goals, wrong_recipient_count("ben@kitchen"))
    assert out == plan and "recipient" not in counts and "unopenable" not in counts


def test_unopenable_open_removed_but_an_open_goal_kept():
    bad = lambda plan: sum(1 for s in plan if s.get("action") == "Open" and s.get("object") == "shelf@hall")  # noqa: E731
    plan = steps("MoveTo shelf@hall", "Open shelf@hall", "Find cup", "Pick cup")
    out, _n, counts = repair(plan, [G("on", "cup", "shelf@hall", "table@kitchen")], bad, person_goals=True)
    assert counts.get("unopenable") == 1 and [s["action"] for s in out] == ["MoveTo", "Find", "Pick"]
    out2, _n, counts2 = repair(plan, [G("open", "shelf@hall")], bad, person_goals=True)
    assert out2 == plan and not counts2.get("unopenable")                         # a goal names it: kept


def test_unopenable_never_touches_an_openable_container():
    bad = lambda plan: 1                                                                       # noqa: E731
    plan = steps("MoveTo fridge@kitchen", "Open fridge@kitchen")
    out, _n, counts = repair(plan, [], bad, person_goals=True)
    assert out == plan and not counts.get("unopenable")


# ── goals ──
def test_dedupe_drops_exact_repeats_only():
    a, b = G("say", "hello", "", "ben@kitchen"), G("on", "cup", "", "table@kitchen")
    assert goal_kinds.dedupe([a, b, dict(a), dict(b)]) == [a, b]
    assert goal_kinds.dedupe([G("say", "hello", "", "ben@kitchen"), G("say", "bye", "", "ben@kitchen")]) == \
        [G("say", "hello", "", "ben@kitchen"), G("say", "bye", "", "ben@kitchen")]


# ── the loop ──
class Planner:
    fixed_subgoals = True


class OldPlanner:
    pass


def stub_loop(cfg_on=True, actions=("MoveTo", "Say", "Pick")):
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg = {"sage_subgoals": "goals", "person_goals": cfg_on}
    lp.robot = types.SimpleNamespace(plan_actions=set(actions))
    return lp


def stub_run(goals, planner, actions=("MoveTo", "Say", "Pick")):
    robot = types.SimpleNamespace(plan_actions=set(actions), text=lambda k, lang: "Here you are.")
    return types.SimpleNamespace(goals=goals, places=Places(ENV), lang="en", planner=planner, robot=robot)


MIXED = [G("go", "", "", "sofa@living room"), G("on", "cup", "table@kitchen", "shelf@hall"),
         G("say", "Dinner is ready", "", "ben@kitchen")]


def test_mixed_request_gives_the_template_goals_their_own_steps():
    out = stub_loop()._subgoals(stub_run([dict(g) for g in MIXED], Planner()))
    assert isinstance(out[1], str) and "cup" in out[1]
    assert out[0][1] == [{"action": "MoveTo", "object": "sofa@living room"}]
    assert out[2][1] == [{"action": "MoveTo", "object": "ben@kitchen"}, {"action": "Say", "object": "Dinner is ready"}]


def test_say_to_the_speaker_goes_to_the_speakers_entry_and_unknown_place_is_not_driven_to():
    goals = [G("say", "Hi", "", "me"), G("say", "Bye", "", "attic@nowhere"), G("on", "cup", "", "shelf@hall")]
    out = stub_loop()._subgoals(stub_run(goals, Planner()))
    assert out[0][1][0] == {"action": "MoveTo", "object": "anna@living room"}
    assert out[1][1] == [{"action": "Say", "object": "Bye"}]


def test_flag_off_or_planner_without_support_or_no_mix_is_the_plain_text():
    plain = stub_loop(False)._subgoals(stub_run([dict(g) for g in MIXED], Planner()))
    assert all(isinstance(x, str) for x in plain)
    assert all(isinstance(x, str) for x in stub_loop()._subgoals(stub_run([dict(g) for g in MIXED], OldPlanner())))
    only_moves = [G("on", "cup", "", "shelf@hall")]
    assert all(isinstance(x, str) for x in stub_loop()._subgoals(stub_run(only_moves, Planner())))


# ── flag: option, env, manifest key; default off ──
def test_flag_default_off_option_env_manifest(make_assist, monkeypatch):
    env = {}
    monkeypatch.delenv("ROBOT_AGENT_PERSON_GOALS", raising=False)
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), Planner(), **kw)             # noqa: E731
    assert mk().cfg["person_goals"] is False
    assert mk(person_goals="1").cfg["person_goals"] is True
    monkeypatch.setenv("ROBOT_AGENT_PERSON_GOALS", "1")
    assert mk().cfg["person_goals"] is True
    assert mk(person_goals="0").cfg["person_goals"] is False
    monkeypatch.delenv("ROBOT_AGENT_PERSON_GOALS")
    lp = mk()
    lp.robot.manifest.planner = dict(lp.robot.manifest.planner or {}, person_goals=1)
    assert lp._v2_cfg({})["person_goals"] is True
    assert "person_goals" in PLANNER_KEYS


# ── sub-goal repair leaves a fixed block alone ──
def test_fixed_block_gets_no_llm_edit_and_is_not_emptied():
    calls = []
    rep = types.SimpleNamespace(repair_block=lambda *a, **k: calls.append(a) or [])
    fixed = types.SimpleNamespace(name="say", steps=[{"action": "Say", "object": "x"}], fixed=True)
    other = types.SimpleNamespace(name="move", steps=[{"action": "MoveTo", "object": "a"}], fixed=False)
    notes = []
    loop = types.SimpleNamespace(robot=types.SimpleNamespace(model_capabilities=lambda: {"hands": 1}))
    subgoal_repair._repair_round(loop, rep, [fixed, other], {0: [subgoal_repair._NO_STEPS], 1: ["something"]},
                                 "t", "", "g", notes, budget=None)
    assert fixed.steps == [{"action": "Say", "object": "x"}] and len(calls) == 1


# ── through the REAL construction path: AssistLoop -> GraceBackend -> SAGEPlanner (no stub with the attribute) ──
# Round 1b: the loop asks ``r.planner`` for ``fixed_subgoals``; in a run that is the GraceBackend wrapper, which did not
# pass the SAGEPlanner's attribute on, so the flag never built a fixed block while the stub-based tests above passed.
def real_sage(expand_log):
    import json
    from embodiplan.sage import SAGEPlanner
    from robot_agent.core.planning.backends.pyplanner_grace import GraceBackend

    class Backend:
        def chat(self, messages, temperature=None):
            user = messages[-1]["content"]
            expand_log.append(user)
            return json.dumps({"steps": steps("MoveTo desk@study room", "Find apple", "Pick apple",
                                              "MoveTo dining table@kitchen", "Place dining table@kitchen")}), 0, 0

    p = SAGEPlanner(host="http://127.0.0.1:1", model="scripted", enable_memory=False,
                    capabilities={"hands": 1, "find_reaches": False})
    p._backend = Backend()
    return GraceBackend.wrap(p)


def test_grace_backend_passes_the_fixed_subgoals_capability_on():
    from robot_agent.core.planning.backends.pyplanner_grace import GraceBackend
    from robot_agent.observe.record import RecordingPlanner
    assert real_sage([]).fixed_subgoals is True
    assert RecordingPlanner(real_sage([])).fixed_subgoals is True                # the proxy the bench adapter adds
    assert GraceBackend.wrap(OldPlanner()).fixed_subgoals is False               # a planner without it: off, no error


def test_real_path_mixed_request_gets_fixed_blocks_with_the_exact_sentence(make_assist, env, monkeypatch):
    from fakes import run_task
    sentence = "Dinner is ready"
    expand = []
    llm = FakeLLM({"status": "clear", "reason": "", "question": "", "goals": [
        {"type": "say", "object": sentence, "from": "", "to": "ben@kitchen"},
        {"type": "on", "object": "apple", "from": "desk@study room", "to": "dining table@kitchen"}]})
    env["ben@kitchen"] = {"person": True, "aliases": ["ben"], "loc": {"x": 3.2, "y": 1.0}}   # the fixture is per test
    robot = FakeRobot(env, {"apple": "desk@study room"})
    for on in (True, False):
        expand.clear()
        sage = real_sage(expand)
        loop = make_assist(robot, llm, sage, person_goals=on)
        # a robot with a Say skill (the fake one has none): the loop's own view of the plan vocabulary
        monkeypatch.setattr(type(loop.robot), "plan_actions", property(lambda self: {"MoveTo", "Say", "Find", "Pick", "Place"}))
        evs = run_task(loop, f"Tell Ben \"{sentence}\" and put the apple from the desk on the dining table.", plan_only=True)
        blocks = sage._p.last_blocks
        if on:
            assert [b.fixed for b in blocks] == [True, False], [(b.name, b.fixed) for b in blocks]
            plan = next(e for e in evs if e["event"] == "plan")["steps"]
            assert {"action": "Say", "object": sentence} in [{k: s_[k] for k in ("action", "object")} for s_ in plan]
            asked = {m for x in expand for m in re.findall(r"Current sub-goal: (.*)", x)}
            assert asked == {"put the apple (it is at desk@study room) on dining table@kitchen"}, asked   # the move block only
        else:
            assert not any(b.fixed for b in blocks or [])      # flag off: no fixed block
