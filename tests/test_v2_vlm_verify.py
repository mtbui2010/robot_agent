"""v2 verify-budget, P1.10: the manifest's vlm VerifySpecs (core/planning/verify_vlm.py)."""

import pytest

from robot_agent.contract import SkillResult, VerifySpec
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.verify import StepVerifier, verdict
from robot_agent.core.planning.verify_vlm import VlmCheck, read_answer, refuted, vlm_enabled

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DS, DT, DR = "desk@study room", "dining table@kitchen", "drawer@kitchen"


class StubRobot:
    """The RobotView surface VlmCheck reads: verify_specs(action) and vlm_ask."""

    def __init__(self, specs=None, ask=None):
        self.specs, self.asked = specs or {}, []
        if ask is not None:
            self.vlm_ask = lambda prompt, step=None, node=None: (self.asked.append(prompt), ask(prompt))[1]

    def verify_specs(self, action):
        return list(self.specs.get(action, []))


PLACE_VLM = {"Place": [VerifySpec(kind="symbolic"), VerifySpec(kind="vlm", prompt="Is the {object} on the {place}?")]}


def holding(obj):
    w = WorldState()
    w.holding = obj
    return w


@pytest.mark.parametrize("raw, want", [
    (True, True), (False, False), (None, None), ("yes", True), ("No.", False), ("unsure", None),
    ({"answer": "yes", "confidence": 0.9}, True), ({"ok": False}, False), ((True, 0.7, "note", 12.0), True),
    ("maybe", None)])
def test_read_answer(raw, want):
    assert read_answer(raw)[0] is want


def test_read_answer_keeps_the_confidence():
    assert read_answer({"answer": "no", "confidence": "0.25"})[1] == 0.25


def test_vlm_enabled_setting_wins_else_the_manifest():
    assert vlm_enabled("1", {"vlm_verify": False}) and not vlm_enabled("0", {})
    assert vlm_enabled(None, {}) and not vlm_enabled(None, {"vlm_verify": False})
    assert vlm_enabled(True, {"vlm_verify": False}) and not vlm_enabled("", {"vlm_verify": "false"})


def test_no_vlm_spec_never_asks():
    rb = StubRobot({"Place": [VerifySpec(kind="symbolic")]}, ask=lambda p: "no")
    assert VlmCheck(rb).run({"action": "Place", "object": DT}, holding("apple")) == []
    assert rb.asked == []


def test_disabled_never_asks():
    rb = StubRobot(PLACE_VLM, ask=lambda p: "no")
    assert VlmCheck(rb, enabled=False).run({"action": "Place", "object": DT}, holding("apple")) == []
    assert rb.asked == []


def test_yes_passes_and_the_prompt_names_object_and_place():
    rb = StubRobot(PLACE_VLM, ask=lambda p: "yes")
    out = VlmCheck(rb).run({"action": "Place", "object": DT}, holding("apple"))
    assert [(v.layer, v.ok) for v in out] == [("vlm", True)]
    assert rb.asked == ["Is the apple on the dining table in the kitchen?"]


def test_no_fails_and_is_refuted():
    rb = StubRobot(PLACE_VLM, ask=lambda p: "no")
    out = VlmCheck(rb).run({"action": "Place", "object": DT}, holding("apple"))
    assert not out[0].ok and refuted(out) is out[0] and verdict(out)[0] is False


def _down(p):
    raise ConnectionError("vlm down")


@pytest.mark.parametrize("seen, ok, unknown", [(True, True, False), (False, False, False), (None, True, True)])
def test_vlm_down_falls_back_to_find(seen, ok, unknown):
    looked = []
    chk = VlmCheck(StubRobot(PLACE_VLM, ask=_down), observe=lambda obj, node: (looked.append(obj), seen)[1])
    out = chk.run({"action": "Place", "object": DT}, holding("apple"))
    assert [(v.layer, v.ok, v.unknown) for v in out] == [("vlm_observe", ok, unknown)]
    assert looked == ["apple"] and "VLM unavailable" in out[0].detail


def test_robot_without_a_vlm_falls_back():
    chk = VlmCheck(StubRobot(PLACE_VLM), observe=lambda obj, node: True)
    out = chk.run({"action": "Place", "object": DT}, holding("apple"))
    assert out[0].layer == "vlm_observe" and out[0].ok and chk.calls == 0


