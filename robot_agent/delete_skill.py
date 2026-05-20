"""CLI to remove a skill from a robot package.

Usage:
    python -m robot_agent.delete_skill <robot_pkg> <skill_name> [--yes]

Behaviour (always prompts for confirmation unless --yes / -y):
  - Removes ``<skill_name>`` from ``<pkg>/configs/skills_config.py``.
  - Inspects the target file the skill resolves to:
      * If no other entry in SKILL_CONFIGS points to the same module AND the
        target file's only top-level function (ignoring underscore-prefixed
        helpers) is this skill → the entire file is deleted.
      * Else → only the ``def <skill>`` block is spliced out of the file.
  - Aliases (other registry entries pointing to the same (module, func) pair)
    are detected and reported: removing the alias name does NOT delete the
    function while another alias still references it.

Examples:
    python -m robot_agent.delete_skill kcare_robot wave
    python -m robot_agent.delete_skill kcare_robot find --yes
"""

from __future__ import annotations

import argparse
import ast
import importlib
import re
import sys
from pathlib import Path


def _err(msg: str) -> None:
    print(f'[delete-skill] ERROR: {msg}', file=sys.stderr)


def _info(msg: str) -> None:
    print(f'[delete-skill] {msg}')


def _locate_package(pkg: str) -> Path:
    try:
        mod = importlib.import_module(pkg)
    except ImportError as e:
        _err(f"cannot import {pkg!r}: {e}")
        sys.exit(2)
    if mod.__file__ is None:
        _err(f"{pkg!r} has no __file__ (namespace package?)")
        sys.exit(2)
    return Path(mod.__file__).parent


def _entry_to_pair(entry) -> tuple[str, str]:
    if isinstance(entry, tuple):
        return entry[0], entry[1]
    return entry, ''


def _module_to_path(pkg_dir: Path, pkg_name: str, module_path: str) -> Path:
    """Resolve 'kcare_robot.skills.recognition' → <pkg_dir>/skills/recognition.py."""
    parts = module_path.split('.')
    if parts[0] != pkg_name:
        return Path(*parts) / '__unresolved__.py'   # foreign module, shouldn't happen
    rel = parts[1:]
    return pkg_dir.joinpath(*rel[:-1], f'{rel[-1]}.py') if rel else pkg_dir / '__init__.py'


