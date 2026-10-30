"""v2 guide: which planners use the Planner Guide (GET /guides reports it), and the new seed
version (guide_v2) added once to an existing guides.json, never active."""

import json
import sys
import types

import pytest

from robot_agent.core import guide_manager as gm


@pytest.mark.parametrize("planner, uses", [("direct", True), ("llm_direct", True), ("grace", False),
                                           ("sage", False)])
def test_planner_uses_guide(planner, uses):
    assert gm.planner_uses_guide(planner) is uses


def test_sage_with_a_direct_generator_uses_it(monkeypatch):
    monkeypatch.setenv("ROBOT_AGENT_SAGE_GENERATOR", "llm_direct")
    assert gm.planner_uses_guide("sage")


def test_default_planner(monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_CLOSED_LOOP", raising=False)
    assert gm.default_planner() == "direct" and gm.planner_uses_guide(None)
    monkeypatch.setenv("ROBOT_AGENT_CLOSED_LOOP", "1")
    monkeypatch.setenv("ROBOT_AGENT_PLANNER", "sage")
    assert gm.default_planner() == "sage" and not gm.planner_uses_guide(None)


def test_plugin_declares_it(monkeypatch):
    mod = types.ModuleType("guide_plug")

    class P:
        uses_guide = True
    mod.P = P
    monkeypatch.setitem(sys.modules, "guide_plug", mod)
    assert gm.planner_uses_guide("guide_plug:P")
    assert not gm.planner_uses_guide("no_such_mod:X")


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    root = tmp_path / "src" / "gpkg" / "configs"
    root.mkdir(parents=True)
    (root.parent / "__init__.py").write_text("")
    (root / "__init__.py").write_text("")
    (root / "guide.py").write_text('GUIDE = "old guide"\nFORMAT = None\n')
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    for m in [m for m in sys.modules if m.startswith("gpkg")]:
        monkeypatch.delitem(sys.modules, m)
    return root


def test_new_seed_added_once_inactive(tmp_path, pkg):
    data = tmp_path / "data"
    data.mkdir()
    (data / "guides.json").write_text(json.dumps({"active": "guide", "versions": {"guide": {"guide": "edited",
                                                                                           "format": None}}}))
    (pkg / "guide_v2.py").write_text('GUIDE = "{ROBOT}"\nFORMAT = None\n')
    m = gm.GuideManager(data, "gpkg")
    lst = m.list()
    assert lst["active"] == "guide" and [v["name"] for v in lst["versions"]] == ["guide", "guide_v2"]
    assert m.get("guide")["guide"] == "edited"
    m.delete("guide_v2")
    assert m.get("guide_v2") is None
    assert gm.GuideManager(data, "gpkg").get("guide_v2") is None          # a delete sticks
    assert "guide_v2" in json.loads((data / "guides.json").read_text())["seeded"]


def test_legacy_store_keeps_deleted_legacy_seeds_deleted(tmp_path, pkg):
    data = tmp_path / "data"
    data.mkdir()
    (data / "guides.json").write_text(json.dumps({"active": "x", "versions": {"x": {"guide": "g"}}}))
    m = gm.GuideManager(data, "gpkg")                     # 'guide' was deleted by the operator before
    assert m.get("guide") is None and m.list()["active"] == "x"


def test_api_reports_usage(monkeypatch, tmp_path, pkg):
    pytest.importorskip("fastapi")
    from robot_agent.api import guides as api
    st = types.SimpleNamespace(guides=gm.GuideManager(tmp_path / "d", "gpkg"), robot_pkg="gpkg")
    monkeypatch.setattr(api, "current", lambda: st)
    out = api.list_guides(planner="sage")
    assert out["used_by"] == ["direct", "llm_direct"] and out["active_planner"] == "sage"
    assert out["active_planner_uses_guide"] is False
    assert api.list_guides(planner="direct")["active_planner_uses_guide"] is True
    assert out["versions"][0]["placeholders"] == []
    prev = api.preview_guide(api.GuidePreviewReq(guide="say COMMAND_HERE", command="hi"), planner="grace")
    assert prev["rendered"] == "say hi" and prev["legacy"] and prev["active_planner_uses_guide"] is False
