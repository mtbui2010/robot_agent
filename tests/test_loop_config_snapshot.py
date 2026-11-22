"""The resolved config of the loop is frozen (Step S01 of docs/proposals/generalize_core_design.md, for Step S24): every key of
``AssistLoop.cfg`` with the engine's defaults and no ROBOT_AGENT_* variable, for fakebot, fakehome and fakeline, equals the
snapshot recorded on the old code (tests/golden/config_snapshots). The environment's precedence is in the cassette
``env_override``. Never writes a snapshot."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

import snapshots  # noqa: E402


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_the_resolved_config_equals_the_snapshot(pkg):
    want = snapshots.load(snapshots.GOLDEN / "config_snapshots" / f"s01_{pkg}.json")
    got = snapshots.config_snapshot(pkg)
    assert sorted(got) == sorted(want), (sorted(set(got) ^ set(want)))
    assert {k: v for k, v in got.items() if want[k] != v} == {}
