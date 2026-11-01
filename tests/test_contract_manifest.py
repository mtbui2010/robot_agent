"""robot_agent.contract.Manifest: loading, validation errors that name the field, lookups."""

from pathlib import Path

import pytest

from robot_agent.contract import FailureCode, Manifest, ManifestError, load_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "contract" / "manifest_kaair.yaml"
# the kcare checkout next to this repo; checked when it is on disk
KCARE_MANIFESTS = [
    Path(__file__).resolve().parents[2] / "kcare_robot" / "kcare_robot" / "configs" / "manifest.yaml",
]


def _base(**kw):
    d = {"robot": "r", "skills": [{"name": "move", "action": "MoveTo"}]}
    d.update(kw)
    return d


def test_fixture_loads():
    m = Manifest.from_yaml(FIXTURE)
    assert (m.robot, m.version, m.language) == ("kaair", "1.0", "ko")
    assert m.skill_names == ["move", "find", "pick", "place", "open_drawer", "close_drawer"]
    pick = m.skill_for("Pick")
    assert pick.name == "pick" and pick.interruptible is False
    assert [v.kind for v in pick.verify] == ["symbolic", "sensor"] and pick.verify[1].skill == "grasp_succeed"
    assert pick.errors == [FailureCode.GRASP_FAILED, FailureCode.UNREACHABLE, FailureCode.PERCEPTION_UNAVAILABLE]
    assert pick.pre == ["found(?inputs)", "hand_empty()"] and pick.hold == ["not estop()"]
    assert m.skill_for("Find").verify[0].kind == "symbolic"           # string shorthand
    assert m.skill("place").extra == {"description": "places the held object"}
    assert m.skill("move").required_params == ["inputs"]
    assert m.geometry_obj().reach_m == 0.78
    assert m.source == str(FIXTURE)


def test_capability_helpers():
    m = Manifest.from_yaml(FIXTURE)
    assert m.has("base") and m.has("drawer") and m.has("hands") and m.has("arms")
    assert not m.has("legs")
    assert m.hands == ["right"] and m.arms == 1
    base_only = Manifest.from_dict({"robot": "b", "capabilities": {"base": True, "arms": 0, "hands": []}})
    assert not base_only.has("arms") and not base_only.has("hands") and base_only.hands == []
    assert Manifest.from_dict({"robot": "h", "capabilities": {"hands": 2}}).hands == ["left", "right"]


def test_skill_for_case_insensitive_and_missing():
    m = Manifest.from_yaml(FIXTURE)
    assert m.skill_for("moveto").name == "move"
    assert m.skill_for("TurnOn") is None
    assert m.actions == {"MoveTo", "Find", "Pick", "Place", "Open", "Close"}


def test_round_trip_through_dict():
    m = Manifest.from_yaml(FIXTURE)
    again = Manifest.from_dict(m.to_dict())
    assert again.to_dict() == m.to_dict()


def test_bare_predicates_alias_actions_and_free_geometry():
    m = Manifest.from_dict(_base(
        geometry={"reach_m": 0.7, "home_place": "living room"},
        skills=[{"name": "place", "action": "Place", "alias_actions": ["PutIn"],
                 "pre": ["not hand_empty"], "effects": ["hand_empty", "not holding(*)"]}]))
    assert m.skill_for("PutIn").name == "place" and m.actions == {"Place", "PutIn"}
    assert m.skill("place").pre == ["not hand_empty"]
    with pytest.raises(ManifestError, match="alias_actions"):
        Manifest.from_dict(_base(skills=[{"name": "p", "action": "Place", "alias_actions": "PutIn"}]))
    with pytest.raises(ManifestError, match="geometry.base_radius_m"):
        Manifest.from_dict(_base(geometry={"base_radius_m": "big"}))


def test_unquoted_version_and_extra_top_keys():
    m = Manifest.from_dict(_base(version=2.1, connections={"ros": "x"}))
    assert m.version == "2.1" and m.extra == {"connections": {"ros": "x"}}


