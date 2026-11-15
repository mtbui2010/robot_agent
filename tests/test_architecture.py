"""The structure rules of CLAUDE.md 13-15, enforced (tools/lint_arch.py, docs/architecture.md): import layers (relative imports
resolved), size limits with a ratchet baseline, the option wiring ratchet for assist_loop.py, no domain literals in the engine and
the option layer, and every registered option documented and run through the real AssistLoop."""
import importlib.util
import json
import re
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lint_arch", ROOT / "tools" / "lint_arch.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)


# ── the repository ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_repository_has_no_new_architecture_finding_and_no_stale_baseline_entry():
    new, stale, _state = lint.run()
    assert not new, "\n".join(new.values())
    assert not stale, "the baseline only shrinks; run `python tools/lint_arch.py --update`:\n" + "\n".join(stale)


def test_the_engine_never_imports_an_option_and_options_never_import_each_other():
    rules = lint.load_rules()
    found = lint.check_layers(str(ROOT), rules)
    assert [k for k in found if "robot_agent/options" in k and k.startswith(("layer", "cross"))] == []
    assert [k for k in found if k.startswith("layer") and "-> robot_agent.options" in k] == []


def test_the_baseline_has_no_import_edge_from_the_engine_up_to_an_option_or_the_api():
    base = json.loads((ROOT / "tools" / "arch_baseline.json").read_text())
    assert not [k for k in base["layers"] if "robot_agent.options" in k or "-> robot_agent.api" in k]


def test_assist_loop_reads_no_key_of_a_registered_option():
    keys = lint.registered_key_names()
    assert {"hazard_rules", "hazard_receiver", "reach_delivery", "unapplied_change"} <= set(keys)
    assert lint.check_wiring(str(ROOT), lint.load_baseline()["wiring"], keys) == {}


def test_the_size_baseline_names_the_shrink_plan_offenders():
    base = lint.load_baseline()["size"]
    assert any(k.endswith("assist_loop.py") for k in base), "the largest file is recorded, not hidden"
    assert all(v > 60 for v in base.values())


# ── no domain literals in the engine's new layers ────────────────────────────────────────────────────────────────────
def test_the_option_interface_and_the_option_layer_hold_no_language_or_domain_literal():
    spec_ = importlib.util.spec_from_file_location("lang_inventory", ROOT / "tools" / "lang_inventory.py")
    inv = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(inv)
    rows = [r for r in inv.scan(str(ROOT))[0] if r["file"].startswith(("robot_agent/options/", "robot_agent/core/options/", "robot_agent/core/safety/"))]
    assert rows == [], rows


# ── every option: spec, docs entry, a run through the real AssistLoop ─────────────────────────────────────────────────
# option -> the test file that runs it through AssistLoop with the option on and off (make_assist ... name="1")
THROUGH_THE_LOOP = {"ask_source": "test_option_ask_source.py", "hazard": "test_sage_lab_r17_hazard_receiver.py", "reach_delivery": "test_sage_lab_r15_reach_delivery.py",
                    "unapplied_change": "test_sage_lab_r15_unapplied_change.py", "confirm_look": "test_option_confirm_look.py",
                    "translate_retry": "test_option_translate_retry.py", "search_per_object": "test_option_search_per_object.py",
                    "place_find": "test_option_place_find.py", "reopen_on_miss": "test_option_reopen_on_miss.py",
                    "end_state": "test_option_end_state.py", "object_words": "test_option_object_words.py",
                    "precond_repair": "test_option_precond_repair.py", "goal_words": "test_option_goal_words.py",
                    "ask_focus": "test_option_ask_focus.py",
                    "entity_cover": "test_option_entity_cover.py", "hw_wait": "test_option_hw_wait.py",
                    "search_budget": "test_option_search_budget.py", "lang_words": "test_option_lang_words.py",
                    "gate_prune": "test_option_gate_prune.py", "cond_goal": "test_option_cond_goal.py", "cond_extract": "test_option_cond_extract.py", "dedupe_keep_end": "test_option_dedupe_keep_end.py",
                    "recipient_keep": "test_option_recipient_keep.py", "name_recover": "test_option_name_recover.py", "asr_guard": "test_option_asr_guard.py", "plan_complete": "test_option_plan_complete.py", "replan_guard": "test_option_replan_guard.py",
                    "ask_agree": "test_option_ask_agree.py", "plan_release": "test_option_plan_release.py", "effect_check": "test_option_effect_check.py",
                    "repair_sync": "test_option_repair_sync.py", "goal_ledger": "test_option_goal_ledger.py", "answer_align": "test_option_answer_align.py"}
THROUGH_THE_LOOP["question_cap"] = "test_option_question_cap.py"                       # D4 layer L23 (a line of its own)


def _specs():
    from robot_agent.core.options import REGISTRY
    return REGISTRY.specs()


@pytest.mark.parametrize("name", [s.name for s in _specs()])
def test_every_registered_option_is_documented_and_tested_through_the_loop(name):
    s = next(x for x in _specs() if x.name == name)
    docs = (ROOT / "docs" / "options.md").read_text(encoding="utf-8")
    assert f"`{name}`" in docs, f"docs/options.md has no entry for the option {name}"
    assert s.doc and s.hooks, "an option declares its hooks and says in one line what it does"
    test = THROUGH_THE_LOOP.get(name)
    assert test, f"option {name}: name the test file that runs it through AssistLoop in THROUGH_THE_LOOP"
    src = (ROOT / "tests" / test).read_text(encoding="utf-8")
    assert "make_assist" in src and re.search(r"\b" + "|".join(map(re.escape, s.flat_names())) + r"\b", src)
    mod = ROOT / "robot_agent" / "options" / f"{name}.py"
    assert mod.exists(), "one option, one module: robot_agent/options/<name>.py"


