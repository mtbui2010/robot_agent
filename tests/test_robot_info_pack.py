"""``GET /robot/manifest`` reports the pack, the goal kinds with their needs, and the question kinds (Step S27a of
docs/proposals/generalize_core_design.md, ``robot_agent/core/pack/report.py``). Additive only: every old key of the answer keeps its
value (the s01 snapshots, tests/test_manifest_snapshot.py, which is not edited), the three added keys are pinned by the s27a
snapshots (``python tools/golden_record.py --snapshots --prefix s27a`` writes ``manifest_snapshots/s27a_<pack>.json``, never
overwriting), and a view with no readable pack gets the old answer. Made-up robots only. Never writes a snapshot."""

import ast
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

import snapshots  # noqa: E402

from robot_agent.api import robot_info  # noqa: E402
from robot_agent.core.pack import report  # noqa: E402
from robot_agent.core.planning import goals  # noqa: E402
from robot_agent.core.planning.robot import RobotView  # noqa: E402

ADDED = ("pack", "goal_kinds", "question_kinds")
OLD = ("robot", "version", "plan_actions", "capabilities", "places", "site")
GOLDEN = snapshots.GOLDEN / "manifest_snapshots"

# The household needs as the manager's old table wrote them (robot_fleet scheduler _NEEDS, before the manifest reported them):
# the default data of the pack equals these constants, so a manager that reads the report sees what it had by hand.
HOUSEHOLD_NEEDS = {"on": ["Pick", "Place"], "in": ["Pick", ["PutIn", "Place"]], "open": ["Open"], "close": ["Close"],
                   "go": ["MoveTo"], "say": ["Say"], "deliver": ["MoveTo", "WaitForUnload"]}

RAW_LINE = {"schema": 2, "name": "line", "version": "1.0", "extends": "household", "assumed": True,
            "goal_kinds": {"mount": {"rules": "std.on", "needs": ["Pick", ["Place", "MoveTo"]],
                                     "slots": {"object": {"type": "object", "required": True}, "to": {"type": "place"}}},
                           "fasten": {"rules": "std.on", "needs": ["Pick"]}},
            "questions": {"far": {"kind": "line.decision", "answer": {"type": "yes_no"}},
                          "near": {"answer": {"type": "yes_no"}}, "again": {"kind": "line.decision", "answer": {"type": "yes_no"}}},
            "questions_default_kind": "line.question"}


def answer(pkg):
    return snapshots.manifest_snapshot(pkg)["manifest_of"]


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_the_answer_equals_the_s27a_snapshot_and_keeps_every_s01_key(pkg):
    want = snapshots.load(GOLDEN / f"s27a_{pkg}.json")
    old = snapshots.load(GOLDEN / f"s01_{pkg}.json")
    got = snapshots.manifest_snapshot(pkg)
    assert got == want
    assert all(want["manifest_of"][k] == v for k, v in old["manifest_of"].items())      # old keys: equal values
    assert want["manifest"] == old["manifest"]
    assert set(want["manifest_of"]) - set(old["manifest_of"]) == set(ADDED)


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_the_default_pack_reports_the_household_needs_and_no_question_kind(pkg):
    got = answer(pkg)
    assert got["pack"] == {"schema": 1, "name": "", "version": "", "extends": "household"}
    assert {k: v["needs"] for k, v in got["goal_kinds"].items()} == HOUSEHOLD_NEEDS
    assert list(got["goal_kinds"]) == list(HOUSEHOLD_NEEDS)                            # the table's order
    assert all(v["slots"] == {} for v in got["goal_kinds"].values()) and got["question_kinds"] == []


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_feasible_is_the_robots_own_rule(pkg):
    view = RobotView.load(pkg)
    for kind, row in answer(pkg)["goal_kinds"].items():
        assert row["feasible"] == (not goals.missing_actions({"type": kind}, view.plan_actions, view.pack)), (pkg, kind)
    assert {k for k, v in answer("fakeline")["goal_kinds"].items() if not v["feasible"]} == {"open", "close"}
    assert {k for k, v in answer("fakebot")["goal_kinds"].items() if not v["feasible"]} == {"say", "deliver"}


