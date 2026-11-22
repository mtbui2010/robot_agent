"""The planner-side prompts are golden (Step S01 of docs/proposals/generalize_core_design.md, for Step S10): the harness replaces
the planner, so SAGE's robot prompt (``robot_prompt.sage_prompt_kwargs``: ORDER, ACTION_TEXT), the direct planner's prompt and
``_planner_cfg()`` are pinned here for fakebot, fakehome and fakeline, in full (prompt_snapshots), and by hash in the cassettes
with the ``prompts`` capture. The core English phrase table is pinned too: no run reads some of its entries (``rb_go``).
Never writes a snapshot."""

import pytest

import harness
import snapshots


@pytest.mark.parametrize("pkg", snapshots.PACKS)
def test_the_planner_prompts_equal_the_snapshot(pkg):
    want = snapshots.load(snapshots.GOLDEN / "prompt_snapshots" / f"s01_{pkg}.json")
    got = snapshots.prompt_snapshot(pkg)
    for key in ("sage_prompt_kwargs", "direct_prompt", "planner_cfg"):
        assert got[key] == want[key], f"{pkg}: {key} changed"


def test_the_core_phrase_table_is_unchanged():
    want = snapshots.load(snapshots.GOLDEN / "prompt_snapshots" / "s01_core_phrases.json")
    assert snapshots.core_phrases() == want


def test_a_cassette_of_each_made_up_pack_records_the_prompts():
    packs = set()
    for p in harness.cassettes():
        cas = harness.load(p)
        if "prompts" in harness.capture_of(cas["scenario"]):
            assert cas["recorded"].get("prompts", {}).get("sage_prompt_kwargs"), p.name
            packs.add(cas["scenario"].get("robot_pkg", "fakebot"))
    assert {"fakebot", "fakehome", "fakeline"} <= packs, packs
