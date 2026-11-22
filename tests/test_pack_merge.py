"""Step S30: the merge of ``extends`` and ``remove`` takes effect in a RUN (opt-in: a robot with a pack of its own).

The loader (robot_agent/core/pack/loader.py, Step S02) already merges the chain core -> base -> robot: an entry of a later pack
replaces the base entry whole and keeps its place, ``remove`` deletes names. Step S07 made the goal-kind table read from the
resolved pack. This file proves, on the real standard pack and made-up robots, that the two meet: (1) the default is untouched
(a pack that only extends the standard pack, or removes nothing, reads the default table; the default chain still gives the old
constants); (2) the merge equals an independent reference implementation over a grid of made-up chains; (3) a removed kind
leaves the understand prompt, the schema enum, the missing-action enum and the refusal of its goals; a replaced kind brings its
own prompt line; (4) the two feature cassettes of tests/golden/scenario_sets/s30_merge.py differ from their base cassettes by
exactly those lines and nothing else. No production code changed in this step: the loader needed no edit."""

import copy
import difflib
import itertools
import json
import sys
from pathlib import Path

import pytest

from legacy_ref import goal_tables as old
from robot_agent.contract.manifest import Manifest
from robot_agent.contract.pack import parse
from robot_agent.core.pack import default_pack, loader, resolve
from robot_agent.core.pack import goal_kinds as kind_data
from robot_agent.core.planning import goals

GOLDEN = Path(__file__).resolve().parent / "golden"
if str(GOLDEN) not in sys.path:
    sys.path.insert(0, str(GOLDEN))

import harness  # noqa: E402
import harness_sets as HS  # noqa: E402
import scenario_sets  # noqa: E402

DEFAULT = default_pack()
ACTIONS = {"Pick", "Place", "PutIn", "Open", "Close", "MoveTo", "Say", "WaitForUnload", "Find"}
SET = {sc["name"]: sc for sc, _t, _a in scenario_sets.load_set(GOLDEN / "scenario_sets" / "s30_merge.py")}
PACK = SET["s30_remove_replace"]["pack"]
REMOVE_ONLY = SET["s30_removed_kind_refused"]["pack"]


def _resolve(raw):
    """The pack of a made-up robot that has every action of ``ACTIONS`` (the check that a kind's needs are declared)."""
    manifest = Manifest.from_dict({"robot": "toy", "skills": [{"name": a.lower(), "action": a} for a in sorted(ACTIONS)]})
    return resolve(manifest, None, raw=raw)


# ── (1) the default is untouched ─────────────────────────────────────────────────────────────────────────────────────
def test_a_pack_that_only_extends_the_standard_pack_reads_the_default_table():
    p = _resolve({"schema": 2, "name": "plain", "extends": "household", "assumed": True})
    assert p.chain[-2:] == ("std", "plain") and p.problems == []
    assert p.tables["goal_kinds"] == DEFAULT.tables["goal_kinds"] and p.origin["goal_kinds"] == {k: "std" for k in p.table("goal_kinds")}
    assert kind_data.is_default(p) and goals.table(p) is goals.GOAL_KINDS


def test_a_pack_that_removes_nothing_that_exists_changes_no_table_and_the_miss_is_recorded():
    p = _resolve({"schema": 2, "name": "typo", "extends": "household", "assumed": True, "remove": {"goal_kinds": ["polish"]}})
    assert p.tables["goal_kinds"] == DEFAULT.tables["goal_kinds"] and goals.table(p) is goals.GOAL_KINDS
    assert [(x.kind, x.path) for x in p.problems] == [("remove", "remove.goal_kinds")]


def test_the_default_chain_still_gives_the_old_constants():
    assert set(goals.GOAL_KINDS) == set(old.GOAL_KINDS) and list(goals.GOAL_KINDS) == list(old.GOAL_KINDS)
    for name, kind in goals.GOAL_KINDS.items():
        assert (kind.needs, kind.prompt) == (old.GOAL_KINDS[name].needs, old.GOAL_KINDS[name].prompt), name
    assert goals.table(DEFAULT) is goals.GOAL_KINDS


