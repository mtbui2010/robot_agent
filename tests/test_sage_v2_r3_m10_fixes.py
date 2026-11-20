"""SAGE-v2 round 3, milestone-10 round-1 fixes (failure classes, not tasks):

* F6.1 the post-step verify applies a step with the robot's body: a release action of the contract (a Handover)
  empties the hand, so a successful hand-over is not judged failed (it was, for every hand-over).

Synthetic places, objects and sentences written for these tests.
"""
from robot_agent.core.planning import action_model
from robot_agent.core.planning.verify import StepVerifier, effects_check
from robot_agent.core.planning.loop import ClosedLoop
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.base import WorldState


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


# ── F6.1 ──
CAPS = action_model.Capabilities.from_any({"hands": 1, "release_actions": ["Handover"]})


def test_release_action_empties_the_hand_in_the_verify():
    step = {"action": "Handover", "object": "neighbour"}
    ok, why = effects_check(step, W(holding="kettle"), ["hand_empty"], capabilities=CAPS)
    assert ok, why
    # without the body (the core default, the ClosedLoop path) the old judgement is kept
    assert effects_check(step, W(holding="kettle"), ["hand_empty"])[0] is False


def test_step_verifier_passes_the_body():
    class Spec:
        name, required_params, effects, verify = "Handover", ["inputs"], ["hand_empty"], []

    class Robot:
        def spec(self, _a):
            return Spec()

        def text(self, *_a, **_k):
            return ""

    v = StepVerifier(False, set(), robot=Robot(), capabilities=CAPS)
    out = v.verify({"action": "Handover", "object": "neighbour"}, {"isdone": True, "msg": ""},
                   W(holding="kettle"), None)
    assert [r.ok for r in out if r.layer == "symbolic"] == [True]


def test_only_the_assist_loop_verifies_with_the_body():
    assert AssistLoop._verify_with_body is True and ClosedLoop._verify_with_body is False


# ── F6.2 ──
def test_handed_over_object_is_observed_at_the_goal_without_a_look():
    from types import SimpleNamespace as NS
    me = NS(_handed={"kettle"})
    r = NS(placed={"kettle": "shelf"})
    ok, ev, dev = AssistLoop._observe_goal(me, r, {"type": "on", "object": "kettle", "to": "shelf"})
    assert ok is True and "handed over" in ev and dev is None
