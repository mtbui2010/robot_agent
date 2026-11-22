"""GET /robot/manifest of the made-up packs is frozen (Step S01 of docs/proposals/generalize_core_design.md): every key that
``manifest_of``'s answer and the parsed manifest of fakebot, fakehome and fakeline had on the old code (tests/golden/
manifest_snapshots) is still there with the same value. A later step may only ADD top-level keys (owner's decision 5, Step
S27a: pack, goal_kinds, question_kinds): an added key passes here, a dropped or changed one fails; the step pins its added
keys in its own snapshot (``tools/golden_record.py --snapshots --prefix <step>``) and edits nothing here. Never writes a
snapshot (tools/golden_record.py --snapshots does, refusing to overwrite)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

import snapshots  # noqa: E402


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_the_manifest_answer_and_the_parsed_manifest_equal_the_snapshot(pkg):
    want = snapshots.load(snapshots.GOLDEN / "manifest_snapshots" / f"s01_{pkg}.json")
    got = snapshots.manifest_snapshot(pkg)
    for part in ("manifest_of", "manifest"):
        assert snapshots.recorded_keys(got[part], want[part]) == want[part], (pkg, part)


def test_an_added_key_passes_and_a_dropped_or_changed_key_fails():
    want = snapshots.load(snapshots.GOLDEN / "manifest_snapshots" / "s01_fakebot.json")["manifest_of"]
    key = sorted(want)[0]
    assert snapshots.recorded_keys(dict(want, goal_kinds=["on"]), want) == want
    assert snapshots.recorded_keys({k: v for k, v in want.items() if k != key}, want) != want
    assert snapshots.recorded_keys(dict(want, **{key: "changed"}), want) != want


def test_the_answer_has_the_keys_of_the_response_model():
    from robot_agent.api.models import RobotManifest
    for pkg in snapshots.PACKS:
        want = snapshots.load(snapshots.GOLDEN / "manifest_snapshots" / f"s01_{pkg}.json")
        assert set(RobotManifest.model_fields) <= set(want["manifest_of"]), pkg
        assert want["manifest_of"]["robot"] and want["manifest_of"]["plan_actions"] and want["manifest_of"]["capabilities"]
