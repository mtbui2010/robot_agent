"""Golden traces of the scenario sets: every cassette in tests/golden/set_cassettes replays the REAL AssistLoop identically through
``harness_sets.py`` (the round-A ``harness.py`` plus the opt-in keys of the sets: captures, environ, pack, pause_after, robot
skills / answers_in_order / drops). The round-A cassettes stay in tests/golden/cassettes with ``harness.py`` alone
(``test_golden_cassettes.py``). These tests never write a cassette: ``tools/golden_record.py`` is the only writer and it refuses
to overwrite."""

import pytest

import harness
import harness_sets as HS
import scenario_sets

SETS = HS.cassettes()


@pytest.mark.parametrize("path", SETS, ids=lambda p: p.name.split(".")[0])
def test_the_set_cassette_replays_identically(path):
    d = HS.check(path)
    assert d == "", f"{path.name}: the run differs from its golden trace\n{d}"


def test_every_set_cassette_is_a_scenario_of_a_set_with_its_tags():
    names = {sc["name"]: tags for sc, tags, _a in scenario_sets.set_scenarios()}
    for p in SETS:
        cas = HS.load(p)
        assert cas["format"] == harness.FORMAT and cas["name"] == p.name.split(".")[0], p.name
        assert cas["name"] in names and cas["tags"] == list(names[cas["name"]]), p.name


def test_recording_a_set_scenario_refuses_to_overwrite_here_and_in_the_round_a_folder():
    sc, tags, about = scenario_sets.set_scenarios()[0]
    path = HS.cassette_path(sc["name"])
    before = path.read_bytes()
    with pytest.raises(FileExistsError, match="refuse to overwrite"):
        HS.record(sc, tags, about)
    assert path.read_bytes() == before
    import scenarios
    old, old_tags, old_about = scenarios.SCENARIOS[0]
    with pytest.raises(FileExistsError, match="refuse to overwrite"):
        HS.record(old, old_tags, old_about)


def test_a_changed_capture_section_is_reported():
    cas = next(HS.load(p) for p in SETS if "planner_io" in HS.capture_of(HS.load(p)["scenario"]))
    got = HS.replay(cas)
    assert HS.diff(cas["recorded"], got) == ""
    got["planner_io"] = got["planner_io"][:-1]
    assert HS.diff(cas["recorded"], got).startswith("== planner_io")


def test_an_unknown_capture_is_refused():
    with pytest.raises(ValueError, match="unknown capture"):
        HS.capture_of({"name": "x", "capture": ["nothing"]})