def _list_public_top_level_funcs(text: str) -> list[str]:
    """Names of top-level `def`s that don't start with `_`."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
            out.append(node.name)
    return out


def _find_func_node(text: str, func_name: str) -> ast.FunctionDef | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def _remove_func_from_file(path: Path, func_name: str) -> bool:
    """Splice ``def <func_name>`` (with its decorators) out of the file."""
    text = path.read_text()
    node = _find_func_node(text, func_name)
    if node is None:
        _err(f"def {func_name} not found in {path.name}")
        return False

    start_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    end_line = node.end_lineno or node.lineno

    lines = text.splitlines(keepends=True)
    new_lines = lines[:start_line - 1] + lines[end_line:]
    # Collapse 3+ consecutive blank lines into 2.
    out = ''.join(new_lines)
    out = re.sub(r'\n{4,}', '\n\n\n', out)
    if not out.endswith('\n'):
        out += '\n'
    path.write_text(out)
    return True


def _remove_from_skills_config(configs_file: Path, skill: str) -> bool:
    """Delete the dict entry ``'<skill>': <value>,`` (single-line) from the
    SKILL_CONFIGS dict. Returns True if anything was removed."""
    if not configs_file.exists():
        _err(f"{configs_file} not found")
        return False
    text = configs_file.read_text()

    pattern = rf"^[ \t]*['\"]{re.escape(skill)}['\"]\s*:[^\n]*\n"
    new_text, n = re.subn(pattern, '', text, count=1, flags=re.M)
    if n == 0:
        _err(f"couldn't find an entry for {skill!r} in {configs_file.name}")
        return False
    configs_file.write_text(new_text)
    return True


def _confirm(prompt: str, default_no: bool = True) -> bool:
    suffix = ' [y/N] ' if default_no else ' [Y/n] '
    try:
        ans = input(prompt + suffix).strip().lower()
    except EOFError:
        return False
    if not ans:
        return not default_no
    return ans in ('y', 'yes')


def main() -> int:
    parser = argparse.ArgumentParser(
        prog='python -m robot_agent.delete_skill',
        description='Remove a skill from a robot package.',
    )
    parser.add_argument('pkg',   help='importable robot package, e.g. kcare_robot')
    parser.add_argument('skill', help='skill name to remove')
    parser.add_argument('-y', '--yes', action='store_true',
                        help='skip confirmation prompt')
    args = parser.parse_args()

    pkg_dir      = _locate_package(args.pkg)
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
    if args.skill not in cfg:
        _info(f"skill {args.skill!r} is not in SKILL_CONFIGS — nothing to do.")
        return 0

    module_path, func_name = _entry_to_pair(cfg[args.skill])
    target_file = _module_to_path(pkg_dir, args.pkg, module_path)

    # Other entries pointing to the same (module, func) — these are aliases.
    aliases = [
        s for s, e in cfg.items()
        if s != args.skill and _entry_to_pair(e) == (module_path, func_name)
    ]
    # Other entries pointing to the same module but different function.
    other_funcs_in_module = sorted({
        _entry_to_pair(e)[1]
        for s, e in cfg.items()
        if s != args.skill and _entry_to_pair(e)[0] == module_path
        and _entry_to_pair(e)[1] != func_name
    })

    will_remove_func = not aliases
    will_delete_file = (
        will_remove_func
        and not other_funcs_in_module
        and target_file.exists()
        and _list_public_top_level_funcs(target_file.read_text()) == [func_name]
    )

    # ── Plan banner ───────────────────────────────────────────────────────
    print()
    print('─' * 64)
    print(f"  Delete skill {args.skill!r} from {args.pkg}")
    print('─' * 64)
    print(f"  Function:        {func_name}() in {target_file.relative_to(pkg_dir.parent)}")
    print(f"  Module path:     {module_path}")
    if aliases:
        print(f"  Aliases:         {', '.join(repr(a) for a in aliases)}")
        print(f"                   (function stays — other aliases still reference it)")
    elif other_funcs_in_module:
        print(f"  Other skills in same file: {', '.join(other_funcs_in_module)}")
        print(f"                   (only `def {func_name}` will be removed)")
    print()
    print("  Planned actions:")
    print(f"    - remove '{args.skill}' from {configs_file.relative_to(pkg_dir.parent)}")
    if will_delete_file:
        print(f"    - DELETE FILE {target_file.relative_to(pkg_dir.parent)}")
    elif will_remove_func:
        if target_file.exists():
            print(f"    - splice `def {func_name}` out of {target_file.relative_to(pkg_dir.parent)}")
        else:
            print(f"    - (file {target_file.relative_to(pkg_dir.parent)} already absent)")
    else:
        print(f"    - keep file/function (still used by alias(es))")
    print('─' * 64)
    print()

    if not args.yes:
        if not _confirm('Proceed?'):
            _info('aborted.')
            return 1

    # ── Execute ───────────────────────────────────────────────────────────
    if will_delete_file:
        target_file.unlink()
        print(f"  ✓ Deleted    {target_file.relative_to(pkg_dir.parent)}")
    elif will_remove_func and target_file.exists():
        if _remove_func_from_file(target_file, func_name):
            print(f"  ✓ Removed    def {func_name}() from {target_file.relative_to(pkg_dir.parent)}")

    if _remove_from_skills_config(configs_file, args.skill):
        print(f"  ✓ Updated    {configs_file.relative_to(pkg_dir.parent)}")

    print()
    print("  Next steps:")
    print("    1. Restart the agent:  make terminate && make run")
    print("    2. Or POST /skills/reload to update the live registry")
    return 0


if __name__ == '__main__':
    sys.exit(main())
