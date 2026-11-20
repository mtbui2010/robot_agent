"""R4b item 3: a miss is not proof of absence. The belief's posterior uses the detector's false-negative rate
(the robot's own: manifest planner.detector_miss_rate, option, env), a phantom pick is a search miss and not a
grasp failure. Synthetic places and objects only."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning.search import SearchBelief, SearchConfig, clamp_miss_rate

def understood(*goals):
    return {"status": "clear", "reason": "", "reason_type": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


PLACES = ["a@r1", "b@r1", "c@r2", "d@r2"]


def belief(rate=0.1, now=1000.0):
    return SearchBelief(PLACES, SearchConfig(miss_rate=rate, rank="prior"), now=lambda: now)


def test_rate_is_clamped_and_comes_from_option_env_manifest():
    assert clamp_miss_rate("x") == 0.1 and clamp_miss_rate(5) == 0.9 and clamp_miss_rate(-1) == 0.0
    assert SearchConfig.from_env({}, env=lambda k, d=None: d).miss_rate == 0.1
    assert SearchConfig.from_env({}, env=lambda k, d=None: d, miss_default=0.3).miss_rate == 0.3
    assert SearchConfig.from_env({"detector_miss_rate": 0.2}, env=lambda k, d=None: d, miss_default=0.3).miss_rate == 0.2
    env = {"ROBOT_AGENT_DETECTOR_MISS_RATE": "0.4"}
    assert SearchConfig.from_env({}, env=lambda k, d=None: env.get(k, d), miss_default=0.3).miss_rate == 0.4
    assert SearchConfig.from_env({}, env=lambda k, d=None: d, miss_default="bad").miss_rate == 0.1
    assert "detector_miss_rate" in PLANNER_KEYS


def test_a_recent_sighting_stays_probable_after_one_miss():
    b = belief(0.1)
    b.see("cup", "a@r1", ts=1000.0, how="seen")
    before = b.posterior("cup")["a@r1"]
    b.missed("cup", "a@r1", [])
    after = b.posterior("cup")
    assert before > 0.9 and abs(sum(after.values()) - 1) < 1e-9
    assert after["a@r1"] > after["b@r1"]          # one miss: ten times less, still above a place nothing points to
    b.missed("cup", "a@r1", [])
    assert b.posterior("cup")["a@r1"] < after["a@r1"]


def test_relook_beats_the_flat_rest_only_for_a_strong_prior():
    b = belief(0.1)
    b.see("cup", "a@r1", ts=1000.0)
    b.missed("cup", "a@r1", [])
    assert b.relook_beats_next("cup", "b@r1", []).place == "a@r1"
    assert b.relook_beats_next("cup", "a@r1", []) is None            # not where the robot stands
    flat = belief(0.1)
    flat.missed("cup", "a@r1", [])
    assert flat.relook_beats_next("cup", "b@r1", []) is None         # nothing pointed there: the rest is as likely


def test_budget_follows_the_rate():
    assert [belief(r).relook_budget() for r in (0, 0.05, 0.1, 0.2, 0.9)] == [0, 1, 2, 4, 4]


def test_perfect_detector_gives_no_relook():
    b = belief(0.0)
    b.see("cup", "a@r1", ts=1000.0)
    b.missed("cup", "a@r1", [])
    assert b.posterior("cup")["a@r1"] == 0.0 and b.relook_budget() == 0


def test_misses_survive_unsearch_and_roundtrip_and_found_clears():
    b = belief(0.1)
    b.see("cup", "a@r1", ts=1000.0)
    b.missed("cup", "a@r1", [])
    b.unsearch("cup", "a@r1")
    b.missed("cup", "a@r1", [])
    assert b.misses["cup"]["a@r1"] == 2
    c = belief(0.1).load(b.to_dict())
    assert c.misses == b.misses and c.posterior("cup") == pytest.approx(b.posterior("cup"))
    b.found("cup", "b@r1")
    assert "cup" not in b.misses




@pytest.mark.parametrize("rate, ok", [(0.1, True), (0, True), (0.9, True), (0.95, False), (-0.1, False), ("often", False)])
def test_manifest_checks_the_miss_rate(tmp_path, rate, ok):
    from robot_agent.contract import Manifest
    from robot_agent.contract.check_manifest import Report, _check_planner
    m = Manifest.from_dict({"robot": "r", "skills": [{"name": "move", "action": "MoveTo"}],
                            "planner": {"detector_miss_rate": rate}})
    rep = Report(pkg_dir=tmp_path, manifest=m)
    _check_planner(m, rep)
    assert (rep.errors == []) is ok and rep.warnings == []