def test_low_confidence_is_unsure():
    chk = VlmCheck(StubRobot(PLACE_VLM, ask=lambda p: {"answer": "yes", "confidence": 0.3}), min_conf=0.5,
                   observe=lambda obj, node: False)
    out = chk.run({"action": "Place", "object": DT}, holding("apple"))
    assert out[0].layer == "vlm_observe" and not out[0].ok


def test_open_unsure_is_unknown_not_a_crash():
    rb = StubRobot({"Open": [VerifySpec(kind="vlm", prompt="Is the drawer open?")]}, ask=lambda p: "unsure")
    out = VlmCheck(rb, observe=lambda obj, node: pytest.fail("nothing to Find for an Open")).run(
        {"action": "Open", "object": DR}, WorldState())
    assert out[0].ok and out[0].unknown


def test_step_verifier_runs_the_check_and_skips_the_legacy_hook():
    hook = []
    rb = StubRobot(PLACE_VLM, ask=lambda p: "no")
    v = StepVerifier(True, {"Place"}, lambda st, node: hook.append(st) or (True, None, "", 0.0),
                     vlm_check=VlmCheck(rb))
    out = v.verify({"action": "Place", "object": DT}, SkillResult(ok=True), holding("apple"), None)
    assert [r.layer for r in out] == ["isdone", "symbolic", "vlm"] and hook == []
    assert verdict(out)[0] is False


# ── through AssistLoop (fakebot: the KAAIR-like manifest, vlm specs on Open / Close) ──
OPEN_TASK = {"status": "clear", "reason": "", "question": "",
             "goals": [{"type": "open", "object": "kitchen drawer", "from": "", "to": ""}]}


def _with_vlm(loop, answers):
    asked = []

    def ask(prompt, step=None, node=None):
        asked.append(prompt)
        return answers.pop(0) if answers else "yes"
    loop.robot.adapter.vlm_ask = ask
    return asked


def test_vlm_no_after_open_replans_and_the_effect_stays_out(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    plan = steps(f"MoveTo {DR}", f"Open {DR}")
    planner = ScriptedPlanner(plan, replans=[steps(f"Open {DR}")])
    loop = make_assist(robot, FakeLLM(default=OPEN_TASK), planner, vlm_verify=True)
    asked = _with_vlm(loop, ["no"])
    evs = run_task(loop, "Open the kitchen drawer.")
    failed = [e for e in evs if e["event"] == "step_done" and e.get("status") == "failed"]
    assert failed and "Is the drawer open? -> no" in failed[0]["reason"]
    assert any(c[0] == "replan" and "did not happen" in c[2] for c in planner.calls)
    assert len(asked) == 2 and done(evs)["status"] == "success"


def test_vlm_off_by_default_for_a_manifest_that_says_so(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    loop = make_assist(robot, FakeLLM(default=OPEN_TASK), ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}")))
    asked = _with_vlm(loop, ["no"])
    assert done(run_task(loop, "Open the kitchen drawer."))["status"] == "success" and asked == []


def test_env_turns_it_on(make_assist, env, monkeypatch):
    monkeypatch.setenv("ROBOT_AGENT_VLM_VERIFY", "1")
    robot = FakeRobot(env, {"apple": DS})
    loop = make_assist(robot, FakeLLM(default=OPEN_TASK), ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}")))
    asked = _with_vlm(loop, [])
    assert done(run_task(loop, "Open the kitchen drawer."))["status"] == "success" and asked == ["Is the drawer open?"]


def test_vlm_down_through_the_loop_is_not_a_crash(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    task = {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    loop = make_assist(robot, FakeLLM(default=task), ScriptedPlanner(plan), vlm_verify=True)
    loop.robot.manifest.skill_for("Place").verify.append(VerifySpec(kind="vlm", prompt="Is the {object} on the {place}?"))
    loop.robot.adapter.vlm_ask = lambda *a, **k: _down(a)
    evs = run_task(loop, "Bring the apple from the desk to the dining table.")
    fb = [v for e in evs if e["event"] == "step_verify" for v in e["verifies"] if v["layer"] == "vlm_observe"]
    assert done(evs)["status"] == "success" and fb and fb[0]["ok"] and "seen" in fb[0]["detail"]
    assert not any(e["event"] == "error" for e in evs)
