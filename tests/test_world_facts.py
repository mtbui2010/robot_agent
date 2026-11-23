"""Step S26: the fact store of the belief (``WorldState.facts``). With no fact (every robot that declares no action rules) the
belief behaves as the old one, proven against a verbatim copy of the old class (tests/legacy_ref/s26_world_state.py) on a
grid of made-up worlds and edits: the same ``to_dict()`` (no ``facts`` key), ``as_text()``, ``copy()`` and ``update_from_dict``.
With facts: in ``to_dict()`` only when not empty, accepted by ``update_from_dict`` (``{"facts": {}}`` clears them; the request
model of ``PUT /agent/world`` in api/agent.py does not pass the key yet), never in ``as_text()``, copied with the world and
cleared by ``clear_facts`` (the run start)."""

import dataclasses
import itertools

import pytest

from legacy_ref import s26_world_state as old
from robot_agent.core.planning.base import WorldState

TS = 1700000000.0
WORLDS = [
    {},
    {"arrived": "bench@cell a", "found": "bracket"},
    {"holding": "bolt", "holding_since": TS, "holding_pose": {"grasppose": [0, 0, 0, 0, 1]}},
    {"opened": {"tote@cell b", "drawer"}, "on": {"lamp"}, "found_pose": {"loc_3d": [1, 2, 3], "robot_pose": [0, 0, 0]}},
    {"arrived": "jig", "holding": "bracket", "opened": {"tote"}},
]
EDITS = [
    {},
    {"arrived": ""},
    {"found": "bolt"},
    {"found": None, "found_pose": {"loc_3d": [0, 0, 0]}},
    {"holding": "bracket", "holding_since": TS + 5},
    {"holding": None},
    {"holding_since": TS + 9},
    {"opened": ["a", "b"], "on": []},
    {"holding_pose": None, "opened": None},
]


def _pair(fields):
    new, ref = WorldState(**fields), old.WorldState(**fields)
    for w in (new, ref):
        w.mark("arrived", "sensor", ts=TS)
        w.mark_container("drawer", True, ts=TS)
    return new, ref


@pytest.mark.parametrize("fields", WORLDS)
def test_without_facts_the_snapshot_text_and_copy_are_the_old_ones(fields):
    new, ref = _pair(fields)
    assert new.to_dict() == ref.to_dict() and "facts" not in new.to_dict()
    assert list(new.to_dict()) == list(ref.to_dict())
    assert new.as_text() == ref.as_text()
    assert new.copy().to_dict() == ref.copy().to_dict()
    assert new.provenance_view() == ref.provenance_view()


@pytest.mark.parametrize("fields, edit", list(itertools.product(WORLDS, EDITS)))
def test_without_facts_an_edit_does_what_it_did(fields, edit):
    new, ref = _pair(fields)
    new.update_from_dict(dict(edit))
    ref.update_from_dict(dict(edit))
    assert new.to_dict() == ref.to_dict()
    assert new.facts == {}


def test_the_new_field_is_the_last_one_and_empty_by_default():
    names = [f.name for f in dataclasses.fields(WorldState)]
    assert names[:-1] == [f.name for f in dataclasses.fields(old.WorldState)] and names[-1] == "facts"
    assert WorldState().facts == {}


def test_facts_are_in_the_snapshot_only_when_there_are_any():
    w = WorldState(facts={"inspected": {("bracket",), ("bolt",)}, "seated": {("pipe", "wall a")}, "gone": set()})
    d = w.to_dict()
    assert d["facts"] == {"inspected": [["bolt"], ["bracket"]], "seated": [["pipe", "wall a"]]}
    w.facts = {"gone": set()}
    assert "facts" not in w.to_dict()


def test_facts_are_never_in_the_planner_text():
    w = WorldState(arrived="jig", facts={"inspected": {("bracket",)}})
    assert w.as_text() == WorldState(arrived="jig").as_text()
    assert "inspected" not in w.as_text()


def test_an_edit_sets_and_clears_the_facts():
    w = WorldState(facts={"inspected": {("bracket",)}})
    w.update_from_dict({"arrived": "jig"})
    assert w.facts == {"inspected": {("bracket",)}}, "an edit without the key keeps them"
    w.update_from_dict({"facts": {"seated": [["pipe", "wall a"]], "crimped": ["pipe"], "none": []}})
    assert w.facts == {"seated": {("pipe", "wall a")}, "crimped": {("pipe",)}}
    w.update_from_dict({"facts": {}})
    assert w.facts == {} and "facts" not in w.to_dict()
    w.update_from_dict({"facts": {"x": [["a"]]}})
    w.update_from_dict({"facts": None})
    assert w.facts == {}


def test_the_snapshot_reads_back_to_the_same_facts():
    w = WorldState(facts={"seated": {("pipe", "wall a"), ("pipe", "wall b")}, "crimped": {("pipe",)}})
    back = WorldState()
    back.update_from_dict(w.to_dict())
    assert back.facts == w.facts


def test_a_copy_has_its_own_facts_and_clear_empties_them():
    w = WorldState(facts={"inspected": {("bracket",)}})
    c = w.copy()
    c.facts["inspected"].add(("bolt",))
    c.facts["new"] = {("x",)}
    assert w.facts == {"inspected": {("bracket",)}}
    w.clear_facts()
    assert w.facts == {} and c.facts["inspected"] == {("bracket",), ("bolt",)}
