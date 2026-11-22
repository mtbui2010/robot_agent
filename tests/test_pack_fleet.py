"""Step S32: the pack's ``fleet:`` section (the footprint of a goal kind) is resolved and reported, opt-in.

Proof, in four parts. (1) The default data equals the old rule: the standard pack's ``fleet.yaml`` and ``fleet.DEFAULT`` against a
reference copy of the rule a manager had in code (robot_fleet graph.py ``footprint`` at d74f326: objects = the goal's object, places
= its ``from`` and ``to``, plus the object for the container kinds ``open`` and ``close``), over every household kind and a grid
of goals. (2) A pack that declares no ``fleet:`` section reports the rows it did before (the s01 and s27a snapshots are the other
witnesses; tests/test_robot_info_pack.py is not edited). (3) A scenario pack that declares footprints is pinned by the endpoint
snapshot ``manifest_snapshots/s32_fakeline.json`` (no loop run reaches the endpoint, so no cassette covers it). (4) The resolver is
pure data in, data out. Made-up robots only. The test never writes the snapshot.
"""

import ast
import itertools
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

import harness  # noqa: E402
import snapshots  # noqa: E402

from robot_agent.api import robot_info  # noqa: E402
from robot_agent.contract.pack import PackError  # noqa: E402
from robot_agent.core.pack import fleet, loader  # noqa: E402
from robot_agent.core.planning import goals  # noqa: E402
from robot_agent.core.planning.robot import RobotView  # noqa: E402

SNAPSHOT = snapshots.GOLDEN / "manifest_snapshots" / "s32_fakeline.json"
CONTAINER_KINDS = ("open", "close")                      # robot_fleet/graph.py:26 at d74f326 (the old constant, copied as the reference)
HOUSEHOLD = ("on", "in", "open", "close", "go", "say", "deliver")

SCENARIO = {"schema": 2, "name": "line", "version": "1.0", "extends": "household", "assumed": True,
            "goal_kinds": {"mount": {"rules": "std.on", "needs": ["Pick", ["Place", "MoveTo"]],
                                     "slots": {"object": {"type": "object", "required": True}, "to": {"type": "place"}}},
                           "fasten": {"rules": "std.on", "needs": ["Pick"]}},
            "fleet": {"kinds": {"mount": {"footprint": {"objects": ["object"], "places": ["to"]}},
                                "fasten": {"footprint": {"objects": ["object", "hand"]}}}}}


def legacy_footprint(goal: dict) -> dict:
    """The old rule of robot_fleet graph.py ``footprint`` as slot VALUES (reference copy; no logical-key mapping, which is the
    manager's): the object, then ``from`` and ``to`` and, for the container kinds, the object again as a place."""
    keys = [goal.get("from"), goal.get("to")]
    if goal.get("type") in CONTAINER_KINDS:
        keys.append(goal.get("object"))
    return {"objects": [goal["object"]] if goal.get("object") else [], "places": [k for k in keys if k]}


def read(spec: dict, goal: dict) -> dict:
    """What a manager does with the declared slot names: the goal's non-empty values under them."""
    return {k: [goal[s] for s in spec[k] if goal.get(s)] for k in ("objects", "places")}


def view_with(raw):
    view = RobotView.load("fakeline")
    view.use_pack(raw)
    return view


def rows(view):
    return robot_info.manifest_of(view, {}, None)["goal_kinds"]


# (1) the default data equals the old rule -------------------------------------------------------------------------------------

def test_the_default_constant_is_the_old_rule():
    assert fleet.DEFAULT == {"objects": ["object"], "places": ["from", "to"]}
    assert fleet.footprint(None) == fleet.DEFAULT and fleet.footprint({}) == fleet.DEFAULT
    assert fleet.footprint({"footprint": {}}) == fleet.DEFAULT


def test_the_standard_pack_declares_open_and_close_as_containers_and_nothing_else():
    table = loader.default_pack().table(fleet.TABLE)
    assert set(table) == set(CONTAINER_KINDS)
    for kind in CONTAINER_KINDS:
        assert fleet.footprint(table[kind]) == {"objects": ["object"], "places": ["from", "to", "object"]}
        assert table[kind].get("source")                                                  # a declared fact carries its source


def test_the_resolved_standard_data_reads_like_the_old_rule_over_a_grid_of_goals():
    resolved = fleet.kinds(loader.default_pack(), HOUSEHOLD)
    assert list(resolved) == list(HOUSEHOLD)
    values = (None, "", "apple", "desk@study")
    for kind, obj, src, dst in itertools.product(HOUSEHOLD + ("unknown_kind",), values, values, values):
        goal = {"type": kind, "object": obj, "from": src, "to": dst}
        spec = resolved.get(kind, fleet.DEFAULT)
        assert read(spec, goal) == legacy_footprint(goal), goal


# (2) a pack with no fleet section reports what it did -------------------------------------------------------------------------

