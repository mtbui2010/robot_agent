"""v2 memory: the manifest's optional ``planner:`` section (``seed_memory``), how the core reads it
(``RobotView.seed_memory``) and how check_manifest validates it."""

import json

import pytest

from robot_agent.contract import Manifest, ManifestError
from robot_agent.contract.check_manifest import _check_planner, Report

BASE = {"robot": "r", "skills": [{"name": "move", "action": "MoveTo"}]}


def test_planner_is_optional():
    m = Manifest.from_dict(BASE)
    assert m.planner == {} and m.seed_memory is None
    assert "planner" not in m.to_dict() and "planner" not in m.extra


def test_seed_memory_relative_to_the_manifest(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "planner").mkdir()
    seed = tmp_path / "planner" / "seed.jsonl"
    seed.write_text(json.dumps({"task": "t", "plan_text": '{"steps": []}'}) + "\n")
    f = tmp_path / "configs" / "manifest.yaml"
    f.write_text("robot: r\nplanner: {seed_memory: ../planner/seed.jsonl}\nskills: []\n")
    m = Manifest.from_yaml(f)
    assert m.seed_memory == seed.resolve()
    assert m.to_dict()["planner"] == {"seed_memory": "../planner/seed.jsonl"}
    rep = Report(pkg_dir=tmp_path, manifest=m)
    _check_planner(m, rep)
    assert rep.errors == [] and rep.warnings == []


@pytest.mark.parametrize("bad, where", [("planner: [a]", "planner: must be a mapping"),
                                        ("planner: {seed_memory: 3}", "planner.seed_memory")])
def test_bad_planner_section(tmp_path, bad, where):
    f = tmp_path / "manifest.yaml"
    f.write_text(f"robot: r\n{bad}\nskills: []\n")
    with pytest.raises(ManifestError) as e:
        Manifest.from_yaml(f)
    assert any(where in x for x in e.value.errors)


def test_check_manifest_seed_file(tmp_path):
    m = Manifest.from_dict({**BASE, "planner": {"seed_memory": str(tmp_path / "none.jsonl"), "other": 1}},
                           source=str(tmp_path / "manifest.yaml"))
    rep = Report(pkg_dir=tmp_path, manifest=m)
    _check_planner(m, rep)
    assert any("no file" in e for e in rep.errors) and any("planner.other" in w for w in rep.warnings)
    (tmp_path / "none.jsonl").write_text('{"task": "t"}\nnot json\n')
    rep = Report(pkg_dir=tmp_path, manifest=m)
    _check_planner(m, rep)
    assert len(rep.errors) == 2 and "line 1 needs" in rep.errors[0] and "line 2 is not JSON" in rep.errors[1]


def test_robot_view_seed_memory(tmp_path):
    from robot_agent.contract import NamemapAdapter
    from robot_agent.core.planning.robot import RobotView
    import types
    nm = types.SimpleNamespace(SKILL_MAP={"MoveTo": "move"})
    plain = RobotView(NamemapAdapter(nm, Manifest.from_dict(BASE)))
    assert plain.seed_memory() is None
    m = Manifest.from_dict({**BASE, "planner": {"seed_memory": "seed.jsonl"}}, source=str(tmp_path / "m.yaml"))
    assert RobotView(NamemapAdapter(nm, m)).seed_memory() == str(tmp_path / "seed.jsonl")
