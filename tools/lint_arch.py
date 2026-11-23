#!/usr/bin/env python3
"""Architecture lint of robot_agent (docs/architecture.md): import layers, size limits, the option wiring ratchet.

Checks (all fail on NEW findings only; the old ones are recorded in ``tools/arch_baseline.json`` and may only shrink):

* ``layers``   a module imports a layer it may not (``tools/arch_rules.json``: contract <- engine <- options; interact and api
               above the engine and never importing an option); an option imports another option; a module imports a ``_private``
               name of another module; a module imports the benchmark, a robot pack or the tests. RELATIVE imports are resolved.
* ``size``     a function or file over its limit (engine functions 80 lines, option functions 60; files 1200, options 400) that is
               not in the baseline, or a baseline offender that grew.
* ``wiring``   the loop's files (``LOOP_SET``: ``assist_loop.py`` and the modules its code moves to, the ones that exist): the
               number of distinct option keys each reads from ``self.cfg`` and of ``if`` branches on ``self.cfg``, summed
               over the files, may not grow, and a registered option's keys appear in them 0 times.
* ``loop``     the same files: no import of embodiplan or of a private name of ``action_model`` (one source of action rules);
               the language-literal rows of ``tools/lang_inventory.py`` and the lines with a standard action name or a goal
               kind in double quotes may not grow (ratchets); ``core/pack`` and ``packs`` hold no language literal at all.

A baseline entry that no longer applies is also an error ("shrink it": run ``--update``): the baseline is a ratchet.

    python tools/lint_arch.py            # report, exit 1 on a new finding or a stale baseline entry
    python tools/lint_arch.py --update   # rewrite the baseline to the current state (a reviewer sees the diff)
    python tools/lint_arch.py --top 15   # the largest offenders (the shrink plan in docs/architecture.md)
"""

from __future__ import annotations

import ast
import glob
import importlib.util
import json
import os
import re
import sys
from typing import Dict, Iterator, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = "robot_agent"
RULES_FILE = os.path.join(HERE, "arch_rules.json")
BASELINE_FILE = os.path.join(HERE, "arch_baseline.json")
LOOP = "robot_agent/core/planning/assist_loop.py"
# the loop's files: the guards follow the code when a step moves it out of assist_loop.py (design, invariant 18)
LOOP_SET = (LOOP, "robot_agent/core/planning/phases/*.py", "robot_agent/core/planning/places.py",
            "robot_agent/core/planning/loop_config.py", "robot_agent/core/planning/find_ladder.py", "robot_agent/core/pack/*.py")
NO_LANG_LAYERS = ("robot_agent/core/pack/", "robot_agent/packs/")


