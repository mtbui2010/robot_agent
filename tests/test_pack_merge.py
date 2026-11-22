"""Step S30: the merge semantics of ``extends`` and ``remove`` take effect in a run. ``core/pack/loader.py`` already merges the
chain (an entry of a later pack replaces the base entry whole, ``remove`` deletes names); S07 made the goal-kind table, the
understand prompt and the schema enum read the merged table. These tests prove (1) a pack that extends the standard pack and
declares nothing reads the default data, character for character; (2) the merge rule equals an independent reference over a
grid of chains (replace whole, keep the place, remove, a removed name that comes back); (3) in a run, a removed kind leaves the
understand prompt and the schema enum, and a replaced kind is replaced whole (its base ``prompt`` does not come back); (4) the
feature cassette ``s30_remove_replace`` differs from its base cassette ``template_say_deliver`` only by the prompt and format
hashes of the understand call (the prompt head, the planner, robot calls, events and final state are the same)."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))
import harness as H                                          # noqa: E402
import harness_sets as HS                                    # noqa: E402

from robot_agent.contract.manifest import Manifest                      # noqa: E402
from robot_agent.contract.pack import PackDecl                            # noqa: E402
from robot_agent.core.pack import default_pack, loader, resolve           # noqa: E402
from robot_agent.core.pack import goal_kinds as kind_data                # noqa: E402
from robot_agent.core.planning import goals                              # noqa: E402

SETS = Path(__file__).resolve().parent / "golden" / "scenario_sets"
BASE, FEATURE = "template_say_deliver", "s30_remove_replace"
ACTIONS = {"MoveTo", "Say", "WaitForUnload", "Pick", "Place", "PutIn", "Open", "Close"}


def _line() -> Manifest:
    """A manifest with the abilities the kinds of the standard pack need (the pack check wants the actions declared)."""
    skills = [{"name": n.lower(), "action": n} for n in sorted(ACTIONS)]
    return Manifest.from_dict({"robot": "s30_line", "skills": skills})


def _pack(**sections):
    return {"schema": 2, "name": "s30_test", "extends": "household", "assumed": True, **sections}


def _decl(name, tables=None, remove=None):
    return PackDecl(name=name, tables=copy.deepcopy(tables or {}), remove=copy.deepcopy(remove or {}))


# ── (1) the default data ────────────────────────────────────────────────────────────────────────────────────────────
def test_a_pack_that_extends_the_standard_pack_and_declares_nothing_reads_the_default_table():
    p = resolve(None, None, raw=_pack())
    assert p.table("goal_kinds") == kind_data.default_table() and kind_data.is_default(p)
    assert goals.table(p) is goals.GOAL_KINDS and list(goals.GOAL_KINDS) == ["on", "in", "open", "close", "go", "say", "deliver"]
    assert p.chain[-2:] == ("std", "s30_test") and p.problems == []


# ── (2) the merge rule against an independent reference ─────────────────────────────────────────────────────────────
def _reference(chain):
    """The rule written plainly: a dict per table; a later entry replaces the earlier one whole (an existing key keeps its place);
    ``remove`` pops names, a name popped may be declared again later in the chain."""
    out = {}
    for d in chain:
        for t, names in d.remove.items():
            for n in names:
                out.setdefault(t, {}).pop(n, None)
        for t, entries in d.tables.items():
            for n, e in entries.items():
                out.setdefault(t, {})[n] = e
    return {t: v for t, v in out.items() if v}


CHAINS = {
    "plain": [_decl("a", {"goal_kinds": {"x": {"needs": ["A"]}, "y": {"prompt": "p"}}}), _decl("b", {"goal_kinds": {"z": {"needs": ["B"]}}})],
    "replace": [_decl("a", {"goal_kinds": {"x": {"needs": ["A"], "prompt": "p"}, "y": {}}}),
                _decl("b", {"goal_kinds": {"x": {"needs": ["B"]}}})],
    "remove": [_decl("a", {"goal_kinds": {"x": {}, "y": {}, "z": {}}}), _decl("b", remove={"goal_kinds": ["y"]})],
    "back": [_decl("a", {"goal_kinds": {"x": {"prompt": "old"}, "y": {}}}),
             _decl("b", {"goal_kinds": {"x": {"prompt": "new"}}}, {"goal_kinds": ["x"]})],
    "three": [_decl("a", {"goal_kinds": {"x": {"prompt": "1"}}}), _decl("b", {"goal_kinds": {"x": {"needs": ["Q"]}, "w": {}}}),
              _decl("c", {"goal_kinds": {"w": {"prompt": "3"}}}, {"goal_kinds": ["x"]})],
}


@pytest.mark.parametrize("name", sorted(CHAINS))
def test_the_merge_equals_the_reference_rule(name):
    chain = CHAINS[name]
    tables, origin, _patterns, problems = loader.merge(chain)
    want = _reference(chain)
    assert {t: v for t, v in tables.items() if v} == want
    assert {t: list(v) for t, v in tables.items() if v} == {t: list(v) for t, v in want.items()}, "the order of the names"
    assert all(set(origin[t]) == set(tables[t]) for t in tables)
    assert problems == []


def test_a_replaced_entry_is_not_a_deep_merge_and_the_base_data_is_not_changed():
    base = kind_data.default_table()
    before = copy.deepcopy(base)
    p = resolve(None, None, raw=_pack(goal_kinds={"deliver": {"rules": "std.deliver", "assumed": True}}))
    assert p.entry("goal_kinds", "deliver") == {"rules": "std.deliver", "assumed": True} and p.origin["goal_kinds"]["deliver"] == "s30_test"
    assert list(p.table("goal_kinds")) == list(before), "a replaced kind keeps its place"
    assert base == before and kind_data.default_table() == before


# ── (3) a run reads the merged table ────────────────────────────────────────────────────────────────────────────────
def _merged():
    import scenario_sets
    return next(s for s, _t, _a in scenario_sets.set_scenarios() if s["name"] == FEATURE)["pack"]


def test_a_removed_kind_leaves_the_prompt_lines_and_the_enum_and_a_replaced_kind_is_replaced_whole():
    base, merged = default_pack(), resolve(_line(), None, raw=_merged())
    assert goals.kinds_for(ACTIONS, (), base) == ["on", "in", "open", "close", "go", "say", "deliver"]
    assert goals.kinds_for(ACTIONS, (), merged) == ["on", "in", "go", "say", "deliver"]
    a, b = goals.prompt_lines(goals.kinds_for(ACTIONS, (), base), base), goals.prompt_lines(goals.kinds_for(ACTIONS, (), merged), merged)
    gone = set(a.split("\n")) - set(b.split("\n"))
    assert gone == {"  " + goals.GOAL_KINDS["deliver"].prompt} and set(b.split("\n")) <= set(a.split("\n"))
    assert merged.value("goal_kinds", "deliver", "needs") == ["MoveTo", "WaitForUnload"] and merged.entry("goal_kinds", "deliver").get("prompt") is None
    assert "Open" in goals.domain_actions(base) and "Open" not in goals.domain_actions(merged), "the action left with its kinds"


# ── (4) the feature cassette against its base cassette ──────────────────────────────────────────────────────────────
def _cassette(name):
    return HS.load(HS.cassette_path(name))


def test_the_feature_cassette_differs_from_the_base_only_by_the_understand_call_hashes():
    base, feat = _cassette(BASE)["recorded"], _cassette(FEATURE)["recorded"]
    for section in ("planner_calls", "robot_calls", "events", "final", "prompts"):
        assert base[section] == feat[section], section
    (a,), (b,) = base["llm_calls"], feat["llm_calls"]
    assert a["head"] == b["head"] and a["answer"] == b["answer"]
    assert a["prompt"] != b["prompt"] and a["format"] != b["format"]


def test_the_understand_prompt_and_schema_of_the_replayed_run_differ_by_exactly_the_merged_lines(monkeypatch):
    seen, orig = {}, H.CassetteLLM.chat
    for name in (BASE, FEATURE):
        def chat(self, prompt="", format=None, _name=name, **kw):
            seen.setdefault(_name, []).append((str(prompt), json.dumps(format, sort_keys=True)))
            return orig(self, prompt=prompt, format=format, **kw)
        monkeypatch.setattr(H.CassetteLLM, "chat", chat)
        assert HS.check(HS.cassette_path(name)) == ""
    (p0, f0), (p1, f1) = seen[BASE][0], seen[FEATURE][0]
    lines0, lines1 = p0.split("\n"), p1.split("\n")
    assert set(lines1) <= set(lines0) and [x for x in lines0 if x not in set(lines1)] == ["  " + goals.GOAL_KINDS["deliver"].prompt]
    e0, e1 = _enum(f0), _enum(f1)
    assert e0 == ["on", "in", "open", "close", "go", "say", "deliver"] and e1 == ["on", "in", "go", "say", "deliver"]


def _enum(fmt):
    d = json.loads(fmt)
    return d["properties"]["goals"]["items"]["properties"]["type"]["enum"]


# ── the set file and its commits file ───────────────────────────────────────────────────────────────────────────────
def test_the_set_pack_is_the_pack_the_cassette_recorded():
    assert _merged() == _cassette(FEATURE)["scenario"]["pack"]
    assert sorted(SETS.glob("s30_*.py")) == [SETS / "s30_merge.py"]
