"""core/planning/verify.py: symbolic_check, StepVerifier layers, verdict."""

import pytest

from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.records import VerifyResult
from robot_agent.core.planning.verify import StepVerifier, symbolic_check, verdict


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


# Verdicts follow the single action model (embodiplan.action_model 1.0, robot capabilities: one hand, Find
# only looks); see docs/action_model_divergence.md for the decided rule of each case.
@pytest.mark.parametrize("step, world, ok, why", [
    ({"action": "MoveTo", "object": "kitchen"}, {}, True, ""),
    ({"action": "Find", "object": "cup"}, {}, True, ""),
    ({"action": "Find", "object": ""}, {}, False, "Find requires a target object"),
    ({"action": "Pick", "object": "cup"}, {}, False, "Pick requires a preceding Find of the target object"),
    ({"action": "Pick", "object": "cup"}, {"found": "cup", "holding": "mug"}, False,
     "cannot Pick while already holding 'mug'"),
    ({"action": "Pick", "object": "cup"}, {"found": "cup"}, True, ""),
    # Pick needs the last Find to be that object (C09)
    ({"action": "Pick", "object": "cup"}, {"found": "mug"}, False, "Pick 'cup' but the last Find was 'mug'"),
    ({"action": "Place", "object": "table"}, {}, False, "Place requires holding an object"),
    ({"action": "Place", "object": "table"}, {"arrived": "table", "holding": "cup"}, True, ""),
    # Place / PutIn need the robot at the receptacle (C13, C19)
    ({"action": "Place", "object": "table"}, {"holding": "cup"}, False,
     "Place requires MoveTo 'table' first (currently at 'None')"),
    ({"action": "PutIn", "object": "drawer"}, {"arrived": "drawer", "holding": "cup"}, False,
     "PutIn requires container 'drawer' to be Open"),
    ({"action": "PutIn", "object": "drawer"}, {"arrived": "drawer", "holding": "cup", "opened": ["drawer"]}, True, ""),
    ({"action": "PutIn", "object": "drawer"}, {"holding": "cup", "opened": ["drawer"]}, False,
     "PutIn requires MoveTo 'drawer' first (currently at 'None')"),
    # PutIn names the container in `object` (`target` is not read); a non-container needs no Open (C18)
    ({"action": "PutIn", "object": "x", "target": "drawer"}, {"arrived": "x", "holding": "cup"}, True, ""),
    ({"action": "PutIn", "object": "drawer"}, {"arrived": "drawer", "opened": ["drawer"]}, False,
     "PutIn requires holding an object"),
    ({"action": "Open", "object": "drawer"}, {"arrived": "drawer", "opened": ["drawer"]}, False,
     "Open 'drawer' but it is already Open"),
    ({"action": "Open", "object": "drawer"}, {"arrived": "drawer"}, True, ""),
    # Open / Close need the robot at the target; Find only looks (C22, C23)
    ({"action": "Open", "object": "drawer"}, {}, False, "Open requires a preceding Find of (or MoveTo) 'drawer'"),
    ({"action": "Open", "object": "drawer"}, {"found": "drawer"}, False,
     "Open requires a preceding Find of (or MoveTo) 'drawer'"),
    # Open / Close with a full hand on a one-hand robot (C21, C26)
    ({"action": "Open", "object": "drawer"}, {"arrived": "drawer", "holding": "cup"}, False,
     "cannot Open 'drawer' while holding 'cup' (one hand)"),
    ({"action": "Close", "object": "drawer"}, {"arrived": "drawer"}, False, "Close 'drawer' but it is not Open"),
    ({"action": "Close", "object": "drawer"}, {"arrived": "drawer", "opened": ["drawer"]}, True, ""),
    ({"action": "TurnOn", "object": "light"}, {"arrived": "light", "on": ["light"]}, False,
     "TurnOn 'light' but it is already On"),
    ({"action": "TurnOff", "object": "light"}, {"arrived": "light"}, False, "TurnOff 'light' but it is not On"),
    ({"action": "Wash", "object": "cup"}, {}, False, "Wash requires a preceding Find of (or MoveTo) 'cup'"),
    ({"action": "Wash", "object": "cup"}, {"holding": "cup"}, True, ""),
    # Serve with an empty hand is only a warning (C31)
    ({"action": "Serve", "object": "cup"}, {}, True, ""),
    ({"action": "Wait", "object": ""}, {}, True, ""),
    ({"action": "Teleport", "object": "moon"}, {}, True, ""),          # unknown: left to the robot's vocabulary (C32)
])
def test_symbolic_check(step, world, ok, why):
    w = W(**world)
    before = w.to_dict()
    assert symbolic_check(step, w) == (ok, why)
    assert w.to_dict() == before                     # never mutates the world


def test_verifier_short_circuits_on_isdone_false():
    v = StepVerifier(vlm_enabled=True, vlm_actions={"Pick"}, vlm_hook=lambda s, n: (True, 1.0, "", 0))
    out = v.verify({"action": "Pick", "object": "cup"}, {"isdone": False, "msg": "no cup"}, W(found="cup"), None)
    assert [(r.layer, r.ok, r.detail) for r in out] == [("isdone", False, "no cup")]
    assert verdict(out) == (False, "no cup")


def test_verifier_layers_and_vlm():
    calls = []

    def hook(step, node):
        calls.append(step["action"])
        return False, 0.4, "cup not in hand", 12.0

    v = StepVerifier(True, {"Pick"}, hook)
    out = v.verify({"action": "Pick", "object": "cup"}, {"isdone": True}, W(found="cup"), None)
    assert [r.layer for r in out] == ["isdone", "symbolic", "vlm"]
    assert out[2].confidence == 0.4 and out[2].latency_ms == 12.0
    assert verdict(out) == (False, "cup not in hand")
    # not a VLM action / VLM off: two layers only
    assert len(v.verify({"action": "Find", "object": "cup"}, {"isdone": True}, W(), None)) == 2
    assert len(StepVerifier(False, {"Pick"}, hook).verify(
        {"action": "Pick", "object": "cup"}, {"isdone": True}, W(found="cup"), None)) == 2
    assert calls == ["Pick"]


def test_verifier_broken_vlm_hook_never_fails_a_step():
    def hook(step, node):
        raise RuntimeError("vlm down")

    out = StepVerifier(True, {"Pick"}, hook).verify({"action": "Pick", "object": "c"}, {"isdone": True},
                                                    W(found="c"), None)
    assert out[-1].ok and "vlm down" in out[-1].detail
    assert verdict(out) == (True, "")


def test_verdict():
    assert verdict([]) == (False, "no verification ran")
    assert verdict([VerifyResult("isdone", True), VerifyResult("symbolic", False, "")]) == (False, "symbolic failed")
    assert verdict([VerifyResult("isdone", True)]) == (True, "")
