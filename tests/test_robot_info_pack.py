"""Step S27a: ``GET /robot/manifest`` also reports the robot's pack, its goal kinds with their needs, and its question kinds.

These tests prove the answer only GREW: (1) the old keys equal a reference copy of the old ``manifest_of`` and the s01 snapshot;
(2) the added keys are pinned by the snapshot set ``tests/golden/manifest_snapshots/s27a_<pack>.json`` (written once on this step's code with the
never-overwrite writer of tests/golden/snapshots.py, the manifest files only; this test only reads them); (3) the default data equals the old
goal-kind constants (tests/legacy_ref/goal_tables.py) and ``feasible`` equals the old ``missing_actions`` over a grid of action
sets; (4) a view with no readable pack gets the old answer, key for key; (5) a pack with its own kinds and questions is reported,
and the HTTP answer carries the same data."""

import itertools
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

import snapshots  # noqa: E402
from legacy_ref import goal_tables as old  # noqa: E402
from robot_agent.api import models as M  # noqa: E402
from robot_agent.api import robot_info  # noqa: E402
from robot_agent.core.pack import Pack, default_pack, report  # noqa: E402
from robot_agent.core.pack import goal_kinds as kind_data  # noqa: E402
from robot_agent.core.planning import goals  # noqa: E402
from robot_agent.core.planning.robot import RobotView  # noqa: E402

PACKS = snapshots.PACKS
ADDED = {"pack", "goal_kinds", "question_kinds"}
ACTIONS = ("Pick", "Place", "PutIn", "Open", "Close", "MoveTo", "Say", "WaitForUnload", "Find", "Handover")
ACTION_SETS = [set(c) for n in (0, 1, 2, 3) for c in itertools.combinations(ACTIONS, n)] + [set(ACTIONS)]


def old_manifest_of(view, env, site) -> dict:
    """A reference copy of ``manifest_of`` as it was before Step S27a."""
    m = view.manifest
    return {'robot': str(m.robot), 'version': str(m.version), 'plan_actions': sorted(view.plan_actions),
            'capabilities': dict(m.capabilities or {}), 'places': [str(k) for k in env.keys()], 'site': str(site or '')}


def snap(pkg, prefix):
    return snapshots.load(snapshots.snapshot_path("manifest", pkg, prefix))


def missing_of(pack, actions):
    return lambda kind: goals.missing_actions({"type": kind}, actions, pack)


# ── (1) the old keys are unchanged ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pkg", PACKS)
def test_the_old_keys_equal_the_reference_copy_and_the_old_snapshot(pkg):
    env = snapshots._env(pkg)
    view = RobotView.load(pkg)
    got = robot_info.manifest_of(view, env, snapshots.SITE)
    assert {k: v for k, v in got.items() if k not in ADDED} == old_manifest_of(view, env, snapshots.SITE)
    want = snap(pkg, "s01")
    assert snapshots.recorded_keys(got, want["manifest_of"]) == want["manifest_of"]


@pytest.mark.parametrize("pkg", PACKS)
def test_the_s27a_snapshot_adds_exactly_three_keys_and_changes_no_old_one(pkg):
    new, was = snap(pkg, "s27a"), snap(pkg, "s01")
    assert new["manifest"] == was["manifest"]
    assert set(new["manifest_of"]) - set(was["manifest_of"]) == ADDED
    assert {k: new["manifest_of"][k] for k in was["manifest_of"]} == was["manifest_of"]


# ── (2) the added keys are pinned ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pkg", PACKS)
def test_the_manifest_answer_equals_the_s27a_snapshot(pkg):
    assert snapshots.manifest_snapshot(pkg) == snap(pkg, "s27a")


# ── (3) the default data is the old constants ────────────────────────────────────────────────────────────────────────
def _jsonable(need):
    return list(need) if isinstance(need, tuple) else need


def test_the_default_kinds_and_needs_are_the_old_goal_table():
    got = report.goal_kinds(default_pack(), lambda kind: [])
    assert list(got) == list(old.GOAL_KINDS)
    for name, k in old.GOAL_KINDS.items():
        assert got[name]["needs"] == [_jsonable(n) for n in k.needs], name
        assert got[name]["slots"] == {}, "the standard pack's kinds declare no slots yet"
    assert report.pack_header(default_pack()) == {"schema": 1, "name": "", "version": "", "extends": "household"}
    assert report.question_kinds(default_pack()) == []


