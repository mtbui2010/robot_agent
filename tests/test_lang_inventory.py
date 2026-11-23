"""``tools/lang_inventory.py`` finds the language-specific literals of the always-on core, and the classes that round 16 emptied
(particles, self-correction markers, yes / no words, control words, scripts of the dialogue code) stay empty: a literal that
comes back fails here with its file and line."""
import importlib.util
import os
import textwrap

ROOT = os.path.join(os.path.dirname(__file__), "..")
spec = importlib.util.spec_from_file_location("lang_inventory", os.path.join(ROOT, "tools", "lang_inventory.py"))
inv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inv)


def _scan(tmp_path, src: str):
    pkg = tmp_path / "robot_agent"
    pkg.mkdir()
    (pkg / "mod.py").write_text(textwrap.dedent(src), encoding="utf-8")
    return inv.scan(str(tmp_path))[0]


def test_the_tool_finds_each_kind_of_literal(tmp_path):
    rows = _scan(tmp_path, '''
        import re
        from . import langdata
        _KO_PART = "|".join(("이랑", "에서", "한테"))
        _CORR = re.compile(r"\\b(?:no wait|i mean|actually)\\b", re.I)
        _INTO = re.compile(r"\\b(?:in|into|inside)\\s+the\\s+")
        _YES = {"yes", "yeah", "okay", "sure"}
        GARBLE_RATIO = 0.6
        def f(lang, text):
            if lang == "ko" and len(text) / 3 >= 0.7:
                return langdata.PROMPT_LANG
        ''')
    by = {(r["kind"], r["class"]) for r in rows}
    assert ("script", "particles") in by and ("regex", "self_correction") in by and ("regex", "into_markers") in by
    assert ("wordset", "yes_no_refusal") in by and ("langcode", "language_code") in by and ("pivot", "pivot") in by
    assert ("threshold", "thresholds") in by
    assert all(r["file"] == os.path.join("robot_agent", "mod.py") and r["line"] >= 3 for r in rows)


def test_docstrings_comments_and_tests_are_not_code(tmp_path):
    pkg = tmp_path / "robot_agent"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "lang").mkdir()
    (pkg / "mod.py").write_text('"""Docstring with 한국어."""\n# comment 한국어\nx = 1\n', encoding="utf-8")
    (pkg / "tests" / "t.py").write_text('y = "한국어"\n', encoding="utf-8")
    rows, meta = inv.scan(str(tmp_path))
    assert rows == [] and meta["comments_with_script"] == 1


def test_the_command_line(tmp_path, capsys):
    (tmp_path / "robot_agent").mkdir()
    (tmp_path / "robot_agent" / "m.py").write_text('A = "한국어"\n', encoding="utf-8")
    assert inv.main(["--root", str(tmp_path), "--summary"]) == 0
    assert "1 findings" in capsys.readouterr().out
    assert inv.main(["--root", str(tmp_path), "--check", "0"]) == 1


# ── the repository ───────────────────────────────────────────────────────────────────────────────────────────────────
ROWS = inv.scan(ROOT)[0]


def _where(cls=None, kind=None):
    return [f"{r['file']}:{r['line']}" for r in ROWS if (cls is None or r["class"] == cls) and (kind is None or r["kind"] == kind)]


def test_the_emptied_classes_stay_empty():
    for cls in ("particles", "verb_endings", "self_correction", "asr_hallucination"):
        assert _where(cls) == [], (cls, _where(cls))
    # no Hangul / CJK / Cyrillic / Arabic letter in the code of the dialogue, the router, the speech layer, the goal fixes,
    # the safety rules or the language layer (the packs hold them)
    forbidden = ("interact/", "safety/", "planning/speech.py", "planning/goal_fix.py", "planning/understand.py",
                 "planning/language.py", "planning/dialog_converge.py", "planning/persons.py", "planning/announcer.py",
                 "planning/dialog_ext.py", "planning/request_coverage.py")
    hits = [w for w in _where(kind="script") if any(f in w for f in forbidden)]
    assert hits == [], hits


def test_what_is_left_is_listed_and_does_not_grow():
    script_left = {w.split(":")[0] for w in _where(kind="script")}
    assert script_left <= {os.path.join("robot_agent", p) for p in (
        "cli.py", "core/plan_skill.py",                       # examples in a help text and an error message
        "core/planning/assist_loop.py",                       # the example inside UNDERSTAND_PROMPT (part of every cache key)
    )}, script_left
    assert set(_where(kind="pivot")) <= {f"robot_agent/core/planning/langdata.py:{r['line']}" for r in ROWS if r["kind"] == "pivot"}
    assert len(ROWS) <= 70, len(ROWS)                          # 63 at the end of round 16
