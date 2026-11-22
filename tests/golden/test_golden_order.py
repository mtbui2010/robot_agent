"""The golden sets stay in order (Step S01): the scenarios of scenarios.py first, then the scenario sets by file name; every
scenario has its cassette; a cassette of a set carries the commit it was recorded on, listed in base_commits.json, whose
robot_agent/ tree is the old code's; the mutations of tools/golden_mutation_check.py keep the first ten first; nothing in
robot_agent imports the reference copies of tests/legacy_ref. These tests never write a golden."""

import importlib.util
import json
import re
import subprocess

import pytest

import harness
import scenario_sets
import scenarios

BASE = json.loads((harness.GOLDEN / "base_commits.json").read_text())
OLD = [sc["name"] for sc, _t, _a in scenarios.SCENARIOS]


def _mutation_tool():
    spec = importlib.util.spec_from_file_location("golden_mutation_check", harness.ROOT / "tools" / "golden_mutation_check.py")
    mc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mc)
    return mc


def test_the_scenarios_of_scenarios_py_come_first_and_every_name_is_unique():
    names = [sc["name"] for sc, _t, _a in scenario_sets.all_scenarios()]
    assert names[:len(OLD)] == OLD
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
    assert all(re.fullmatch(r"s\d+[a-z]?_\w+\.py", p.name) for p in scenario_sets.set_files())


def test_every_scenario_of_a_set_has_its_cassette_and_its_tags():
    missing = [sc["name"] for sc, _t, _a in scenario_sets.set_scenarios() if not harness.cassette_path(sc["name"]).exists()]
    assert not missing, f"no cassette for {missing}: run tools/golden_record.py with its record flag"
    for sc, tags, _a in scenario_sets.set_scenarios():
        assert tags and harness.load(harness.cassette_path(sc["name"]))["tags"] == list(tags), sc["name"]


def test_every_new_cassette_names_a_listed_commit_and_the_old_ones_none():
    for p in harness.cassettes():
        cas = harness.load(p)
        if cas["name"] in OLD:
            assert "commit" not in cas, p.name
        else:
            assert cas.get("commit") in BASE["equivalence"], (p.name, cas.get("commit"))


def _git(*args):
    return subprocess.run(["git", *args], cwd=harness.ROOT, capture_output=True, text=True)


def test_the_listed_commits_have_the_old_code():
    if _git("rev-parse", "--git-dir").returncode != 0 or _git("cat-file", "-e", BASE["old_code"]).returncode != 0:
        pytest.skip("no git history here")
    for c in BASE["equivalence"]:
        d = _git("diff", "--stat", BASE["old_code"], c, "--", "robot_agent")
        assert d.returncode == 0 and d.stdout.strip() == "", f"{c}: robot_agent/ differs from {BASE['old_code']}\n{d.stdout}"


def test_the_scenario_keys_of_the_sets_are_known():
    known = {"name", "env", "robot_pkg", "task", "robot", "llm", "planner", "options", "pack_options", "world", "goals", "lang",
             "plan_only", "managed", "cancel_after", "say_after", "pause_after", "health", "capture", "environ", "pack"}
    for sc, _t, _a in scenario_sets.set_scenarios():
        assert set(sc) <= known, (sc["name"], set(sc) - known)
        harness.capture_of(sc)                                  # raises on an unknown capture


def test_the_mutation_tool_keeps_its_first_ten_and_loads_the_sets_after():
    mc = _mutation_tool()
    names = list(mc.MUTATIONS)
    assert names[:10] == ["gate_sees_nothing", "drop_recover_events", "one_retry_fewer", "yes_means_no", "bt_fallback_as_sequence",
                          "no_replan_budget", "goals_only_reports_none", "plan_only_ignored", "stop_not_seen",
                          "menu_stop_word_unheard"]
    s01 = {"template_always_reached", "non_manip_goal_seen", "in_needs_no_pick", "deliver_prompt_changed", "readback_in_phrase",
           "rb_go_phrase", "silence_is_yes", "correction_rest_lost", "menu_retry_unheard", "say_not_translated",
           "korean_phrases_lost", "help_move_changed", "bt_opened_inverted", "bt_at_inverted", "bt_placed_ignores_hand",
           "effect_unknown_pred_fails", "planner_obs_empty", "unknown_kind_not_reached", "on_goal_without_place",
           "sage_prompt_changed", "place_arg_set_changed", "ladder_choice", "end_check_trusts_belief", "manifest_drops_capabilities"}
    assert s01 <= set(names[10:]), sorted(s01 - set(names))


def test_robot_agent_never_imports_the_legacy_reference_copies():
    hits = [str(p.relative_to(harness.ROOT)) for p in (harness.ROOT / "robot_agent").rglob("*.py")
            if re.search(r"\blegacy_ref\b", p.read_text(encoding="utf-8", errors="ignore"))]
    assert not hits, hits
    assert (harness.TESTS / "legacy_ref" / "__init__.py").exists()
