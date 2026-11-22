"""The pack's ``fleet:`` section: the footprint of each goal kind is resolved and reported (Step S32 of
docs/proposals/generalize_core_design.md; ``robot_agent/core/pack/fleet.py``, ``report.py``, ``packs/household/fleet.yaml``).

Proof: the standard pack's data equals the rule a fleet manager had in code (a reference copy of ``robot_fleet/graph.py``
``footprint()`` at d74f326, written here as it was), for every household kind; a robot whose own pack declares no ``fleet:``
section reports exactly what it did (the s01 and s27a snapshots, never edited); one that declares it reports the footprint of
every kind, pinned by ``manifest_snapshots/s32_fakeline.json`` (the endpoint's answer; no loop run reaches the endpoint).
Made-up robots only. Never writes a snapshot."""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

import snapshots  # noqa: E402

from robot_agent.api import robot_info  # noqa: E402
from robot_agent.contract.pack import PackError  # noqa: E402
from robot_agent.core.pack import fleet, loader, report  # noqa: E402
from robot_agent.core.planning import goals  # noqa: E402
from robot_agent.core.planning.robot import RobotView  # noqa: E402

GOLDEN = snapshots.GOLDEN / "manifest_snapshots"
KINDS = ("on", "in", "open", "close", "go", "say", "deliver")                          # the household kinds, in their order

RAW_LINE = {"schema": 2, "name": "line", "version": "1.0", "extends": "household", "assumed": True,
            "goal_kinds": {"mount": {"rules": "std.on", "needs": ["Pick", ["Place", "MoveTo"]],
                                     "slots": {"object": {"type": "object", "required": True}, "to": {"type": "place"}}},
                           "fasten": {"rules": "std.on", "needs": ["Pick"]}},
            "fleet": {"kinds": {"mount": {"footprint": {"objects": ["object"], "places": ["to"]}},
                                "fasten": {"footprint": {"objects": ["object"]}}}}}


def reference(goal):
    """The rule of robot_fleet/graph.py ``footprint()`` at d74f326, as it was (keys in a list instead of sets, names not
    mapped to logical places): the goal's object; the places from, to and, for open / close, the object."""
    objects = [goal["object"]] if goal.get("object") else []
    keys = [goal.get("from"), goal.get("to")]
    if goal["type"] in ("open", "close"):
        keys.append(goal.get("object"))
    return {"objects": objects, "places": [k for k in keys if k]}


GOALS = [{"object": "cup", "from": "a", "to": "b"}, {"object": "cup", "to": "b"}, {"object": "drawer"}, {"to": "b"}, {"from": "a"},
         {"object": "cup", "from": "a"}, {}]


def household():
    return loader.default_pack()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("goal", GOALS)
def test_the_standard_data_gives_the_footprint_the_manager_computed_in_code(kind, goal):
    pack = household()
    spec = fleet.kinds(pack, [kind])[kind]
    g = dict(goal, type=kind)
    assert fleet.keys(spec, g) == reference(g)


def test_the_standard_pack_names_only_open_and_close_and_the_rest_take_the_default():
    pack = household()
    assert list(pack.table(fleet.TABLE)) == ["open", "close"]
    assert fleet.kinds(pack, KINDS)["go"] == fleet.DEFAULT == {"objects": ["object"], "places": ["from", "to"]}
    assert fleet.kinds(pack, ["close"])["close"] == {"objects": ["object"], "places": ["from", "to", "object"]}
    assert all(e.get("source") for e in pack.table(fleet.TABLE).values())              # a fact with a source label


def test_an_entry_with_one_list_takes_the_default_for_the_other_and_bad_values_are_ignored():
    assert fleet.footprint({"footprint": {"objects": ["object"]}}) == {"objects": ["object"], "places": ["from", "to"]}
    assert fleet.footprint({"footprint": {"places": []}}) == {"objects": ["object"], "places": []}     # empty is a statement
    assert fleet.footprint({"footprint": {"objects": ["a", 3, ""], "places": "to"}}) == {"objects": ["a"], "places": ["from", "to"]}
    assert fleet.footprint(None) == fleet.footprint({}) == fleet.footprint({"footprint": 5}) == fleet.DEFAULT
    out = fleet.footprint(None)
    out["objects"].append("x")
    assert fleet.DEFAULT["objects"] == ["object"]                                      # the default is never handed out itself


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_a_pack_with_no_fleet_section_of_its_own_reports_no_footprint(pkg):
    pack = RobotView.load(pkg).pack
    assert not fleet.declared(pack)
    got = snapshots.manifest_snapshot(pkg)["manifest_of"]
    assert all("footprint" not in row for row in got["goal_kinds"].values())
    view = RobotView.load("fakeline")
    view.use_pack(dict(RAW_LINE, fleet=None))                                          # schema 2, builds on the standard pack
    assert not fleet.declared(view.pack)
    assert all("footprint" not in r for r in robot_info.manifest_of(view, {}, None)["goal_kinds"].values())


def test_the_endpoint_answer_of_a_pack_that_declares_a_footprint_equals_the_s32_snapshot():
    view = RobotView.load("fakeline")
    view.use_pack(RAW_LINE)
    assert fleet.declared(view.pack)
    got = snapshots.harness.normal({"manifest_of": robot_info.manifest_of(view, snapshots._env("fakeline"), snapshots.SITE)})
    want = snapshots.load(GOLDEN / "s32_fakeline.json")
    assert got == want
    rows = want["manifest_of"]["goal_kinds"]
    assert rows["mount"]["footprint"] == {"objects": ["object"], "places": ["to"]}
    assert rows["fasten"]["footprint"] == {"objects": ["object"], "places": ["from", "to"]}     # one list declared: default
    assert rows["close"]["footprint"]["places"] == ["from", "to", "object"] and rows["go"]["footprint"] == fleet.DEFAULT


def test_the_other_keys_of_a_declaring_pack_equal_the_answer_without_the_section():
    a, b = RobotView.load("fakeline"), RobotView.load("fakeline")
    a.use_pack(RAW_LINE)
    b.use_pack(dict(RAW_LINE, fleet=None))
    got, old = robot_info.manifest_of(a, {}, None), robot_info.manifest_of(b, {}, None)
    strip = {k: {n: {f: v for f, v in row.items() if f != "footprint"} for n, row in rows.items()} if k == "goal_kinds" else rows
             for k, rows in got.items()}
    assert strip == old


def test_a_footprint_for_a_kind_the_pack_does_not_have_is_refused_by_name():
    view = RobotView.load("fakeline")
    with pytest.raises(PackError) as e:
        view.use_pack(dict(RAW_LINE, fleet={"kinds": {"nosuch": {"footprint": {"objects": ["object"]}}}}))
        view.pack
    assert "nosuch" in str(e.value)


def test_the_report_adds_the_footprint_only_when_declared_and_keeps_the_other_fields():
    view = RobotView.load("fakeline")
    view.use_pack(RAW_LINE)
    rows = report.goal_kinds(view.pack, goals.table(view.pack), lambda g: [])
    assert list(rows["mount"]) == ["feasible", "needs", "slots", "footprint"]
    assert [k for k in rows] == list(goals.table(view.pack))                           # the table's order


def test_fleet_is_pure_data_in_data_out():
    src = Path(fleet.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("planning" in m or "api" in m for m in mods), mods
    assert len(src.splitlines()) <= 300
    assert all(n.end_lineno - n.lineno + 1 <= 50 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
