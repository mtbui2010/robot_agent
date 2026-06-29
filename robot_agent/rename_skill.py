"""CLI to rename a skill in a robot package — name AND function.

Usage:
    python -m robot_agent.rename_skill <robot_pkg> <old> <new> [--yes]

Two modes, decided automatically:

  • The skill's function is named after the skill (``func_name == old``) — the
    usual case (``find_arm`` → ``find_grasp``). Then ``old`` is treated as an
    identifier and renamed **package-wide** (word-boundary): the ``def``, every
    ``import``/call in sibling files, the SKILL_CONFIGS key + function literal,
    and any skill-name string match in config (e.g. grace_namemap). The module
    file is also renamed (``<old>.py`` → ``<new>.py``) when the skill owns a
    dedicated single-skill file. This keeps imports across the package working.

  • The skill's function has a different name (alias, e.g. ``detect`` → func
    ``find``). Then ONLY the registry key is renamed (the shared function is
    left alone).

A plan banner lists every file + match count and asks to confirm (``--yes`` to
skip). Word boundaries make distinctive names safe; review the counts for short
or generic names.

Examples:
    python -m robot_agent.rename_skill kcare_robot find_arm find_grasp
    python -m robot_agent.rename_skill kcare_robot detect scan --yes
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from pathlib import Path

from robot_agent.delete_skill import (
    _err, _info, _locate_package, _entry_to_pair, _module_to_path,
    _list_public_top_level_funcs, _confirm,
)


def _iter_py(pkg_dir: Path):
    for p in sorted(pkg_dir.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        yield p


def _count_identifier(pkg_dir: Path, ident: str) -> dict[Path, int]:
    pat = re.compile(rf'\b{re.escape(ident)}\b')
    out: dict[Path, int] = {}
    for p in _iter_py(pkg_dir):
        try:
            n = len(pat.findall(p.read_text()))
        except Exception:
            continue
        if n:
            out[p] = n
    return out


def _rename_identifier(pkg_dir: Path, old: str, new: str) -> int:
    pat = re.compile(rf'\b{re.escape(old)}\b')
    total = 0
    for p in _iter_py(pkg_dir):
        try:
            text = p.read_text()
        except Exception:
            continue
        new_text, n = pat.subn(new, text)
        if n:
            p.write_text(new_text)
            total += n
    return total


def _rename_key_only(configs_file: Path, old: str, new: str) -> bool:
    """Rename just the dict key ``'old'`` -> ``'new'`` on its entry line."""
    text = configs_file.read_text()
    line_re = re.compile(
        rf"^([ \t]*)(['\"]){re.escape(old)}\2([ \t]*:[ \t]*.*)$", re.M)
    m = line_re.search(text)
    if not m:
        _err(f"couldn't find an entry for {old!r} in {configs_file.name}")
        return False
    new_line = f"{m.group(1)}{m.group(2)}{new}{m.group(2)}{m.group(3)}"
    configs_file.write_text(text[:m.start()] + new_line + text[m.end():])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        prog='python -m robot_agent.rename_skill',
        description='Rename a skill (and its function) in a robot package.',
    )
    parser.add_argument('pkg', help='importable robot package, e.g. kcare_robot')
    parser.add_argument('old', help='current skill name')
    parser.add_argument('new', help='new skill name')
    parser.add_argument('-y', '--yes', action='store_true', help='skip confirmation prompt')
    args = parser.parse_args()
    old, new = args.old, args.new

    if old == new:
        _info('old and new names are identical — nothing to do.')
        return 0
    if not new.isidentifier():
        _err(f"{new!r} is not a valid skill name (must be a Python identifier)")
        return 2

    pkg_dir = _locate_package(args.pkg)
    configs_file = pkg_dir / 'configs' / 'skills_config.py'
    if not configs_file.exists():
        _err(f"expected {configs_file}")
        return 2

    try:
        cfg_mod = importlib.import_module(f'{args.pkg}.configs.skills_config')
        importlib.reload(cfg_mod)
    except Exception as e:
        _err(f"cannot import {args.pkg}.configs.skills_config: {e}")
        return 2
    cfg: dict = getattr(cfg_mod, 'SKILL_CONFIGS', {})
    if old not in cfg:
        _err(f"skill {old!r} is not in SKILL_CONFIGS")
        return 2
    if new in cfg:
        _err(f"skill {new!r} already exists in SKILL_CONFIGS")
        return 2

    module_path, func_name = _entry_to_pair(cfg[old])
    target_file = _module_to_path(pkg_dir, args.pkg, module_path)
    module_basename = module_path.split('.')[-1]
    rel = lambda p: p.relative_to(pkg_dir.parent)

    rename_func = (func_name == old)

    # ── Mode A: function named after the skill → package-wide identifier rename
    if rename_func:
        # File rename only when this skill owns a dedicated single-skill file.
        others_same_module = [
            s for s, e in cfg.items()
            if s != old and _entry_to_pair(e)[0] == module_path
        ]
        file_funcs = _list_public_top_level_funcs(target_file.read_text()) if target_file.exists() else []
        dedicated = (not others_same_module) and file_funcs == [func_name]
        rename_file = dedicated and (module_basename == old) and target_file.exists()

        counts = _count_identifier(pkg_dir, old)
        print()
        print('─' * 64)
        print(f"  Rename skill+function  {old!r} -> {new!r}   ({args.pkg})")
        print('─' * 64)
        print(f"  Identifier {old!r} found in {len(counts)} file(s):")
        for p, n in counts.items():
            print(f"      {n:>3}×  {rel(p)}")
        print()
        print("  Planned actions:")
        print(f"    - word-boundary rename `{old}` -> `{new}` across the package")
        print(f"      (def, imports, calls, SKILL_CONFIGS key+function, name strings)")
        if rename_file:
            print(f"    - rename file {rel(target_file)} -> {new}.py")
        print('─' * 64)
        print()
        if not args.yes and not _confirm('Proceed?'):
            _info('aborted.')
            return 1

        total = _rename_identifier(pkg_dir, old, new)
        print(f"  ✓ Renamed   {total} occurrence(s) of `{old}` -> `{new}`")
        if rename_file:
            new_file = target_file.with_name(f'{new}.py')
            if new_file.exists():
                _err(f"{rel(new_file)} already exists — file NOT renamed (fix manually)")
            else:
                target_file.rename(new_file)
                print(f"  ✓ Renamed   file {target_file.name} -> {new_file.name}")

    # ── Mode B: aliased skill (func has a different name) → key only
    else:
        print()
        print('─' * 64)
        print(f"  Rename skill key  {old!r} -> {new!r}   ({args.pkg})")
        print('─' * 64)
        print(f"  Function {func_name}() (shared) is left unchanged.")
        print(f"  Only the SKILL_CONFIGS key is renamed in {rel(configs_file)}.")
        print('─' * 64)
        print()
        if not args.yes and not _confirm('Proceed?'):
            _info('aborted.')
            return 1
        if _rename_key_only(configs_file, old, new):
            print(f"  ✓ Updated   {rel(configs_file)} (key only)")

    print()
    print("  Next steps:")
    print("    1. Restart the agent:  make terminate && make run")
    print("    2. Or POST /skills/reload to update the live registry")
    return 0


if __name__ == '__main__':
    sys.exit(main())