@pytest.mark.parametrize("pack", [None, default_pack()])
def test_feasible_equals_the_old_missing_actions_over_a_grid_of_action_sets(pack):
    pack = pack or default_pack()
    for acts in ACTION_SETS:
        got = report.goal_kinds(pack, missing_of(pack, acts))
        assert {n: v["feasible"] for n, v in got.items()} == {n: not old.missing_actions({"type": n}, acts) for n in old.GOAL_KINDS}, acts


# ── (4) no readable pack: the old answer ─────────────────────────────────────────────────────────────────────────────
class Stub:
    manifest = types.SimpleNamespace(robot="zorbot", version="2.1", capabilities={"base": True})
    plan_actions = {"Pick", "MoveTo", "Place"}


class BrokenPack(Stub):
    @property
    def pack(self):
        raise RuntimeError("the pack cannot be resolved")


class NoPack(Stub):
    pack = None


@pytest.mark.parametrize("view", [Stub(), BrokenPack(), NoPack()])
def test_a_view_with_no_readable_pack_gets_the_old_answer_key_for_key(view):
    env = {"shelf@galley": {}}
    assert robot_info.manifest_of(view, env, "flat") == old_manifest_of(view, env, "flat")


# ── (5) a pack with its own kinds and questions ──────────────────────────────────────────────────────────────────────
INSTALL = {"needs": ["Measure", ["Seat", "Place"]], "slots": {"object": {"type": "object", "required": True}, "to": {"type": "place"}},
           "source": "a made-up robot of this test"}
MADE_UP = Pack(schema=2, name="made_up", version="3.0", extends="household", questions_default_kind="made_up.ask",
               tables={"goal_kinds": {"install": INSTALL, "go": {"needs": ["MoveTo"]}},
                       "questions": {"far": {"kind": "made_up.far"}, "near": {"kind": "made_up.far"}, "plain": {"phrase": "q"}}})


def test_a_pack_with_its_own_kinds_reports_needs_slots_and_the_robots_own_feasibility():
    got = report.fields(MADE_UP, missing_of(MADE_UP, {"Measure", "MoveTo"}))
    assert got["pack"] == {"schema": 2, "name": "made_up", "version": "3.0", "extends": "household"}
    assert list(got["goal_kinds"]) == ["install", "go"]
    assert got["goal_kinds"]["install"] == {"feasible": False, "needs": ["Measure", ["Seat", "Place"]], "slots": INSTALL["slots"]}
    assert got["goal_kinds"]["go"] == {"feasible": True, "needs": ["MoveTo"], "slots": {}}
    assert got["question_kinds"] == ["made_up.ask", "made_up.far"]
    for acts in ({"Measure", "Seat"}, {"Measure", "Place"}):     # an any-of group: one of its actions is enough
        assert report.goal_kinds(MADE_UP, missing_of(MADE_UP, acts))["install"]["feasible"] is True
    assert not kind_data.is_default(MADE_UP)


def test_a_pack_with_no_goal_kinds_reports_the_default_table_as_the_loop_reads_it():
    bare = Pack(schema=2, name="bare", tables={"questions": {"q": {"kind": "bare.q"}}})
    assert list(report.goal_kinds(bare, lambda kind: [])) == list(old.GOAL_KINDS) and report.question_kinds(bare) == ["bare.q"]


def test_the_http_answer_carries_the_added_keys_and_a_stub_view_gets_only_the_old_ones(monkeypatch):
    app = FastAPI()
    app.include_router(robot_info.router)
    client = TestClient(app)
    monkeypatch.setattr(robot_info, "current", lambda: types.SimpleNamespace(robot_pkg="fakebot", location="golden"))
    view = RobotView.load("fakebot")
    from robot_agent.core.planning import robot as R
    monkeypatch.setattr(R, "robot_for", lambda pkg: view)
    body = client.get("/robot/manifest").json()
    want = robot_info.manifest_of(view, body and {k: 1 for k in body["places"]}, "golden")
    assert body == want and ADDED <= set(body)
    monkeypatch.setattr(R, "robot_for", lambda pkg: Stub())
    assert set(client.get("/robot/manifest").json()) == {"robot", "version", "plan_actions", "capabilities", "places", "site"}


def test_the_response_model_of_the_old_keys_is_unchanged_and_the_new_one_extends_it():
    assert set(M.RobotManifest.model_fields) == {"robot", "version", "plan_actions", "capabilities", "places", "site"}
    assert set(M.RobotManifestPack.model_fields) == set(M.RobotManifest.model_fields) | ADDED
