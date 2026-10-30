"""check_manifest: manifest <-> skill registry / signatures, without importing the robot."""

import json
from pathlib import Path

import pytest

from robot_agent.contract import check_manifest as cm

KCARE_PKGS = [Path("/home/trung/trung_workdir/arch_wt/kcare_robot-hygiene-manifest"),
              Path(__file__).resolve().parents[2] / "kcare_robot"]

SKILLS_CONFIG = """_PKG = 'toybot.skills'

SKILL_CONFIGS: dict[str, tuple[str, str]] = {
    'move':  (f'{_PKG}.mobile', 'move'),
    'pick':  (f'{_PKG}.arm',    'pick'),
    'check': (f'{_PKG}.arm',    'grasp_ok'),
    'lost':  (f'{_PKG}.gone',   'lost'),
}
"""
MOBILE = "def move(node, **kwargs):\n    return {'isdone': True}\n"
ARM = ("def pick(node, target, speed=1.0):\n    return {'isdone': True}\n\n"
       "def grasp_ok(node):\n    return {'isdone': True}\n")
MANIFEST = """robot: toy
skills:
  - name: move
    action: MoveTo
    params: {type: object, properties: {inputs: {type: string}}, required: [inputs]}
  - name: pick
    action: Pick
    params: {type: object, properties: {target: {type: string}, speed: {type: number}}, required: [target]}
    verify: [{kind: sensor, skill: check}]
  - name: pour
    action: Pour
    note: made-up key
"""


def make_pkg(root: Path, manifest: str = MANIFEST, skills_json=None) -> Path:
    pkg = root / "toybot_repo" / "toybot"
    (pkg / "configs").mkdir(parents=True)
    (pkg / "skills").mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "configs" / "skills_config.py").write_text(SKILLS_CONFIG)
    (pkg / "configs" / "manifest.yaml").write_text(manifest)
    (pkg / "configs" / "grace_namemap.py").write_text('SKILL_MAP = {"MoveTo": "move", "Pick": "grab"}\n')
    (pkg / "skills" / "mobile.py").write_text(MOBILE)
    (pkg / "skills" / "arm.py").write_text(ARM)
    if skills_json is not None:
        (pkg / "configs" / "common").mkdir()
        (pkg / "configs" / "common" / "skills.json").write_text(json.dumps(skills_json))
    return pkg


def test_read_skill_configs_follows_fstrings(tmp_path):
    pkg = make_pkg(tmp_path)
    cfg = cm.read_skill_configs(pkg / "configs" / "skills_config.py")
    assert cfg["pick"] == ("toybot.skills.arm", "pick")


def test_check_reports_errors_and_warnings(tmp_path):
    pkg = make_pkg(tmp_path)
    rep = cm.check(pkg.parent)                       # the repo root resolves to the package
    assert rep.pkg_dir == pkg and not rep.ok
    assert rep.errors == ["skill 'pour' (Pour): not in the skill registry (configs/skills_config.py / skills.json)"]
    assert any("'note' is not part of the contract" in w for w in rep.warnings)
    assert any("SKILL_MAP binds 'grab'" in w for w in rep.warnings)


def test_param_mismatches(tmp_path):
    bad = MANIFEST.replace("{target: {type: string}, speed: {type: number}}, required: [target]",
                           "{speed: {type: number}, force: {type: number}}")
    rep = cm.check(make_pkg(tmp_path, bad.split("  - name: pour")[0]))
    assert sorted(rep.errors) == [
        "skill 'pick' (Pick): param 'force' is not accepted by pick() (takes speed, target)",
        "skill 'pick' (Pick): pick() requires 'target', which the manifest params do not declare"]


def test_missing_module_function_and_sensor(tmp_path):
    m = """robot: toy
skills:
  - {name: lost, action: Find}
  - {name: check, action: Verify, verify: [{kind: sensor, skill: nope}]}
"""
    rep = cm.check(make_pkg(tmp_path, m))
    assert any("module 'toybot.skills.gone' not found" in e for e in rep.errors)
    assert any("verify skill 'nope' is not a registered skill" in e for e in rep.errors)


def test_skills_json_and_aliases(tmp_path):
    sj = [{"name": "pour", "type": "plan", "plan": "move::sink"},
          {"name": "move", "type": "internal", "module_path": "toybot.skills.mobile", "func_name": "move",
           "aliases": ["go"]}]
    m = MANIFEST.replace("name: move", "name: go")
    rep = cm.check(make_pkg(tmp_path, m, skills_json=sj))
    assert rep.ok, rep.format()


def test_invalid_manifest_is_reported_by_field(tmp_path):
    rep = cm.check(make_pkg(tmp_path, "robot: toy\nskills:\n  - {name: move, action: MoveTo, errors: [boom]}\n"))
    assert rep.errors and rep.errors[0].startswith("manifest: skills[0] (move).errors[0]: 'boom'")


def test_cli_exit_codes(tmp_path, capsys):
    pkg = make_pkg(tmp_path, MANIFEST.split("  - name: pour")[0])
    assert cm.main([str(pkg)]) == 0                          # the SKILL_MAP warning alone passes
    assert cm.main([str(pkg), "--strict"]) == 1
    assert cm.main([str(tmp_path / "nowhere")]) == 2
    assert "0 error(s)" in capsys.readouterr().out
    with pytest.raises(AssertionError, match="SKILL_MAP"):
        cm.assert_manifest_matches(pkg, strict=True)


@pytest.mark.parametrize("root", KCARE_PKGS, ids=["hygiene-manifest-worktree", "sibling-kcare_robot"])
def test_kcare_manifest_matches_its_skills(root):
    try:
        pkg = cm.resolve_pkg_dir(root)
    except FileNotFoundError:
        pytest.skip(f"{root} not on disk")
    if not (pkg / "configs" / "manifest.yaml").is_file():
        pytest.skip(f"{pkg} has no manifest yet")
    rep = cm.check(pkg)
    assert rep.ok, rep.format()
