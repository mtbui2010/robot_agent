"""Golden traces: every cassette in tests/golden/cassettes replays the REAL AssistLoop identically (model answers from the cassette,
FakeRobot and scripted planner as recorded). A difference fails with a per-section diff. These tests never write a cassette:
``tools/golden_record.py`` is the only writer and it refuses to overwrite (docs/testing.md)."""

import pytest

import harness
import scenarios

CASSETTES = harness.cassettes()
NEEDED_TAGS = {"success", "failure", "question", "stop", "goals_only", "bt_goal", "replan", "plan_only"}


@pytest.mark.parametrize("path", CASSETTES, ids=lambda p: p.name.split(".")[0])
def test_the_cassette_replays_identically(path):
    d = harness.check(path)
    assert d == "", f"{path.name}: the run differs from its golden trace\n{d}"


def test_there_are_at_least_eight_cassettes_and_their_tags_cover_the_core_paths():
    tags = set()
    for p in CASSETTES:
        cas = harness.load(p)
        assert cas["tags"] and cas["format"] == harness.FORMAT and cas["name"] == p.name.split(".")[0], p.name
        tags |= set(cas["tags"])
    assert len(CASSETTES) >= 8 and NEEDED_TAGS <= tags, sorted(NEEDED_TAGS - tags)


def test_every_declared_scenario_has_its_cassette():
    missing = [sc["name"] for sc, _t, _a in scenarios.SCENARIOS if not harness.cassette_path(sc["name"]).exists()]
    assert not missing, f"no cassette for {missing}: run tools/golden_record.py with its record flag"


def test_recording_refuses_to_overwrite_a_golden():
    sc, tags, about = scenarios.SCENARIOS[0]
    path = harness.cassette_path(sc["name"])
    before = path.read_bytes()
    with pytest.raises(FileExistsError, match="refuse to overwrite"):
        harness.record(sc, tags, about)
    assert path.read_bytes() == before


def test_a_changed_answer_is_reported_as_a_readable_diff():
    """The harness bites: a replay whose final status is changed in memory gives a non-empty diff naming the section and the event."""
    cas = harness.load(harness.cassette_path("fetch_success"))
    got = harness.replay(cas)
    got["events"] = [dict(e, status="failed") if e.get("event") == "done" else e for e in got["events"]]
    d = harness.diff(cas["recorded"], got)
    assert d.startswith("== events:") and '"status": "failed"' in d


def test_the_mutation_check_fails_on_a_mutation_no_golden_catches(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("golden_mutation_check", harness.ROOT / "tools" / "golden_mutation_check.py")
    mc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mc)
    assert len(mc.MUTATIONS) >= 5
    first, second = list(mc.MUTATIONS)[:2]
    caught = {"mutation": first, "caught_by": ["fetch_success"], "first_diff": {"fetch_success": "== events"}}
    assert mc.report({"none": {"caught_by": []}, first: caught}) == 0
    assert mc.report({"none": {"caught_by": []}, first: caught, second: {"mutation": second, "caught_by": []}}) == 1
    assert mc.report({"none": {"caught_by": ["fetch_success"]}, first: caught}) == 1
    assert "MISS" in capsys.readouterr().out
