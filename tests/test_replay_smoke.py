"""``robot_agent/observe/replay.py`` still replays a run recorded on the old code (Step S01 of
docs/proposals/generalize_core_design.md, for Step S24 and invariant 14): the assist run of fakebot in
tests/golden/replay_runs/s01_fetch (the cassette scenario fetch_success, recorded through the RunManager with the run record
on) replays identically in ``exec`` (the fake robot returns the recorded skill results) and in ``plan`` mode."""

from pathlib import Path

import pytest

RECORD = Path(__file__).resolve().parent / "golden" / "replay_runs" / "s01_fetch" / "record.jsonl"


@pytest.fixture(autouse=True)
def _no_agent_env(monkeypatch):
    import os
    for k in [k for k in os.environ if k.startswith("ROBOT_AGENT_")]:
        monkeypatch.delenv(k)


@pytest.mark.parametrize("mode", ["exec", "plan"])
def test_the_recorded_run_replays_identically(mode):
    from robot_agent.observe import replay as RP
    res = RP.replay(RP.load(RECORD), mode)
    assert res.error is None and res.identical, res.summary()
    assert res.loop == "assist" and res.unused_llm == 0


def test_the_record_is_an_assist_run_of_a_made_up_pack():
    from robot_agent.observe import replay as RP
    rec = RP.load(RECORD)
    assert rec.context.get("loop") == "assist"
    assert "fakebot" in str(rec.context)