def load_rules(path: str = RULES_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── files and imports ──────────────────────────────────────────────────────────────────────────────────────────────
def py_files(root: str) -> Iterator[Tuple[str, str]]:
    """(path relative to root with '/', dotted module) for every module of the package."""
    for d, dirs, fs in os.walk(os.path.join(root, PKG)):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", "tests")]
        for f in sorted(fs):
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                mod = rel[:-3].replace("/", ".")
                yield rel, (mod[: -len(".__init__")] if mod.endswith(".__init__") else mod)


def _resolve(mod: str, is_pkg: bool, node: ast.ImportFrom) -> Optional[str]:
    """The absolute dotted name of ``from <...> import`` (relative levels resolved against ``mod``)."""
    if not node.level:
        return node.module
    base = mod.split(".") if is_pkg else mod.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join(base + ([node.module] if node.module else []))


def imports_of(root: str, rel: str, mod: str, known: set) -> List[Tuple[str, Optional[str]]]:
    """[(imported module, private name or None)] for every import in the file, also inside functions."""
    try:
        tree = ast.parse(open(os.path.join(root, rel), encoding="utf-8").read())
    except SyntaxError:
        return []
    is_pkg = rel.endswith("__init__.py")
    out: List[Tuple[str, Optional[str]]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.name, None) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = _resolve(mod, is_pkg, n)
            if not base:
                continue
            for a in n.names:
                sub = f"{base}.{a.name}"
                target = sub if sub in known else base          # `from . import x` imports the module x
                out.append((target, a.name if a.name.startswith("_") and target == base else None))
    return out


def layer_of(mod: str, rules: dict) -> Optional[str]:
    best, best_len = None, -1
    for name, paths in rules["layers"].items():
        for p in paths:
            dotted = p.replace("/", ".")
            if (mod == dotted or mod.startswith(dotted + ".")) and len(dotted) > best_len:
                best, best_len = name, len(dotted)
    return best


# ── layers ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def check_layers(root: str, rules: dict) -> Dict[str, str]:
    """{finding key: message}. The key is ``<file> -> <imported module>[._name]`` so that a baseline can name it."""
    files = list(py_files(root))
    known = {m for _r, m in files}
    found: Dict[str, str] = {}
    allowed = rules["may_import"]
    forbidden = rules.get("forbidden_modules", [])
    only = rules.get("only_in", {})
    for rel, mod in files:
        mine = layer_of(mod, rules)
        for target, private in imports_of(root, rel, mod, known):
            tl = layer_of(target, rules)
            if mine and tl and tl != mine and tl not in allowed.get(mine, []):
                found[f"layer: {rel} -> {target}"] = f"layer {mine} imports layer {tl} ({target})"
            elif mine and tl == mine and mine in rules.get("no_cross", []) and not _same_family(mod, target):
                found[f"cross: {rel} -> {target}"] = f"option {mod} imports another option {target}"
            if private and not target.startswith(mod.rsplit(".", 0)[0] + ".") and target != mod:
                found[f"private: {rel} -> {target}.{private}"] = f"{mod} imports the private name {private} of {target}"
            top = target.split(".")[0]
            for pat in forbidden:
                if top == pat:
                    found[f"forbidden: {rel} -> {target}"] = f"{mod} imports {target} (not allowed in the package)"
            for pat, ok in only.items():
                if top == pat and not any(rel.startswith(a) for a in ok):
                    found[f"only_in: {rel} -> {target}"] = f"{pat} may be imported only in {ok}; {mod} imports it"
    return found


def _same_family(mod: str, target: str) -> bool:
    return mod == target or mod.startswith(target + ".") or target.startswith(mod + ".")


# ── size ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _limits(rel: str, rules: dict) -> Tuple[Optional[int], Optional[int]]:
    """(function limit, file limit) for a file, from the longest matching ``size`` path; None = not checked."""
    best = None
    for s in rules["size"]:
        if rel.startswith(s["path"]) and (best is None or len(s["path"]) > len(best["path"])):
            best = s
    return (best.get("max_function"), best.get("max_file")) if best else (None, None)


def measure(root: str, rules: dict) -> Dict[str, int]:
    """{"fn: file::qualified.name#n": lines, "file: path": lines} for every function / file with a limit (all sizes)."""
    out: Dict[str, int] = {}
    for rel, _mod in py_files(root):
        mf, mfile = _limits(rel, rules)
        if mf is None and mfile is None:
            continue
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        out[f"file: {rel}"] = src.count("\n") + 1
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        seen: Dict[str, int] = {}

        def visit(node, prefix):
            for n in ast.iter_child_nodes(node):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = prefix + n.name
                    k = seen[q] = seen.get(q, -1) + 1
                    out[f"fn: {rel}::{q}#{k}"] = (n.end_lineno or n.lineno) - n.lineno + 1
                    visit(n, q + ".")
                elif isinstance(n, ast.ClassDef):
                    visit(n, prefix + n.name + ".")
                else:
                    visit(n, prefix)
        visit(tree, "")
    return out


def over_limit(sizes: Dict[str, int], rules: dict) -> Dict[str, int]:
    res = {}
    for k, v in sizes.items():
        rel = k.split(": ", 1)[1].split("::")[0]
        mf, mfile = _limits(rel, rules)
        lim = mf if k.startswith("fn:") else mfile
        if lim is not None and v > lim:
            res[k] = v
    return res


def check_size(root: str, rules: dict, base: Dict[str, int]) -> Tuple[Dict[str, str], Dict[str, int]]:
    cur = over_limit(measure(root, rules), rules)
    bad = {}
    for k, v in cur.items():
        if k not in base:
            bad[f"size: {k}"] = f"new offender {k}: {v} lines"
        elif v > base[k]:
            bad[f"size: {k}"] = f"{k} grew: {base[k]} -> {v}"
    return bad, cur


# ── wiring ratchet ─────────────────────────────────────────────────────────────────────────────────────────────────
_CFG_KEY = re.compile(r"self\.cfg(?:\.get)?\(?\[?[\"']([a-z_0-9.]+)[\"']")
_CFG_IF = re.compile(r"\bif\b[^\n]*self\.cfg")


def loop_files(root: str) -> List[str]:
    """The files of ``LOOP_SET`` that exist (paths relative to root, '/', in the order of the set)."""
    out: List[str] = []
    for pat in LOOP_SET:
        out += sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in glob.glob(os.path.join(root, pat)))
    return list(dict.fromkeys(out))


