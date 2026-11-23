"""The loop guards of the architecture lint (``tools/lint_arch.py`` calls ``check``): checks over the LOOP's files.

What: over the files of ``lint_arch.LOOP_SET`` that exist, (1) no import of embodiplan and no private name of the action model
(one source of action rules: only ``core/planning/action_model.py`` reads them); (2) the language-literal rows of
``tools/lang_inventory.py`` may not grow, and ``core/pack`` / ``packs`` hold none; (3) the vocabulary ratchet: the lines with a
standard action name or a goal kind in double quotes may not grow.
Why: the core generalization moves code out of ``assist_loop.py`` step by step; guards that read one file would fall only
because code moved (design, invariant 18). Summing over the set, a count falls only when a use is really removed.
Switch off: remove the ``loop`` section of ``tools/arch_baseline.json`` (a missing count is not checked); the internals and
the no-language-layer rules have no baseline.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
# source: embodiplan base.py ROBOT_ACTIONS (14 names) + Heat, Cool, Clean (EMBODIPLAN_EXTRA_ACTIONS) + Say, WaitForUnload (the
# needs of core/planning/goals.py GOAL_KINDS at 8002347). A fixed list: the ratchet must not move when the tables move to a pack.
STANDARD_ACTIONS = ("MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close", "TurnOn", "TurnOff", "Wash", "Sit", "LieOn",
                    "Serve", "Wait", "Heat", "Cool", "Clean", "Say", "WaitForUnload")
# source: core/planning/goals.py GOAL_KINDS and OPTIONAL_KINDS at 8002347
GOAL_KIND_NAMES = ("on", "in", "open", "close", "go", "say", "deliver", "give")
RATCHETS = ("lang_rows", "vocab_actions", "vocab_kinds")


def _quoted(names) -> "re.Pattern":
    return re.compile(r'"(?:' + "|".join(map(re.escape, names)) + r')"')


_ACTION_RX, _KIND_RX = _quoted(STANDARD_ACTIONS), _quoted(GOAL_KIND_NAMES)


def _read(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def vocabulary(root: str, files: List[str]) -> Dict[str, int]:
    """{"vocab_actions": lines with a quoted standard action name, "vocab_kinds": lines with a quoted goal kind}, summed."""
    lines = [ln for rel in files for ln in _read(root, rel).splitlines()]
    return {"vocab_actions": sum(1 for ln in lines if _ACTION_RX.search(ln)),
            "vocab_kinds": sum(1 for ln in lines if _KIND_RX.search(ln))}


def internals(root: str, files: List[str]) -> Dict[str, str]:
    """{finding key: message} for an import of embodiplan or of a private name of the action model in the loop's files."""
    found: Dict[str, str] = {}
    for rel in files:
        try:
            tree = ast.parse(_read(root, rel))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                   [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            for m in mods:
                if m.split(".")[0] == "embodiplan":
                    found[f"internals: {rel} -> {m}"] = f"{rel} imports {m}: action rules are read only by action_model.py"
            if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[-1] == "action_model":
                for a in n.names:
                    if a.name.startswith("_"):
                        found[f"internals: {rel} -> action_model.{a.name}"] = f"{rel} imports the private {a.name} of action_model"
            if isinstance(n, ast.Attribute) and n.attr.startswith("_") and isinstance(n.value, ast.Name) \
                    and n.value.id == "action_model":
                found[f"internals: {rel} -> action_model.{n.attr}"] = f"{rel} reads the private {n.attr} of action_model"
    return found


def _lang_inventory():
    spec = importlib.util.spec_from_file_location("lang_inventory", os.path.join(HERE, "lang_inventory.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lang_rows(root: str, files: List[str], no_lang_layers) -> Tuple[int, Dict[str, str]]:
    """(language-literal rows in the loop's files, {finding: message} for a row in a layer that may hold none)."""
    rows = _lang_inventory().scan(root)[0]
    rel = lambda r: r["file"].replace(os.sep, "/")              # noqa: E731
    mine = sum(1 for r in rows if rel(r) in set(files))
    bad = {f"language: {rel(r)}:{r['line']}": f"{rel(r)}:{r['line']} holds a language literal ({r['kind']}, {r['class']}): "
           f"the layer {[p for p in no_lang_layers if rel(r).startswith(p)][0]} holds none (put it in a data file)"
           for r in rows if rel(r).startswith(tuple(no_lang_layers))}
    return mine, bad


def check(root: str, files: List[str], base: Dict[str, int], no_lang_layers) -> Tuple[Dict[str, str], List[str], Dict[str, int]]:
    """(new findings, stale baseline entries, current counts) of the loop guards; ``base``: the ``loop`` baseline section."""
    n_lang, new = lang_rows(root, files, no_lang_layers)
    new.update(internals(root, files))
    cur = dict(vocabulary(root, files), lang_rows=n_lang)
    for k in RATCHETS:
        if k in base and cur[k] > base[k]:
            new[f"loop: {k}"] = f"the loop's files grew in {k}: {base[k]} -> {cur[k]} (the count may only fall)"
    stale = [f"loop: {k} {base[k]} -> {cur[k]}" for k in RATCHETS if k in base and cur[k] < base[k]]
    return new, stale, {k: cur[k] for k in RATCHETS}