@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_a_pack_without_a_fleet_section_reports_no_footprint(pkg):
    got = snapshots.manifest_snapshot(pkg)["manifest_of"]["goal_kinds"]
    assert got and all("footprint" not in row for row in got.values())
    pack = RobotView.load(pkg).pack
    assert not fleet.declared(pack)
    assert fleet.kinds(pack, HOUSEHOLD)["open"]["places"] == ["from", "to", "object"]      # still resolved, only not reported


def test_a_pack_that_only_extends_the_standard_one_does_not_report_the_standard_footprints():
    raw = {k: v for k, v in SCENARIO.items() if k != "fleet"}
    assert all("footprint" not in row for row in rows(view_with(raw)).values())


# (3) the endpoint snapshot of a scenario pack that declares a footprint -------------------------------------------------------

def test_the_endpoint_answer_of_the_scenario_pack_equals_the_recorded_snapshot():
    got = harness.normal({"manifest_of": robot_info.manifest_of(view_with(SCENARIO), snapshots._env("fakeline"), snapshots.SITE)})
    assert got == snapshots.load(SNAPSHOT)


def test_the_scenario_pack_reports_declared_footprints_and_the_default_for_every_other_kind():
    got = rows(view_with(SCENARIO))
    assert got["mount"]["footprint"] == {"objects": ["object"], "places": ["to"]}
    assert got["fasten"]["footprint"] == {"objects": ["object", "hand"], "places": ["from", "to"]}   # one list: the other is the default
    assert got["on"]["footprint"] == fleet.DEFAULT and got["go"]["footprint"] == fleet.DEFAULT
    assert got["open"]["footprint"] == {"objects": ["object"], "places": ["from", "to", "object"]}   # the standard pack's entry
    assert {k: v for k, v in got["mount"].items() if k != "footprint"} == {
        "feasible": True, "needs": ["Pick", ["Place", "MoveTo"]], "slots": {"object": "object", "to": "place"}}


def test_the_endpoint_sends_the_footprint_of_a_pack_that_declares_one(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import robot_agent.core.planning.robot as robot_mod
    import robot_agent.skill_configs as sc
    views = {"fakeline": view_with(SCENARIO)}
    monkeypatch.setattr(robot_info, "current", lambda: types.SimpleNamespace(robot_pkg="fakeline", location="here"))
    monkeypatch.setattr(robot_mod, "robot_for", lambda pkg: views.get(pkg))
    monkeypatch.setattr(sc, "ENV", {"bench@line": {}})
    app = FastAPI()
    app.include_router(robot_info.router)
    with TestClient(app) as client:
        body = client.get("/robot/manifest").json()
    assert body["goal_kinds"]["mount"]["footprint"] == {"objects": ["object"], "places": ["to"]}


def test_the_snapshot_file_is_plain_data_with_the_declared_kinds():
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["manifest_of"]
    assert snap["goal_kinds"]["mount"]["footprint"] == {"objects": ["object"], "places": ["to"]} and snap["pack"]["name"] == "line"


# (4) bad data and purity ------------------------------------------------------------------------------------------------------

def test_a_footprint_for_a_kind_the_pack_does_not_have_is_refused():
    raw = dict(SCENARIO, fleet={"kinds": {"nosuch": {"footprint": {"objects": ["object"]}}}})
    with pytest.raises(PackError):
        view_with(raw).pack


def test_a_footprint_list_keeps_only_slot_names_and_a_missing_list_is_the_default():
    assert fleet.footprint({"footprint": {"objects": ["object", 3, "", None, "hand"]}}) == {"objects": ["object", "hand"], "places": ["from", "to"]}
    assert fleet.footprint({"footprint": {"places": []}}) == {"objects": ["object"], "places": []}      # an empty list says none
    assert fleet.footprint({"footprint": "x"}) == fleet.DEFAULT and fleet.footprint("x") == fleet.DEFAULT
    default = fleet.footprint(None)
    default["objects"].append("changed")
    assert fleet.DEFAULT["objects"] == ["object"]                                          # the caller gets a copy


def test_declared_is_false_for_a_pack_with_no_name_or_origin():
    assert not fleet.declared(types.SimpleNamespace()) and not fleet.declared(types.SimpleNamespace(name="x", origin=None))
    assert fleet.declared(types.SimpleNamespace(name="x", origin={fleet.TABLE: {"a": "x"}}))
    assert not fleet.declared(types.SimpleNamespace(name="x", origin={fleet.TABLE: {"a": "std"}}))


def test_fleet_is_pure_data_in_data_out_and_imports_no_planning_or_api_code():
    tree = ast.parse(Path(fleet.__file__).read_text(encoding="utf-8"))
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("planning" in m or "api" in m or "contract" in m for m in mods), mods
    assert len(Path(fleet.__file__).read_text(encoding="utf-8").splitlines()) <= 300
    assert goals.table(loader.default_pack())                                              # the default table is untouched