# ── (2) the merge equals a reference implementation ──────────────────────────────────────────────────────────────────
def _reference(chain):
    """The merge rule written again from the docstring of loader.py, with plain dicts and lists."""
    out = {}
    for d in chain:
        for t, names in d.remove.items():
            for n in names:
                out.setdefault(t, {}).pop(n, None)
        for t, entries in d.tables.items():
            for n, e in entries.items():
                out.setdefault(t, {})[n] = copy.deepcopy(e)
    return out


def _decl(name, kinds=None, remove=None, phrases=None):
    raw = {"schema": 2, "name": name, "assumed": True}
    if kinds is not None:
        raw["goal_kinds"] = {k: {"rules": "x.r", "needs": [f"A_{name}_{k}"]} for k in kinds}
    if phrases is not None:
        raw["phrases"] = {p: {"en": f"{name} {p}"} for p in phrases}
    if remove:
        raw["remove"] = remove
    return parse(raw, name)


NAMES = ("on", "in", "go", "mount")
CHAINS = [[_decl("a", ks), _decl("b", kb, {"goal_kinds": list(rb)} if rb else None), _decl("c", kc, {"goal_kinds": list(rc)} if rc else None)]
          for ks, kb, rb, kc, rc in itertools.product(
              (NAMES[:3], NAMES), ((), ("in",), ("mount", "on")), ((), ("go",)), ((), ("on", "mount")), ((), ("on", "in")))]


@pytest.mark.parametrize("chain", CHAINS, ids=lambda c: "-".join(f"{len(d.tables.get('goal_kinds', {}))}r{len(d.remove.get('goal_kinds', ()))}" for d in c))
def test_the_merge_equals_the_reference_over_a_grid_of_made_up_chains(chain):
    tables, origin, _patterns, problems = loader.merge(chain)
    want = _reference(chain)
    assert {t: v for t, v in tables.items() if v} == {t: v for t, v in want.items() if v}
    assert list(tables["goal_kinds"]) == list(want.get("goal_kinds", {})), "the order of the table is the order of the reference"
    assert set(origin["goal_kinds"]) == set(tables["goal_kinds"])
    assert all(origin["goal_kinds"][n] == next(d.name for d in chain[::-1] if n in d.tables.get("goal_kinds", {})) for n in tables["goal_kinds"])
    misses = sum(1 for p in problems if p.kind == "remove")
    held, count = set(), 0
    for d in chain:
        for n in d.remove.get("goal_kinds", ()):
            count += n not in held
            held.discard(n)
        held |= set(d.tables.get("goal_kinds", {}))
    assert misses == count


def test_the_grid_has_the_chains_it_claims():
    assert len(CHAINS) == 2 * 3 * 2 * 2 * 2


# ── (3) a removed kind leaves the run; a replaced kind brings its own line ───────────────────────────────────────────
def test_the_merged_table_keeps_the_place_of_a_replaced_kind_and_drops_the_removed_ones():
    p = _resolve(PACK)
    assert list(p.table("goal_kinds")) == ["on", "in", "go", "say", "deliver"] and list(DEFAULT.table("goal_kinds")) == list(old.GOAL_KINDS)
    assert p.origin["goal_kinds"] == {"on": "std", "in": "std", "go": "std", "say": "s30_merge", "deliver": "std"}
    assert p.entry("goal_kinds", "say")["prompt"] != DEFAULT.entry("goal_kinds", "say")["prompt"]
    assert p.entry("goal_kinds", "go") == DEFAULT.entry("goal_kinds", "go"), "an entry nobody replaced is the base entry"
    assert p.problems == [] and not kind_data.is_default(p)


def test_a_removed_kind_leaves_the_kinds_the_prompt_lines_and_the_action_vocabulary():
    p = _resolve(PACK)
    base, mine = goals.kinds_for(ACTIONS, (), DEFAULT), goals.kinds_for(ACTIONS, (), p)
    assert base == ["on", "in", "open", "close", "go", "say", "deliver"] and mine == ["on", "in", "go", "say", "deliver"]
    lines = difflib.unified_diff(goals.prompt_lines(base, DEFAULT).splitlines(), goals.prompt_lines(mine, p).splitlines(), lineterm="", n=0)
    changed = [x for x in lines if x[:1] in "+-" and x[:3] not in ("+++", "---")]
    assert len(changed) == 2 and changed[0].startswith('-  "say" = tell someone') and changed[1].startswith('+  "say" = pass a message on')
    assert set(goals.lacking(set(), DEFAULT)) - set(goals.lacking(set(), p)) == {"Open", "Close"}, "Open / Close left the missing_action enum"


