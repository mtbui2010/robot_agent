#!/usr/bin/env python3
"""Inventory of language-specific literals in the always-on core of robot_agent.

The core must not carry facts about one human language in its code: those are DATA (the language packs
``robot_agent/core/planning/lang/<code>.json``, the robot pack's i18n, the manifest). This script lists what is still
written in code, so the work is measured and a regression is caught by ``tests/test_lang_inventory.py``.

What is scanned: every ``*.py`` under ``robot_agent/`` except ``tests`` and the language packs. Docstrings and
comments are not code (they are counted apart, with ``--with-text`` they are listed too). What is looked for, in string
constants and names of the AST:

* ``script``: a character of Hangul, Han / kana, Cyrillic, Arabic, or a letter that only Vietnamese uses;
* ``regex``: a string given to ``re`` (or written like a regex) that holds an English function word (in, into, on, the,
  and, not, ...);
* ``wordset``: a list / set / tuple of strings that is mostly spoken-dialogue words (yes, no, stop, please, ...);
* ``langcode``: a language code (``"en"``, ``"ko"``, ...) compared with, or used as a default;
* ``pivot``: a use of ``PROMPT_LANG`` (the language the core's prompts are written in);
* ``threshold``: a similarity / share / count limit written in code (``ratio() >= 0.6``, ``*_RATIO = 0.6``).

Each finding is classified (particles, verb_endings, yes_no_refusal, self_correction, into_markers, script_detect,
speaker_forms, domain_words, thresholds, language_code, pivot, action_vocab, failure_text, model_output, pivot_grammar (English function words in a
regex over the English reading), asr_hallucination, example_text, other) from the names around it and what it
holds. The classification is a reading aid: a finding is a place to look at, not a verdict.

Usage::

    python tools/lang_inventory.py                       # markdown table + summary
    python tools/lang_inventory.py --summary             # the counts only
    python tools/lang_inventory.py --format json         # machine readable
    python tools/lang_inventory.py --class particles     # one class
    python tools/lang_inventory.py --check N             # exit 1 when more than N findings remain (a regression gate)

The detector words below are the tool's own, not the core's: this file is outside ``robot_agent/``.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = "robot_agent"

SCRIPTS = {
    "hangul": re.compile("[ᄀ-ᇿ㄰-㆏가-힯]"),
    "han_kana": re.compile("[぀-ヿ㐀-䶿一-鿿]"),
    "cyrillic": re.compile("[Ѐ-ӿ]"),
    "arabic": re.compile("[؀-ۿ]"),
    # letters that Vietnamese uses and French / Spanish / Portuguese / German do not
    "vietnamese": re.compile("[ăơưđẠ-ỹ]"),
}
FUNCTION_WORDS = frozenset("""a an the in into inside on onto at of to from by with without and or but not no nor if unless
whether so then than that this these those it its is are was were be been do does did don't doesn't can could would should
will shall may might my your our his her their me you him them us i we he she they what where when who which how why open
close shut""".split())
DIALOG_WORDS = frozenset("""yes yeah yep yup ok okay sure right correct nope no not wrong stop halt abort cancel freeze enough
pause wait hold hang resume continue proceed carry please sorry pardon uh um umm er erm ah eh oh hm hmm mm mhm huh well
robot hey just forget skip instead actually rather also too status progress""".split())
LANG_CODES = frozenset({"en", "ko", "ja", "zh", "vi", "de", "es", "fr", "ru", "ar"})
RE_FUNCS = frozenset({"compile", "search", "match", "fullmatch", "sub", "subn", "findall", "finditer", "split"})
REGEXISH = re.compile(r"\\b|\\s|\\w|\(\?[:=!<]|\[\^?[^\]]+\]|\.\*|\|")

STRONG = frozenset("in into inside on onto the and or if unless from to then my your out with without".split())
CLASS_RULES = (   # (class, regex over "<names> <value>", lower case); first match wins
    ("failure_text", re.compile(r"failures\.py")),            # English reasons a robot skill reports (the contract)
    ("model_output", re.compile(r"verify_vlm|_yes\b.*true")),  # tokens a vision model answers with
    ("example_text", re.compile(r"->|said->real")),
    ("asr_hallucination", re.compile(r"hallucin")),
    ("self_correction", re.compile(r"corr\b|corr_|_corr|correct(?:ion)?\b|no wait|i mean|self.?corr")),
    ("into_markers", re.compile(r"\binto\b|_into|into_|ontop|on_top|\bputin\b|\binside\b|\bon top\b|위에|안에|속에|안쪽")),
    ("particles", re.compile(r"_eul|_reul|part(?:icle)?\b|_part\b|part_|particle|_acc\b|_from\b|_and\b|한테|에게|부터|에서")),
    ("verb_endings", re.compile(r"hada|ending|suffix|_tail|tail_|polite|conjug|어요|아요|해요|세요|합니다")),
    ("yes_no_refusal", re.compile(r"\byes\b|\bno\b|\byes_|_yes|_no\b|filler|near_|_stop|stop_|_pause|pause_|_resume|resume_|"
                                  r"\bneg|_neg|fallback|refus|decline|아니|멈춰|그만|정지|취소|잠깐")),
    ("speaker_forms", re.compile(r"speaker|first_person|recipient|나한테|저한테")),
    ("script_detect", re.compile(r"hangul|han_kana|cyrillic|non_latin|isascii|script|letters|jamo")),
    ("thresholds", re.compile(r"ratio|thresh|share|tol\b|restate|garble|similar")),
    ("domain_words", re.compile(r"hazard|place_class|bleach|fridge|냉장고|표백|세제|칼|container|food|heat|throw|pour|cook|"
                                r"wash|climb|poison|medicine|knife|가위|독|약")),
    ("action_vocab", re.compile(r"\bopen\b|\bclose\b|\bshut\b|열어|닫아|가져|\bbring\b|\bfetch\b")),
)


def _scripts(s: str) -> list:
    return [n for n, rx in SCRIPTS.items() if rx.search(s)]


def _classify(names: str, value: str, kind: str) -> str:
    if kind == "langcode":
        return "language_code"
    if kind == "pivot":
        return "pivot"
    if kind == "threshold":
        return "thresholds"
    hay = (names + " " + value).lower()
    for cls, rx in CLASS_RULES:
        if rx.search(hay):
            return cls
    return "pivot_grammar" if kind == "regex" else "other"


class _Walker(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel, self.out, self.docs, self.stack, self.parents = rel, [], set(), [], {}

    # ── helpers ──
    def names(self, node) -> str:
        """Names around ``node``: enclosing assignment targets, calls and function names."""
        out, cur = [], node
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append(cur.name)
            elif isinstance(cur, ast.Assign):
                out += [t.id if isinstance(t, ast.Name) else getattr(t, "attr", "") for t in cur.targets]
            elif isinstance(cur, ast.AnnAssign) and isinstance(cur.target, ast.Name):
                out.append(cur.target.id)
            elif isinstance(cur, ast.Call):
                f = cur.func
                out.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
            cur = self.parents.get(cur)
        return " ".join(n for n in out if n)

    def add(self, node, kind: str, value: str, extra: str = "") -> None:
        names = self.names(node)
        self.out.append({"file": self.rel, "line": node.lineno, "kind": kind,
                         "class": _classify(names + " " + os.path.basename(self.rel), value, kind),
                         "context": names.split(" ")[0] if names else "", "detail": extra,
                         "excerpt": " ".join(str(value).split())[:70]})

    def in_re_call(self, node) -> bool:
        cur = self.parents.get(node)
        while cur is not None and not isinstance(cur, (ast.stmt,)):
            if isinstance(cur, ast.Call):
                f = cur.func
                if isinstance(f, ast.Attribute) and f.attr in RE_FUNCS and isinstance(f.value, ast.Name) and f.value.id == "re":
                    return True
            cur = self.parents.get(cur)
        return False

    # ── visits ──
    def run(self, tree) -> None:
        for p in ast.walk(tree):
            for c in ast.iter_child_nodes(p):
                self.parents[c] = p
        for p in ast.walk(tree):
            if isinstance(p, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and p.body:
                b = p.body[0]
                if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                    self.docs.add(id(b.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                self.constant(node)
            elif isinstance(node, (ast.List, ast.Set, ast.Tuple)):
                self.collection(node)
            elif isinstance(node, ast.Compare):
                self.compare(node)
            elif isinstance(node, (ast.Name, ast.Attribute)):
                nm = node.id if isinstance(node, ast.Name) else node.attr
                if nm == "PROMPT_LANG" and not isinstance(self.parents.get(node), ast.Attribute):
                    self.add(node, "pivot", "PROMPT_LANG")
            elif isinstance(node, ast.Assign):
                self.assign(node)

    def constant(self, node) -> None:
        v = node.value
        if not isinstance(v, str) or id(node) in self.docs or not v.strip():
            return
        sc = _scripts(v)
        if sc:
            self.add(node, "script", v, ",".join(sc))
            return
        if len(v) >= 6 and (self.in_re_call(node) or REGEXISH.search(v)) and REGEXISH.search(v):
            words = set(re.findall(r"[a-z']+", v.lower()))
            hit = sorted(words & FUNCTION_WORDS)
            if (len(set(hit) & STRONG) >= 1 or len(hit) >= 3 or len(words & DIALOG_WORDS) >= 2) and self.regexish_use(node):
                self.add(node, "regex", v, ",".join(hit[:5]))

    def regexish_use(self, node) -> bool:
        if self.in_re_call(node):
            return True
        cur = self.parents.get(node)
        while cur is not None and not isinstance(cur, ast.stmt):
            cur = self.parents.get(cur)
        return isinstance(cur, ast.Assign) and bool(REGEXISH.search(getattr(node, "value", "")))

    def collection(self, node) -> None:
        elts = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(elts) < 3 or len(elts) != len(node.elts):
            return
        if any(_scripts(e) for e in elts):
            return                                        # reported per constant
        toks = [t for e in elts for t in re.findall(r"[a-z']+", e.lower())]
        if toks and sum(1 for t in toks if t in DIALOG_WORDS) / len(toks) >= 0.5:
            self.add(node, "wordset", ", ".join(elts[:6]) + (" ..." if len(elts) > 6 else ""))

    def compare(self, node) -> None:
        parts = [node.left] + list(node.comparators)
        codes = [c.value for c in parts if isinstance(c, ast.Constant) and c.value in LANG_CODES]
        for c in parts:
            if isinstance(c, (ast.Tuple, ast.Set, ast.List)):
                codes += [e.value for e in c.elts if isinstance(e, ast.Constant) and e.value in LANG_CODES]
        if codes:
            self.add(node, "langcode", "/".join(map(str, codes)))
        for c in parts:
            for sub in ast.walk(c):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, float) and 0 < sub.value < 1:
                    # a share / similarity limit: a float in (0, 1) in a comparison that holds a ratio, a length or a quotient
                    src = ast.dump(node)
                    if "ratio" in src or "len" in src or "Div" in src or "share" in src:
                        self.add(node, "threshold", f"{sub.value}")
                        return

    def assign(self, node) -> None:
        for t in node.targets:
            if isinstance(t, ast.Name) and re.search(r"(RATIO|THRESH|MIN_?SHARE|TOL|SHARE)", t.id) and \
                    isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                self.add(node, "threshold", f"{t.id} = {node.value.value}")


def _comments_and_docs(path: str, src: str) -> int:
    n = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and any(_scripts(tok.string)):
                n += 1
    except (tokenize.TokenError, IndentationError):
        pass
    return n


def scan(root: str = ROOT, pkg: str = PKG) -> tuple:
    """([finding, ...], {"comments_with_script": n, "files": n})."""
    findings, meta = [], {"comments_with_script": 0, "files": 0}
    base = os.path.join(root, pkg)
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in ("tests", "__pycache__", "lang"))
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(d, f)
            rel = os.path.relpath(path, root)
            src = open(path, encoding="utf-8").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            w = _Walker(rel)
            w.run(tree)
            findings += w.out
            meta["files"] += 1
            meta["comments_with_script"] += _comments_and_docs(path, src)
    findings.sort(key=lambda r: (r["file"], r["line"], r["kind"]))
    grouped: dict = {}
    for r in findings:                     # one row per statement: the literals of one line are one finding
        k = (r["file"], r["line"], r["kind"], r["class"], r["context"])
        if k not in grouped:
            grouped[k] = dict(r, n=1, items=[r["excerpt"]])
        elif r["excerpt"] not in grouped[k]["items"]:
            grouped[k]["n"] += 1
            grouped[k]["items"].append(r["excerpt"])
    uniq = []
    for r in grouped.values():
        if r["n"] > 1:
            r["excerpt"] = " ".join(r["items"][:4])[:70]
            r["detail"] = f"{r['detail']} x{r['n']}".strip()
        r.pop("items")
        uniq.append(r)
    return uniq, meta


def render_md(rows: list, meta: dict) -> str:
    out = ["| file:line | class | kind | context | what |", "|---|---|---|---|---|"]
    for r in rows:
        ex = r["excerpt"].replace("|", "\\|")
        out.append(f"| {r['file']}:{r['line']} | {r['class']} | {r['kind']} | {r['context']} | `{ex}` {r['detail']} |")
    return "\n".join(out)


def summary(rows: list, meta: dict) -> str:
    by = Counter(r["class"] for r in rows)
    kinds = Counter(r["kind"] for r in rows)
    lines = [f"{len(rows)} findings in {meta['files']} files; {meta['comments_with_script']} comments with non-Latin script "
             f"(not code, not counted)"]
    lines += [f"  {c:16s} {n}" for c, n in sorted(by.items(), key=lambda x: -x[1])]
    lines.append("  by kind: " + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--class", dest="cls", default="")
    ap.add_argument("--check", type=int, default=None, help="exit 1 when more than N findings remain")
    a = ap.parse_args(argv)
    rows, meta = scan(a.root)
    if a.cls:
        rows = [r for r in rows if r["class"] == a.cls]
    if a.format == "json":
        print(json.dumps({"meta": meta, "findings": rows}, ensure_ascii=False, indent=1))
    elif a.summary:
        print(summary(rows, meta))
    else:
        print(render_md(rows, meta))
        print()
        print(summary(rows, meta))
    return 1 if a.check is not None and len(rows) > a.check else 0


if __name__ == "__main__":
    sys.exit(main())
