"""The architecture lint's loop guards follow the code (Step S03, design invariant 18): the wiring ratchet, the option-key scan,
the embodiplan-internals scan, the language-literal ratchet and the vocabulary ratchet read every file of the loop set
(assist_loop.py, phases/*.py, places.py, loop_config.py, find_ladder.py, core/pack/*.py, the ones that exist), so moving code
out of assist_loop.py changes no count; core/pack and packs hold no language literal. Made-up files in a temp tree."""
import importlib.util
import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lint_arch", ROOT / "tools" / "lint_arch.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)
PLAN = "robot_agent/core/planning/"


def tree(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src), encoding="utf-8")
    return str(tmp_path)


def guards(root, base=None):
    return lint.check_loop(root, base or {})


LOOP_SRC = '''
    def run(self, st):
        if self.cfg.get("alpha"):
            return "Pick", "on"
        return self.cfg["beta"]
    '''
MOVED_SRC = '''
    def look(self, st):
        if self.cfg.get("alpha") and st == "Find":
            return "in"
        return self.cfg.get("gamma")
    '''


# ── the set ──
def test_the_repository_loop_set_lists_the_files_that_exist():
    files = lint.loop_files(str(ROOT))
    assert files[0] == lint.LOOP
    assert {PLAN + "places.py", PLAN + "phases/__init__.py"} <= set(files)
    assert all((ROOT / f).exists() for f in files)


def test_a_file_of_the_set_that_does_not_exist_is_skipped_and_a_glob_takes_every_module(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": "x = 1\n", PLAN + "phases/a.py": "", PLAN + "phases/b.py": "",
                           "robot_agent/core/pack/loader.py": "", PLAN + "other.py": ""})
    assert lint.loop_files(root) == [PLAN + "assist_loop.py", PLAN + "phases/a.py", PLAN + "phases/b.py",
                                     "robot_agent/core/pack/loader.py"]


# ── wiring: the sum over the set ──
def test_wiring_sums_the_keys_and_branches_of_every_loop_file(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": LOOP_SRC, PLAN + "phases/look.py": MOVED_SRC})
    w = lint.wiring(root)
    assert (w["cfg_keys"], w["cfg_branches"], w["keys"]) == (2 + 2, 1 + 1, ["alpha", "beta", "gamma"])
    assert lint.check_wiring(root, {"cfg_keys": 3, "cfg_branches": 2}, [])           # grew
    assert lint.check_wiring(root, {"cfg_keys": 4, "cfg_branches": 2}, []) == {}


def test_moving_a_method_to_another_loop_file_keeps_the_branches_the_keys_and_the_guard_counts(tmp_path):
    one = tree(tmp_path / "one", {PLAN + "assist_loop.py": LOOP_SRC + MOVED_SRC})
    two = tree(tmp_path / "two", {PLAN + "assist_loop.py": LOOP_SRC, PLAN + "places.py": MOVED_SRC})
    pick = lambda w: (w["cfg_branches"], w["keys"])                                    # noqa: E731
    assert pick(lint.wiring(one)) == pick(lint.wiring(two))
    assert guards(one)[2] == guards(two)[2]


def test_a_registered_option_key_in_a_moved_module_is_found(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": "x = 1\n", PLAN + "places.py": 'K = "hazard_rules"\n'})
    bad = lint.check_wiring(root, {}, ["hazard_rules"])
    assert list(bad) == [f"wiring: {PLAN}places.py: hazard_rules"]
    assert lint.check_wiring(root, {}, ["other_key"]) == {}


# ── embodiplan internals ──
def test_an_embodiplan_import_or_a_private_action_model_name_in_the_loop_is_found(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": "from . import action_model\nx = action_model._rules\n",
                           PLAN + "phases/run.py": "import embodiplan.action_model\nfrom .action_model import _hidden\n",
                           PLAN + "elsewhere.py": "import embodiplan\n"})
    new = guards(root)[0]
    assert set(new) == {f"internals: {PLAN}assist_loop.py -> action_model._rules",
                        f"internals: {PLAN}phases/run.py -> embodiplan.action_model",
                        f"internals: {PLAN}phases/run.py -> action_model._hidden"}


def test_the_public_action_model_is_allowed(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": "from . import action_model\nx = action_model.check, action_model.ROBOT_ACTIONS\n"})
    assert guards(root)[0] == {}


# ── language literals ──
def test_language_rows_are_counted_over_the_set_and_forbidden_in_the_pack_layers(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": 'import re\nA = re.sub(r"^(the|a|an)\\s+", "", "x")\n',
                           PLAN + "places.py": 'import re\nB = re.search(r"\\b(?:in|of|at)\\s+", "x")\n',
                           "robot_agent/core/pack/loader.py": 'WORD = "\\uc548\\ub155"\n',
                           "robot_agent/packs/household/callables/x.py": 'W = "\\uc548\\ub155"\n',
                           PLAN + "elsewhere.py": 'C = "\\uc548\\ub155"\n'})
    new, _stale, cur = guards(root)
    assert cur["lang_rows"] == 3                      # assist_loop.py, places.py and core/pack/loader.py (in the set), not elsewhere.py
    assert sorted(k for k in new if k.startswith("language:")) == ["language: robot_agent/core/pack/loader.py:1",
                                                                   "language: robot_agent/packs/household/callables/x.py:1"]


# ── the vocabulary ratchet ──
def test_vocabulary_counts_lines_with_a_double_quoted_action_name_or_goal_kind(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": 'a = "Pick"\nb = ("on", "in")\nc = "Pick up" + "online"\nd = "Pick", "on"\n',
                           PLAN + "phases/x.py": 'e = "MoveTo"\nf = "WaitForUnload"\ng = "give"\n'})
    cur = guards(root)[2]
    assert (cur["vocab_actions"], cur["vocab_kinds"]) == (4, 3)


def test_the_loop_ratchets_block_growth_and_report_a_fall_as_stale(tmp_path):
    root = tree(tmp_path, {PLAN + "assist_loop.py": 'a = "Pick"\nb = "on"\n'})
    new, stale, cur = guards(root, {"vocab_actions": 0, "vocab_kinds": 1, "lang_rows": 0})
    assert list(new) == ["loop: vocab_actions"] and stale == []
    new, stale, _cur = guards(root, {"vocab_actions": 1, "vocab_kinds": 5})
    assert new == {} and stale == ["loop: vocab_kinds 5 -> 1"]
    assert guards(root, {})[:2] == ({}, [])                                                # no baseline: not checked


# ── the repository ──
def test_the_repository_counts_equal_the_recorded_baseline():
    base = json.loads((ROOT / "tools" / "arch_baseline.json").read_text())
    new, stale, cur = guards(str(ROOT), base["loop"])
    assert new == {} and stale == [] and cur == base["loop"]
    assert set(base["loop"]) == {"lang_rows", "vocab_actions", "vocab_kinds"}
    w = lint.wiring(str(ROOT))
    assert (w["cfg_keys"], w["cfg_branches"]) == (base["wiring"]["cfg_keys"], base["wiring"]["cfg_branches"])
