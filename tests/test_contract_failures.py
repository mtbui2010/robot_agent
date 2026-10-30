"""classify_legacy_failure on the texts skills and the core's verify actually produce; recovery policy table."""

import ast
import re
from pathlib import Path

import pytest

from robot_agent.contract import (DEFAULT_RECOVERY, SOFTWARE_ERR, FailureCode, Recovery, RecoveryPolicy,
                                  RecoveryRule, classify_legacy_failure, is_software_error, policy_for)

C = FailureCode
CASES = [
    # kcare pick / place reach_problem()
    ("pick: cup 0.81 m deep from the robot, beyond the arm reach 0.74 m", C.UNREACHABLE),
    ("pick: cup needs the base to drive +0.40 m forward, more than max_shift 0.30 m", C.UNREACHABLE),
    # assist_loop verify details
    ("the cup was seen 2.3 m away, near kitchen, out of reach from here", C.UNREACHABLE),
    ("stopped 0.45 m from table@living room", C.UNREACHABLE),
    ("I can't get to the table", C.UNREACHABLE),
    ("pick reported success but the gripper is empty", C.GRASP_FAILED),
    ("place: cup seems to be still in the gripper", C.GRASP_FAILED),
    ("pnp: could not grasp cup in 3 tries", C.GRASP_FAILED),
    ("place failed and the object is no longer in the hand", C.DROPPED),
    # recognition.find / find_place
    ("find ['cup'] failed ...", C.ABSENT),
    ("find_place: table not found", C.ABSENT),
    ("find_place: no free spot on table clear of its edges and of the objects on it", C.BLOCKED),
    ("camera agent 'head_rgb' not registered — add it in the Connections panel", C.PERCEPTION_UNAVAILABLE),
    ("TCP client 'detector' not registered — add it in the Connections panel", C.PERCEPTION_UNAVAILABLE),
    ("qa: no fresh frame from head", C.PERCEPTION_UNAVAILABLE),
    ("Sam returned None", C.PERCEPTION_UNAVAILABLE),
    # skill bugs (assist_loop stops at once on these)
    ("move() got an unexpected keyword argument 'foo'", C.SOFTWARE),
    ("'NoneType' object has no attribute 'get'", C.SOFTWARE),
    ("pick() missing 1 required positional argument: 'node'", C.SOFTWARE),
    ('Skill "flyy" not registered', C.SOFTWARE),
    ("AttributeError: has no attribute ... object not found", C.SOFTWARE),   # a bug wins over 'not found'
    # run control / devices
    ("Cancelled by user", C.CANCELLED),
    ("emergency stop pressed", C.ESTOP),
    ("base e-stop active", C.ESTOP),
    ("vla_drawer_open: vla-serve at http://10.0.0.2:8000 not reachable (refused)", C.HW_FAULT),
    ("[Errno 111] Connection refused", C.HW_FAULT),
    ("request timed out after 30 s", C.TIMEOUT),
    ("no reply", C.TIMEOUT),
    ("", C.UNKNOWN),
    (None, C.UNKNOWN),
    ("{'isdone': False, 'rets': [True, False]}", C.UNKNOWN),
]


@pytest.mark.parametrize("msg, code", CASES)
def test_classify_legacy_failure(msg, code):
    assert classify_legacy_failure(msg) is code


def test_failure_text_is_read_only_here():
    """Drift guard: the planning loops keep no failure-text patterns of their own; they read the
    FailureCode (SkillResult.from_legacy / classify_legacy_failure) and the recovery policy."""
    planning = Path(__file__).resolve().parents[1] / "robot_agent" / "core" / "planning"
    for src in sorted(planning.glob("*.py")):
        tree = ast.parse(src.read_text())
        names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets
                 if isinstance(t, ast.Name)}
        assert "_SOFTWARE_ERR" not in names, src.name
        consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not any("unexpected keyword argument" in c or "still in the gripper" in c for c in consts), src.name
    assert is_software_error("TypeError: x") and not is_software_error("cup not found")
    assert SOFTWARE_ERR.search("TypeError: execute() got an unexpected keyword argument 'type'")


def test_default_table_covers_every_code():
    assert set(DEFAULT_RECOVERY) == set(FailureCode)


@pytest.mark.parametrize("code, first, later", [
    (C.ESTOP, Recovery.STOP, Recovery.STOP),
    (C.CANCELLED, Recovery.STOP, Recovery.STOP),
    (C.SOFTWARE, Recovery.STOP, Recovery.STOP),
    (C.PERCEPTION_UNAVAILABLE, Recovery.WAIT_HEALTH, Recovery.STOP),
    (C.UNREACHABLE, Recovery.REAPPROACH, Recovery.REPLAN),
    (C.ABSENT, Recovery.CONTINUE_SEARCH, Recovery.ASK_HELP),
    (C.TIMEOUT, Recovery.HEALTH_CHECK, Recovery.REPLAN),
    (C.GRASP_FAILED, Recovery.RETRY, Recovery.REPLAN),
    (C.DROPPED, Recovery.REFIND, Recovery.REPLAN),
    (C.UNKNOWN, Recovery.VERIFY, Recovery.REPLAN),
])
def test_default_policy(code, first, later):
    p = RecoveryPolicy()
    assert p.decide(code, 1) is first
    assert p.decide(code, 10) is later


def test_policy_flags():
    p = RecoveryPolicy()
    assert p.rule_for(C.ESTOP).retry_allowed is False
    assert p.rule_for(C.PERCEPTION_UNAVAILABLE).mark_searched is False
    assert p.rule_for(C.ABSENT).mark_searched is True
    assert p.rule_for(C.SOFTWARE).report is True
    assert p.decide(C.GRASP_FAILED, 2) is Recovery.REPLAN          # retry once, then replan
    assert p.decide(C.ABSENT, 3) is Recovery.CONTINUE_SEARCH and p.decide(C.ABSENT, 4) is Recovery.ASK_HELP
    assert p.rule_for(None) is p.rule_for("garbage") is p.rule_for(C.UNKNOWN)


def test_adapter_overrides():
    class A:
        recovery_overrides = {"perception_unavailable": {"action": "wait_health", "retries": 5, "then": "replan"},
                              C.BLOCKED: "ask_help"}
    p = policy_for(A())
    assert p.decide(C.PERCEPTION_UNAVAILABLE, 5) is Recovery.WAIT_HEALTH
    assert p.decide(C.PERCEPTION_UNAVAILABLE, 6) is Recovery.REPLAN
    assert p.rule_for(C.BLOCKED) == RecoveryRule(Recovery.ASK_HELP)
    assert p.rule_for(C.ESTOP) is DEFAULT_RECOVERY[C.ESTOP]               # untouched entries stay
    assert policy_for(None).table == DEFAULT_RECOVERY
    assert RecoveryPolicy().with_overrides({C.ESTOP: "stop"}).rule_for(C.ABSENT) is DEFAULT_RECOVERY[C.ABSENT]
    with pytest.raises(ValueError):
        RecoveryPolicy({"nope": "stop"})