def test_a_goal_of_a_removed_kind_needs_an_action_nobody_has():
    p = _resolve(REMOVE_ONLY)
    for kind in ("open", "close"):
        assert goals.missing_actions({"type": kind, "object": "drawer"}, ACTIONS, DEFAULT) == []
        assert goals.missing_actions({"type": kind, "object": "drawer"}, ACTIONS, p) == [kind]
        assert goals.cannot_do([{"type": kind}], ACTIONS, p) == kind
    assert goals.missing_actions({"type": "on", "object": "mug"}, {"Pick"}, p) == ["Place"], "a kind nobody removed keeps its needs"


def test_remove_then_declare_in_one_pack_brings_the_kind_back_whole_at_the_end():
    raw = dict(REMOVE_ONLY, name="back", goal_kinds={"open": {"rules": "std.open", "needs": ["Open"], "understood": "capable",
                                                              "planned_by": "planner", "conflicts": "default", "subgoal": "open {object}"}})
    p = _resolve(raw)
    assert list(p.table("goal_kinds")) == ["on", "in", "go", "say", "deliver", "open"] and p.origin["goal_kinds"]["open"] == "back"
    assert "close" not in p.table("goal_kinds") and goals.table(p)["open"].understood == "capable"


# ── (4) the feature cassettes against their base cassettes ───────────────────────────────────────────────────────────
def _seen(name):
    """Every text and format the model call hashed while the cassette ``name`` replays (harness.sha is the one place)."""
    seen, orig = [], harness.sha
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(harness, "sha", lambda x: (seen.append(x), orig(x))[1])
        HS.replay(HS.load(HS.cassette_path(name)))
    return ([x for x in seen if isinstance(x, str) and x.startswith("You turn")][0],
            [x for x in seen if isinstance(x, dict) and "properties" in x][0])


def _changed(a: str, b: str) -> list:
    return [x for x in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0) if x[:1] in "+-" and x[:3] not in ("+++", "---")]


def test_the_understand_call_differs_from_the_base_cassette_by_the_say_line_and_the_two_enum_values():
    (base_prompt, base_fmt), (prompt, fmt) = _seen("template_say_deliver"), _seen("s30_remove_replace")
    said = _changed(base_prompt, prompt)
    assert len(said) == 2 and said[0].startswith('-  "say" = tell someone something') and said[1].startswith('+  "say" = pass a message on')
    dump = lambda f: json.dumps(f, indent=1, sort_keys=True)          # noqa: E731
    assert _changed(dump(base_fmt), dump(fmt)) == ['-       "open",', '-       "close",']


def _recorded(name):
    return HS.load(HS.cassette_path(name))["recorded"]


def test_only_the_model_call_differs_in_the_say_deliver_run_with_the_merged_pack():
    base, mine = _recorded("template_say_deliver"), _recorded("s30_remove_replace")
    assert [k for k in base if base[k] != mine[k]] == ["llm_calls"]
    (b,), (m,) = base["llm_calls"], mine["llm_calls"]
    assert [k for k in b if b[k] != m[k]] == ["prompt", "format"] and m["answer"] == b["answer"] and m["head"] == b["head"]


def test_the_refused_run_stops_at_understand_and_names_no_step():
    mine = _recorded("s30_removed_kind_refused")
    assert [e["event"] for e in mine["events"]] == ["task_start", "reconcile", "status", "understand", "done"]
    done = mine["events"][-1]
    assert (done["status"], done["refusal"]) == ("refused", "capability") and "has no open skill" in done["msg"]
    assert _recorded("open_goal")["events"][-1]["status"] == "success", "with the standard pack the same task is done"
    assert {c["skill"] for c in mine["robot_calls"]} == {"mobile_pose"} and mine["planner_calls"] == [], "the start pose only, no plan"
    assert [e for e in _recorded("open_goal")["events"] if e["event"] == "step_start"], "the base cassette does run steps"
