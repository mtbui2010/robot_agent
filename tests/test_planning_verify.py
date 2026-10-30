"""core/planning/verify.py: symbolic_check, StepVerifier layers, verdict."""

import pytest

from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.records import VerifyResult
from robot_agent.core.planning.verify import StepVerifier, symbolic_check, verdict


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


@pytest.mark.parametrize("step, world, ok, why", [
    ({"action": "MoveTo", "object": "kitchen"}, {}, True, ""),
    ({"action": "Find", "object": "cup"}, {}, True, ""),
    ({"action": "Find", "object": ""}, {}, False, "Find requires an object"),
    ({"action": "Pick", "object": "cup"}, {}, False, "Pick requires a found object"),
    ({"action": "Pick", "object": "cup"}, {"found": "cup", "holding": "mug"}, False,
     "Pick requires empty gripper, holding 'mug'"),
    ({"action": "Pick", "object": "cup"}, {"found": "cup"}, True, ""),
    ({"action": "Place", "object": "table"}, {}, False, "Place requires holding an object"),
    ({"action": "Place", "object": "table"}, {"holding": "cup"}, True, ""),
    ({"action": "PutIn", "object": "drawer"}, {"holding": "cup"}, False, "PutIn requires 'drawer' to be open"),
    ({"action": "PutIn", "object": "drawer"}, {"holding": "cup", "opened": ["drawer"]}, True, ""),
    ({"action": "PutIn", "object": "x", "target": "drawer"}, {"holding": "cup", "opened": ["drawer"]}, True, ""),
    ({"action": "PutIn", "object": "drawer"}, {"opened": ["drawer"]}, False, "PutIn requires holding an object"),
    ({"action": "Open", "object": "drawer"}, {"opened": ["drawer"]}, False, "'drawer' is already open"),
    ({"action": "Open", "object": "drawer"}, {}, True, ""),
    ({"action": "Close", "object": "drawer"}, {}, False, "'drawer' is not open"),
    ({"action": "Close", "object": "drawer"}, {"opened": ["drawer"]}, True, ""),
    ({"action": "TurnOn", "object": "light"}, {"on": ["light"]}, False, "'light' is already on"),
    ({"action": "TurnOff", "object": "light"}, {}, False, "'light' is not on"),
    ({"action": "Wash", "object": "cup"}, {}, False, "Wash requires 'cup' to be held or found"),
    ({"action": "Wash", "object": "cup"}, {"holding": "cup"}, True, ""),
    ({"action": "Serve", "object": "cup"}, {}, False, "Serve requires holding an object"),
    ({"action": "Wait", "object": ""}, {}, True, ""),
    ({"action": "Teleport", "object": "moon"}, {}, True, ""),          # unknown: not blocked
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