@pytest.mark.parametrize("bad, field", [
    ({"skills": []}, "robot:"),
    (_base(robot=""), "robot:"),
    (_base(language=3), "language:"),
    (_base(capabilities=[1]), "capabilities:"),
    (_base(capabilities={"base": "yes"}), "capabilities.base:"),
    (_base(capabilities={"hands": "right"}), "capabilities.hands:"),
    (_base(capabilities={"arms": -1}), "capabilities.arms:"),
    (_base(geometry={"reach_m": "far"}), "geometry.reach_m:"),
    (_base(skills={"move": 1}), "skills:"),
    (_base(skills=["move"]), "skills[0]:"),
    (_base(skills=[{"action": "MoveTo"}]), "skills[0].name:"),
    (_base(skills=[{"name": "move", "action": "move_to"}]), "skills[0] (move).action:"),
    (_base(skills=[{"name": "move", "action": "MoveTo", "errors": ["absent", "oops"]}]),
     "skills[0] (move).errors[1]: 'oops' is not a FailureCode"),
    (_base(skills=[{"name": "pick", "action": "Pick", "verify": [{"kind": "sensor"}]}]),
     "skills[0] (pick).verify[0].skill:"),
    (_base(skills=[{"name": "pick", "action": "Pick", "verify": [{"kind": "camera"}]}]),
     "skills[0] (pick).verify[0].kind:"),
    (_base(skills=[{"name": "pick", "action": "Pick", "pre": ["holding ?x"]}]), "skills[0] (pick).pre[0]:"),
    (_base(skills=[{"name": "pick", "action": "Pick", "interruptible": "no"}]),
     "skills[0] (pick).interruptible:"),
    (_base(skills=[{"name": "pick", "action": "Pick", "params": {"type": "array"}}]),
     "skills[0] (pick).params.type:"),
    (_base(skills=[{"name": "pick", "action": "Pick", "params": {"properties": {"x": {"type": "str"}}}}]),
     "skills[0] (pick).params.properties.x.type:"),
    (_base(skills=[{"name": "pick", "action": "Pick", "params": {"properties": {}, "required": ["x"]}}]),
     "skills[0] (pick).params.required: 'x'"),
    (_base(skills=[{"name": "move", "action": "MoveTo"}, {"name": "move", "action": "Find"}]),
     "skills[1].name: 'move' is already declared at skills[0]"),
])
def test_validation_names_the_bad_field(bad, field):
    with pytest.raises(ManifestError) as ei:
        Manifest.from_dict(bad)
    assert any(e.startswith(field) for e in ei.value.errors), ei.value.errors


def test_all_errors_reported_at_once_with_the_file(tmp_path):
    f = tmp_path / "manifest.yaml"
    f.write_text("robot: ''\nskills:\n  - {name: a, action: x, errors: [nope]}\n")
    with pytest.raises(ManifestError) as ei:
        Manifest.from_yaml(f)
    assert len(ei.value.errors) == 3 and str(f) in str(ei.value)
    f.write_text("robot: [unclosed\n")
    with pytest.raises(ManifestError, match="not valid YAML"):
        Manifest.from_yaml(f)
    with pytest.raises(ManifestError, match="top level must be a mapping"):
        Manifest.from_dict([1])


def test_load_manifest_from_dir_and_file(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "manifest.yaml").write_text(FIXTURE.read_text())
    assert load_manifest(tmp_path).robot == "kaair"
    assert load_manifest(FIXTURE).robot == "kaair"
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "empty")


@pytest.mark.parametrize("path", KCARE_MANIFESTS, ids=["sibling-kcare_robot"])
def test_kcare_manifest_if_present(path):
    if not path.is_file():
        pytest.skip(f"{path} not written yet")
    m = Manifest.from_yaml(path)
    assert m.robot and m.skills
    for action in ("MoveTo", "Find", "Pick", "Place"):
        assert m.skill_for(action) is not None, f"{path}: no skill for {action}"
