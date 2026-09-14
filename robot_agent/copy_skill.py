"""CLI to copy a skill from one robot package into another.

Usage:
    python -m robot_agent.copy_skill <skill>... --from <src_pkg> --to <dst_pkg>

Examples:
    # One skill and whatever it imports from its own package
    python -m robot_agent.copy_skill grip --from kcare_robot --to lab_robot

    # Several at once
    python -m robot_agent.copy_skill inform vlm pointcloud \\
        --from kcare_robot --to lab_robot

    # Just the named module, no dependency chasing
    python -m robot_agent.copy_skill pick --from kcare_robot --to lab_robot --no-deps

Skills are copied, not shared. A skill is bound to the hardware it was written
for, so each robot owns an editable copy and diverges freely; the cost is that
a fix has to be re-applied per robot.

What it does
------------
1. Resolves ``<skill>`` through the source's ``SKILL_CONFIGS`` to a module.
2. Follows ``from <src_pkg>...`` imports transitively and copies those modules
   too, so helper modules (``_pick_helpers`` and friends) come along.
3. Rewrites the package identifier in every copied file.
4. Registers each named skill in the destination's ``SKILL_CONFIGS``.
5. Reports which ``robot_agent.skill_configs`` groups the copied code reads —
   those must exist in the destination's ``configs/tasks.py`` or a site
   override, or the skill will run against empty defaults. See
   :mod:`robot_agent.skill_config_defaults` for the resolution order.

Existing destination files are never overwritten without ``--force``.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import re
import sys
from pathlib import Path

# Config groups a skill can read; reported so the destination can declare them.
KNOWN_CONFIG_NAMES = {
    'GRIP_CONFIGS', 'LIFT_CONFIGS', 'HEAD_CONFIGS', 'ARM_CONFIGS',
    'MOBILE_CONFIGS', 'FIND_CONFIGS', 'CALIB_PARAMS', 'ENV', 'HOME_LOC',
    'LLM_SERVERS', 'KR2EN', 'EN2KR', 'NO_ACTION', 'ME', 'VLA_CLIENTS',
    'STANDING_OBJ_NAMES', 'LYING_OBJ_NAMES', 'HAVING_HANDLE_OBJ_NAMES',
}


def _err(msg: str) -> None:
    print(f'[copy_skill] {msg}', file=sys.stderr)


def _pkg_dir(pkg: str) -> Path:
    return Path(importlib.import_module(pkg).__file__).resolve().parent


def _skill_configs(pkg: str) -> dict:
    return getattr(importlib.import_module(f'{pkg}.configs.skills_config'),
                   'SKILL_CONFIGS', {})


def _module_file(pkg: str, module_path: str) -> Path:
    """'<pkg>.skills.pick' -> <pkg dir>/skills/pick.py"""
    rel = module_path.split('.')[1:]           # drop the package name itself
    return _pkg_dir(pkg).joinpath(*rel).with_suffix('.py')


def _local_imports(src_file: Path, src_pkg: str) -> set[str]:
    """Modules of `src_pkg` that `src_file` imports (dotted, package-qualified)."""
    try:
        tree = ast.parse(src_file.read_text())
    except (SyntaxError, OSError) as e:
        _err(f'cannot parse {src_file.name}: {e}')
        return set()
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith(f'{src_pkg}.'):
            found.add(n.module)
        elif isinstance(n, ast.Import):
            for al in n.names:
                if al.name.startswith(f'{src_pkg}.'):
                    found.add(al.name)
    return found


def _collect(src_pkg: str, roots: list[str], follow: bool) -> list[str]:
    """Transitive closure of source-package modules to copy."""
    seen: list[str] = []
    queue = list(roots)
    while queue:
        mod = queue.pop(0)
        if mod in seen:
            continue
        f = _module_file(src_pkg, mod)
        if not f.exists():
            _err(f'{mod}: {f} not found — skipping')
            continue
        seen.append(mod)
        if follow:
            queue.extend(sorted(_local_imports(f, src_pkg) - set(seen)))
    return seen


def _configs_used(files: list[Path]) -> set[str]:
    used = set()
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, OSError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module == 'robot_agent.skill_configs':
                used |= {al.name for al in n.names} & KNOWN_CONFIG_NAMES
    return used


def _register(configs_file: Path, skill: str, module_path: str, func: str) -> bool:
    """Insert one entry before the closing brace of SKILL_CONFIGS."""
    if not configs_file.exists():
        _err(f'{configs_file} not found; cannot register {skill}')
        return False
    text = configs_file.read_text()
    stem = module_path.split('.', 2)[-1]       # 'skills.pick' -> keep after pkg
    stem = stem.split('.', 1)[-1] if stem.startswith('skills.') else stem
    line = f"    '{skill}': (f'{{_PKG}}.{stem}', '{func}'),"

    m = re.search(r'SKILL_CONFIGS\b[^=]*=\s*\{', text)
    if not m:
        _err(f'no SKILL_CONFIGS dict in {configs_file.name}; add manually:\n    {line}')
        return False
    open_idx, depth, close_idx = m.end() - 1, 0, None
    for i, ch in enumerate(text[open_idx:], start=open_idx):
        depth += (ch == '{') - (ch == '}')
        if depth == 0 and ch == '}':
            close_idx = i
            break
    if close_idx is None:
        _err(f'SKILL_CONFIGS in {configs_file.name} is not closed; add manually:\n    {line}')
        return False

    before = text[:close_idx].rstrip()
    if not before.endswith('{') and not before.endswith(','):
        before += ','
    configs_file.write_text(before + '\n' + line + '\n' + text[close_idx:].lstrip(' \t'))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(prog='python -m robot_agent.copy_skill')
    ap.add_argument('skills', nargs='+', help='skill name(s) as registered in the source')
    ap.add_argument('--from', dest='src_pkg', required=True, help='source robot package')
    ap.add_argument('--to', dest='dst_pkg', required=True, help='destination robot package')
    ap.add_argument('--no-deps', action='store_true',
                    help='copy only the named modules, not what they import')
    ap.add_argument('--force', action='store_true', help='overwrite destination files')
    a = ap.parse_args()

    try:
        src_cfg = _skill_configs(a.src_pkg)
        dst_dir = _pkg_dir(a.dst_pkg)
    except ImportError as e:
        _err(f'{e}  (both packages must be importable — pip install -e them)')
        return 2

    unknown = [s for s in a.skills if s not in src_cfg]
    if unknown:
        _err(f'not registered in {a.src_pkg}: {", ".join(unknown)}')
        print(f'         available: {", ".join(sorted(src_cfg))}', file=sys.stderr)
        return 2

    roots = sorted({src_cfg[s][0] for s in a.skills})
    modules = _collect(a.src_pkg, roots, follow=not a.no_deps)
    print(f'[copy_skill] {a.src_pkg} -> {a.dst_pkg}')
    print(f'[copy_skill] {len(modules)} module(s): {", ".join(m.split(".")[-1] for m in modules)}')

    word = re.compile(rf'\b{re.escape(a.src_pkg)}\b')
    copied, skipped, written = [], [], []
    for mod in modules:
        srcf = _module_file(a.src_pkg, mod)
        rel = srcf.relative_to(_pkg_dir(a.src_pkg))
        dstf = dst_dir / rel
        if dstf.exists() and not a.force:
            skipped.append(str(rel))
            copied.append(srcf)
            continue
        dstf.parent.mkdir(parents=True, exist_ok=True)
        dstf.write_text(word.sub(a.dst_pkg, srcf.read_text()))
        copied.append(srcf)
        written.append(str(rel))

    for r in written:
        print(f'[copy_skill]   wrote {r}')
    for r in skipped:
        print(f'[copy_skill]   kept existing {r} (use --force to overwrite)')

    cfg_file = dst_dir / 'configs' / 'skills_config.py'
    dst_cfg = _skill_configs(a.dst_pkg)
    for s in a.skills:
        mod, func = src_cfg[s]
        if s in dst_cfg:
            print(f'[copy_skill]   {s!r} already registered in {a.dst_pkg} — left alone')
        elif _register(cfg_file, s, mod, func):
            print(f'[copy_skill]   registered {s!r} -> {mod.split(".", 1)[-1]}:{func}')

    needed = _configs_used(copied)
    if needed:
        print('\n[copy_skill] the copied code reads these config groups:')
        print(f'    {", ".join(sorted(needed))}')
        print(f'    Declare them in {a.dst_pkg}/configs/tasks.py (or a site override),')
        print('    otherwise they resolve to the neutral shapes in')
        print('    robot_agent.skill_config_defaults and the skill runs on empty values.')

    print(f'\nNext:\n    cd <{a.dst_pkg} repo> && make doctor    # confirms every skill imports\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