def test_a_pack_with_its_own_kinds_slots_and_questions_is_reported_as_declared():
    view = RobotView.load("fakeline")
    view.use_pack(RAW_LINE)
    got = robot_info.manifest_of(view, {}, None)
    assert got["pack"] == {"schema": 2, "name": "line", "version": "1.0", "extends": "household"}
    assert got["goal_kinds"]["mount"] == {"feasible": True, "needs": ["Pick", ["Place", "MoveTo"]], "slots": {"object": "object", "to": "place"}}
    assert got["goal_kinds"]["fasten"] == {"feasible": True, "needs": ["Pick"], "slots": {}}
    assert got["question_kinds"] == ["line.decision", "line.question"]                  # sorted, no duplicate, the default kind in
    old = RobotView.load("fakeline")
    assert {k: v for k, v in got.items() if k in OLD} == {k: v for k, v in robot_info.manifest_of(old, {}, None).items() if k in OLD}


def test_a_kind_the_robot_lacks_the_actions_for_is_listed_as_not_feasible_with_its_needs():
    view = RobotView.load("fakeline")
    raw = dict(RAW_LINE, goal_kinds={"mount": {"rules": "std.on", "needs": ["Pick", ["Place", "MoveTo"]]}})
    view.use_pack(raw)
    rows = report.goal_kinds(view.pack, goals.table(view.pack), lambda g: ["Swim"] if g["type"] == "mount" else [])
    assert rows["mount"]["feasible"] is False and rows["on"]["feasible"] is True and rows["mount"]["needs"] == ["Pick", ["Place", "MoveTo"]]


def test_a_view_with_no_readable_pack_gets_the_answer_it_had_before():
    class Plain:                                         # no `pack` at all (as tests/test_fleet_seams.py's View)
        manifest = types.SimpleNamespace(robot="zorbot", version="2.1", capabilities={"base": True})
        plan_actions = {"Pick"}

    class Broken(Plain):
        @property
        def pack(self):
            raise RuntimeError("the pack cannot be resolved")

    for view in (Plain(), Broken()):
        assert robot_info.manifest_of(view, {}, "s") == {"robot": "zorbot", "version": "2.1", "plan_actions": ["Pick"],
                                                         "capabilities": {"base": True}, "places": [], "site": "s"}


def test_the_endpoint_sends_the_added_keys_and_a_stub_view_still_gets_six_keys(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import robot_agent.core.planning.robot as robot_mod
    import robot_agent.skill_configs as sc
    views = {"fakeline": RobotView.load("fakeline")}
    monkeypatch.setattr(robot_info, "current", lambda: types.SimpleNamespace(robot_pkg="fakeline", location="here"))
    monkeypatch.setattr(robot_mod, "robot_for", lambda pkg: views.get(pkg))
    monkeypatch.setattr(sc, "ENV", {"bench@line": {}})
    app = FastAPI()
    app.include_router(robot_info.router)
    with TestClient(app) as client:
        body = client.get("/robot/manifest").json()
    assert set(body) == set(OLD) | set(ADDED) and body["places"] == ["bench@line"] and body["site"] == "here"
    assert body["goal_kinds"]["on"]["needs"] == ["Pick", "Place"] and body["question_kinds"] == []


def test_the_response_model_keeps_its_six_fields_and_the_openapi_schema_names_the_added_keys():
    from robot_agent.api.models import RobotManifest
    assert not set(ADDED) & set(RobotManifest.model_fields)                             # tests/test_manifest_snapshot.py pins the fields
    props = RobotManifest.model_json_schema()["properties"]
    assert set(ADDED) <= set(props) and set(OLD) <= set(props)
    assert "pack" not in RobotManifest.model_json_schema().get("required", [])          # optional: a robot without a readable pack
    assert RobotManifest(robot="r", version="1", pack={"schema": 1}).model_dump(exclude_unset=True)["pack"] == {"schema": 1}


def test_report_is_pure_data_in_data_out_and_imports_no_planning_code():
    tree = ast.parse(Path(report.__file__).read_text(encoding="utf-8"))
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("planning" in m or "api" in m for m in mods), mods
    assert report.question_kinds(types.SimpleNamespace(table=lambda name: {}, questions_default_kind=None)) == []