def test_the_option_modules_are_listed_explicitly_in_the_package_init():
    init = (ROOT / "robot_agent" / "options" / "__init__.py").read_text(encoding="utf-8")
    on_disk = {p.stem for p in (ROOT / "robot_agent" / "options").glob("*.py") if p.stem != "__init__"}
    listed = set(re.findall(r"\b(\w+)\b", init.split("import", 1)[1].split("#")[0]))
    assert on_disk <= listed, f"modules in options/ that __init__ does not import: {on_disk - listed}"


# ── the linter itself ────────────────────────────────────────────────────────────────────────────────────────────────
RULES = {"layers": {"contract": ["robot_agent/contract"], "engine": ["robot_agent/core"], "options": ["robot_agent/options"],
                    "api": ["robot_agent/api"]},
         "may_import": {"contract": [], "engine": ["contract"], "options": ["contract", "engine"], "api": ["contract", "engine"]},
         "no_cross": ["options"], "forbidden_modules": ["homeloop_bench"], "only_in": {"embodiplan": ["robot_agent/core/planning/backends/"]},
         "size": [{"path": "robot_agent/core", "max_function": 5, "max_file": 30}, {"path": "robot_agent/options", "max_function": 4, "max_file": 10}]}


def tree(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / "robot_agent" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src), encoding="utf-8")
    for d in {x.parent for x in (tmp_path / "robot_agent").rglob("*.py")}:
        (d / "__init__.py").touch()
    return str(tmp_path)


def test_the_linter_resolves_relative_imports_and_finds_each_kind_of_violation(tmp_path):
    root = tree(tmp_path, {
        "core/engine.py": "from ..options import a\nfrom ..contract import types\n",          # engine -> options (relative)
        "core/deep/x.py": "from ...options import a as _a\nimport embodiplan\n",             # three levels up; embodiplan outside its home
        "contract/types.py": "from ..core import engine\n",                                    # contract -> engine
        "options/a.py": "from . import b\nfrom ..core.engine import _hidden\nimport homeloop_bench\n",
        "options/b.py": "X = 1\n",
        "api/z.py": "from ..options import b\n"})
    f = lint.check_layers(root, RULES)
    assert "layer: robot_agent/core/engine.py -> robot_agent.options.a" in f
    assert "layer: robot_agent/core/deep/x.py -> robot_agent.options.a" in f
    assert "layer: robot_agent/contract/types.py -> robot_agent.core.engine" in f
    assert "cross: robot_agent/options/a.py -> robot_agent.options.b" in f
    assert "private: robot_agent/options/a.py -> robot_agent.core.engine._hidden" in f
    assert "forbidden: robot_agent/options/a.py -> homeloop_bench" in f
    assert "only_in: robot_agent/core/deep/x.py -> embodiplan" in f
    assert "layer: robot_agent/api/z.py -> robot_agent.options.b" in f
    assert "layer: robot_agent/core/engine.py -> robot_agent.contract.types" not in f             # allowed edge


def test_the_linter_ratchet_blocks_growth_and_new_offenders_and_flags_a_stale_entry(tmp_path):
    root = tree(tmp_path, {"core/big.py": "def f():\n" + "    x = 1\n" * 8 + "\n" * 30, "options/o.py": "def g():\n    pass\n"})
    bad, cur = lint.check_size(root, RULES, {})
    assert "size: fn: robot_agent/core/big.py::f#0" in bad and "size: file: robot_agent/core/big.py" in bad
    base = dict(cur)
    assert lint.check_size(root, RULES, base)[0] == {}                                        # recorded: allowed at this size
    (tmp_path / "robot_agent" / "core" / "big.py").write_text("def f():\n" + "    x = 1\n" * 12 + "\n" * 30)
    grew, _cur = lint.check_size(root, RULES, base)
    assert any("grew" in m for m in grew.values())
    (tmp_path / "robot_agent" / "core" / "big.py").write_text("def f():\n    pass\n")
    assert lint.check_size(root, RULES, base)[0] == {}                                        # shrunk: fine here, stale in run()


def test_wiring_counts_cfg_reads_and_branches(tmp_path):
    src = 'def f(self):\n    if self.cfg.get("alpha"):\n        pass\n    return self.cfg["beta"], self.cfg.get("alpha")\n'
    p = tmp_path / "robot_agent" / "core" / "planning"
    p.mkdir(parents=True)
    (p / "assist_loop.py").write_text(src)
    w = lint.wiring(str(tmp_path))
    assert (w["cfg_keys"], w["cfg_branches"], w["keys"]) == (2, 1, ["alpha", "beta"])
    assert lint.check_wiring(str(tmp_path), {"cfg_keys": 1, "cfg_branches": 1}, ["alpha"])
    assert lint.check_wiring(str(tmp_path), {"cfg_keys": 2, "cfg_branches": 1}, ["gamma"]) == {}
