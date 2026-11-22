"""GET /robot/manifest of the made-up packs is frozen (Step S01 of docs/proposals/generalize_core_design.md): ``manifest_of``'s
answer and the whole parsed manifest of fakebot, fakehome and fakeline equal the snapshots recorded on the old code
(tests/golden/manifest_snapshots). A later step may only ADD keys (owner's decision 5): this test then fails and the step adds
its own snapshot, it never edits these. Never writes a snapshot (tools/golden_record.py --snapshots does, refusing to overwrite)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

import snapshots  # noqa: E402


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_the_manifest_answer_and_the_parsed_manifest_equal_the_snapshot(pkg):
    want = snapshots.load(snapshots.GOLDEN / "manifest_snapshots" / f"s01_{pkg}.json")
    got = snapshots.manifest_snapshot(pkg)
    assert got["manifest_of"] == want["manifest_of"], pkg
    assert got["manifest"] == want["manifest"], pkg


def test_the_answer_has_the_keys_of_the_response_model():
    from robot_agent.api.models import RobotManifest
    for pkg in snapshots.PACKS:
        want = snapshots.load(snapshots.GOLDEN / "manifest_snapshots" / f"s01_{pkg}.json")
        assert set(RobotManifest.model_fields) <= set(want["manifest_of"]), pkg
        assert want["manifest_of"]["robot"] and want["manifest_of"]["plan_actions"] and want["manifest_of"]["capabilities"]
