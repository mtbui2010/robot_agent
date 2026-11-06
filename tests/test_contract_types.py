"""robot_agent.contract: FailureCode, SkillResult (legacy round trips), Geometry, Messages."""

import pytest

from robot_agent.contract import FailureCode, Geometry, Messages, SkillResult


def test_failure_code_values_are_the_shared_names():
    assert [c.value for c in FailureCode] == [
        "absent", "perception_unavailable", "unreachable", "blocked", "grasp_failed", "dropped",
        "hw_fault", "estop", "timeout", "cancelled", "software", "unknown",
        "budget_exceeded"]
    assert FailureCode.ABSENT == "absent"          # str enum: compares / serialises as text


@pytest.mark.parametrize("raw", ["grasp_failed", "GRASP_FAILED", " Grasp_Failed ", FailureCode.GRASP_FAILED])
def test_failure_code_parse(raw):
    assert FailureCode.parse(raw) is FailureCode.GRASP_FAILED


def test_failure_code_parse_rejects_with_the_valid_list():
    with pytest.raises(ValueError, match="valid: absent"):
        FailureCode.parse("nope")
    assert FailureCode.try_parse(3) is None


def test_from_legacy_isdone_dict_success_keeps_data():
    r = SkillResult.from_legacy({"isdone": True, "msg": "found cup", "ins": {"cup": {"loc_3d": [1, 2, 3]}}})
    assert r.ok and r.code is None and r.msg == "found cup"
    assert r.data == {"ins": {"cup": {"loc_3d": [1, 2, 3]}}}
    assert r.to_legacy() == {"isdone": True, "msg": "found cup", "ins": {"cup": {"loc_3d": [1, 2, 3]}}}


def test_from_legacy_isdone_failure_is_classified():
    r = SkillResult.from_legacy({"isdone": False, "msg": "pick: cup 0.81 m deep from the robot, beyond the arm "
                                                         "reach 0.74 m"})
    assert not r.ok and r.code is FailureCode.UNREACHABLE
    assert r.to_legacy()["isdone"] is False and r.to_legacy()["code"] == "unreachable"


def test_from_legacy_explicit_code_wins_over_text():
    r = SkillResult.from_legacy({"isdone": False, "msg": "not found", "code": "estop", "device": "base"})
    assert r.code is FailureCode.ESTOP and r.device == "base"
    assert r.to_legacy() == {"isdone": False, "msg": "not found", "code": "estop", "device": "base"}


def test_from_legacy_switchbot_ok_code():
    ok = SkillResult.from_legacy({"ok": True, "code": 1, "result": "success"})
    assert ok.ok and ok.code is None and ok.data["legacy_code"] == 1
    bad = SkillResult.from_legacy({"ok": False, "code": -1, "result": "no reply"})
    assert not bad.ok and bad.code is FailureCode.TIMEOUT and bad.msg == "no reply"
    assert bad.data == {"result": "no reply", "legacy_code": -1}


def test_from_legacy_scalars():
    assert SkillResult.from_legacy(True).ok
    f = SkillResult.from_legacy(False)
    assert not f.ok and f.code is FailureCode.UNKNOWN
    s = SkillResult.from_legacy("the cup is on the table")
    assert s.ok and s.msg == "the cup is on the table"
    assert SkillResult.from_legacy([1, 2]).data == {"result": [1, 2]}
    r = SkillResult.success("x")
    assert SkillResult.from_legacy(r) is r


def test_from_legacy_none_and_flagless_dict_follow_direct_mode_unless_strict():
    # direct mode (unified_agent) treats these as done; the planner verdict does not
    assert SkillResult.from_legacy(None).ok
    assert SkillResult.from_legacy({"pose": [0, 0, 0]}).ok
    n = SkillResult.from_legacy(None, strict=True)
    assert not n.ok and n.code is FailureCode.UNKNOWN
    d = SkillResult.from_legacy({"pose": [0, 0, 0]}, strict=True)
    assert not d.ok and d.data == {"pose": [0, 0, 0]}


def test_constructor_normalises_code():
    assert SkillResult(False, "dropped").code is FailureCode.DROPPED
    assert SkillResult(True, FailureCode.ABSENT).code is None                 # success carries no code
    assert SkillResult(False, msg="TypeError: x").code is FailureCode.SOFTWARE  # classified when missing
    assert SkillResult.failure("timeout", "slow", device="lift").to_legacy() == {
        "isdone": False, "msg": "slow", "code": "timeout", "device": "lift"}
    assert not SkillResult.failure("absent")


def test_geometry_defaults_match_the_current_hardcoded_values():
    g = Geometry.from_dict(None)
    assert (g.arrive_tol_m, g.reach_m, g.reach_far_m) == (0.3, 0.78, 1.0)
    g = Geometry.from_dict({"arrive_tol_m": 0.5, "base_radius_m": 0.25})
    assert g.arrive_tol_m == 0.5 and g.reach_m == 0.78 and g.extra == {"base_radius_m": 0.25}
    assert g.to_dict()["base_radius_m"] == 0.25


def test_messages_fallbacks():
    m = Messages("ko", {"hello": {"en": "Hello {name}", "ko": "안녕 {name}"}})
    assert m.text("hello", name="A") == "안녕 A"
    assert m.text("hello", lang="qq", name="A") == "Hello A"
    assert m.text("missing") == "missing"