def _src(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


def wiring(root: str) -> dict:
    """The option keys read (distinct per file) and the ``if`` branches on ``self.cfg``, summed over the loop's files."""
    per = {rel: _src(root, rel) for rel in loop_files(root)}
    keys = [set(_CFG_KEY.findall(s)) for s in per.values()]
    lines = per[LOOP].count("\n") + 1 if LOOP in per else 0
    return {"cfg_keys": sum(map(len, keys)), "cfg_branches": sum(len(_CFG_IF.findall(s)) for s in per.values()),
            "keys": sorted(set().union(*keys)), "lines": lines}


def check_wiring(root: str, base: dict, option_names: List[str]) -> Dict[str, str]:
    cur, bad = wiring(root), {}
    for k in ("cfg_keys", "cfg_branches"):
        if cur[k] > base.get(k, 10 ** 9):
            bad[f"wiring: {k}"] = f"the loop's files read more option keys ({k}): {base[k]} -> {cur[k]}"
    for rel in loop_files(root):
        src = _src(root, rel)
        for name in option_names:
            if re.search(r"""["']""" + re.escape(name) + r"""["']""", src):
                key = f"wiring: {name}" if rel == LOOP else f"wiring: {rel}: {name}"
                bad[key] = f"{rel} names the registered option key {name!r}: it belongs to its option module"
    return bad


def check_loop(root: str, base: dict) -> Tuple[Dict[str, str], List[str], Dict[str, int]]:
    """The loop guards of ``tools/lint_loop.py`` over the loop's files: (new findings, stale entries, counts)."""
    spec = importlib.util.spec_from_file_location("lint_loop", os.path.join(HERE, "lint_loop.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check(root, loop_files(root), base, NO_LANG_LAYERS)


# ── driver ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def load_baseline(path: str = BASELINE_FILE) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"layers": {}, "size": {}, "wiring": {}}


def registered_key_names() -> List[str]:
    sys.path.insert(0, ROOT)
    from robot_agent.core.options import REGISTRY
    return sorted(REGISTRY.all_keys())


def run(root: str = ROOT, rules_path: str = RULES_FILE, baseline_path: str = BASELINE_FILE, names: Optional[List[str]] = None):
    """(new findings, stale baseline entries, current state)."""
    rules, base = load_rules(rules_path), load_baseline(baseline_path)
    layers = check_layers(root, rules)
    size_bad, size_cur = check_size(root, rules, base.get("size", {}))
    wiring_bad = check_wiring(root, base.get("wiring", {}), registered_key_names() if names is None else names)
    loop_bad, loop_stale, loop_cur = check_loop(root, base.get("loop", {}))
    new = {k: m for k, m in layers.items() if k not in base.get("layers", {})}
    new.update(size_bad)
    new.update(wiring_bad)
    new.update(loop_bad)
    stale = [f"layers: {k}" for k in base.get("layers", {}) if k not in layers]
    stale += [f"size: {k}" for k, v in base.get("size", {}).items() if k not in size_cur or size_cur[k] < v]
    w = wiring(root)
    stale += [f"wiring: {k} {base['wiring'][k]} -> {w[k]}" for k in ("cfg_keys", "cfg_branches")
              if k in base.get("wiring", {}) and w[k] < base["wiring"][k]]
    stale += loop_stale
    state = {"layers": {k: layers[k] for k in sorted(layers)}, "size": dict(sorted(size_cur.items())),
             "wiring": {"cfg_keys": w["cfg_keys"], "cfg_branches": w["cfg_branches"]}, "loop": loop_cur}
    return new, stale, state


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    new, stale, state = run()
    if "--top" in argv:
        n = int(argv[argv.index("--top") + 1])
        for k, v in sorted(state["size"].items(), key=lambda x: -x[1])[:n]:
            print(f"{v:6d}  {k}")
        return 0
    if "--update" in argv:
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)
            f.write("\n")
        print(f"baseline written: {len(state['layers'])} import findings, {len(state['size'])} size offenders, wiring {state['wiring']}")
        return 0
    for k, m in sorted(new.items()):
        print("VIOLATION", m)
    for s in stale:
        print("STALE    ", s, "(fixed: run tools/lint_arch.py --update to shrink the baseline)")
    print(f"architecture: {len(new)} new finding(s), {len(stale)} stale baseline entr(ies); baseline {len(state['layers'])} import "
          f"findings, {len(state['size'])} size offenders, wiring {state['wiring']}, loop {state['loop']}")
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main())
