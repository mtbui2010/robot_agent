"""CLI to fork an existing robot package into a new one.

Usage:
    python -m robot_agent.new_robot <new_pkg> [--from <src_pkg>] [options]

Examples:
    # Full fork of kcare_robot into ../pnp_robot
    python -m robot_agent.new_robot pnp_robot --from kcare_robot

    # Fork aimed at different hardware: no inherited devices or numbers
    python -m robot_agent.new_robot lab_robot --from kcare_robot \
        --reset-sites --blank-configs

    # Somewhere other than a sibling directory, on another port
    python -m robot_agent.new_robot lab_robot --dest /tmp/lab_robot --port 8005

For a robot that shares nothing with an existing one, use the cookiecutter in
``robot_template`` instead — that generates mock skills and a clean skeleton.
This tool is for "same family as <src>, diverging from here".

What it does
------------
1. Copies the source repo, skipping ``.git``, caches, build output and captured
   data (see ``SKIP_DIRS`` / ``SKIP_GLOBS``).
2. Renames the inner package directory and rewrites the package identifier in
   every text file — imports, ``SKILL_CONFIGS`` module paths, ``pyproject``,
   ``Makefile``, entry points, docs.
3. Carries over ``configs/locations/`` — device endpoints, per-site overrides
   and the active-site marker — so the fork runs as-is. That is what you want
   when the fork drives the same hardware; the cost is that both packages then
   address the same robot, which is unsafe to do simultaneously.
   ``--reset-sites`` wipes them to one empty ``default`` site instead, for a
   fork aimed at different hardware.
4. Keeps ``configs/tasks.py`` (the robot's own hardware numbers) unless
   ``--blank-configs``, since a fork usually means the same hardware family.
   See :mod:`robot_agent.skill_config_defaults` for how those layer.

It does NOT rewrite prose spellings of the source robot's name ("KCare", "kcare"
outside the identifier). Those are reported at the end for a human to judge.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import sys
from pathlib import Path

IDENT_RE = re.compile(r'^[a-z_][a-z0-9_]*$')

SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
             'node_modules', 'build', 'dist', 'logs', '.vscode-test',
             # tool permissions and paths of whoever last worked on the source
             '.claude'}
SKIP_GLOBS = ('*.pyc', '*.pyo', '*.egg-info', '*.log', '*.npy', '*.png',
              '*.jpg', '*.bak', 'task_runs')
# Files whose contents get the identifier rewrite. Anything else is copied
# byte-for-byte.
TEXT_SUFFIXES = {'.py', '.toml', '.cfg', '.ini', '.md', '.txt', '.json', '.yaml',
                 '.yml', '.sh', '.bash', ''}
TEXT_NAMES = {'Makefile', 'Dockerfile', '.gitignore'}


def _is_text(p: Path) -> bool:
    return p.name in TEXT_NAMES or p.suffix in TEXT_SUFFIXES


def _skip(p: Path) -> bool:
    if any(part in SKIP_DIRS for part in p.parts):
        return True
    # Match on every path component, not just the leaf: `p.match('*.egg-info')`
    # is False for `kcare_robot.egg-info/PKG-INFO`, which is how a stale
    # egg-info directory used to survive the copy and then shadow imports.
    if any(part.endswith('.egg-info') for part in p.parts):
        return True
    return any(p.match(g) for g in SKIP_GLOBS)


def _src_root(src_pkg: str) -> Path:
    """Repo root of an importable robot package (the dir holding pyproject)."""
    mod = importlib.import_module(src_pkg)
    pkg_dir = Path(mod.__file__).resolve().parent
    root = pkg_dir.parent
    if not (root / 'pyproject.toml').exists():
        raise SystemExit(f'{src_pkg}: no pyproject.toml beside {pkg_dir} — '
                         f'expected <repo>/{src_pkg}/{src_pkg}/')
    return root


def _copy_tree(src: Path, dst: Path) -> int:
    n = 0
    for p in sorted(src.rglob('*')):
        rel = p.relative_to(src)
        if _skip(rel):
            continue
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            n += 1
    return n


def _rewrite(root: Path, old: str, new: str) -> tuple[int, list[str]]:
    """Replace the package identifier in every text file. Returns
    (files changed, other-spelling hits worth a human look)."""
    word = re.compile(rf'\b{re.escape(old)}\b')
    # e.g. 'kcare' / 'KCare' left over once 'kcare_robot' is gone
    stem = old.rsplit('_', 1)[0] if '_' in old else old
    loose = re.compile(rf'\b{re.escape(stem)}\b', re.I)

    changed, notes = 0, []
    for p in sorted(root.rglob('*')):
        if not p.is_file() or _skip(p.relative_to(root)) or not _is_text(p):
            continue
        try:
            s = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        out = word.sub(new, s)
        if out != s:
            p.write_text(out)
            changed += 1
        for i, line in enumerate(out.splitlines(), 1):
            if loose.search(line) and new not in line:
                notes.append(f'{p.relative_to(root)}:{i}: {line.strip()[:90]}')
    return changed, notes


def _reset_locations(root: Path, pkg: str, blank_configs: bool,
                     reset_sites: bool = False, src_pkg: str = '') -> list[str]:
    """Carry over or wipe the per-site config; optionally blank the numbers."""
    done = []
    cfg = root / pkg / 'configs'
    locs = cfg / 'locations'
    common = cfg / 'common'

    # Caches that must not survive: the skill registry re-seeds from
    # SKILL_CONFIGS, and the world state is a belief about a past run.
    stale = ['skills.json', 'world_state.json', 'guides.json']

    if reset_sites:
        if locs.is_dir():
            shutil.rmtree(locs)
        (locs / 'default').mkdir(parents=True, exist_ok=True)
        (locs / 'default' / 'connections.json').write_text('[]\n')
        done.append('configs/locations/ -> single empty "default" site (--reset-sites)')
        # The marker would point at a site that no longer exists.
        stale.append('active_location')
    else:
        sites = sorted(d.name for d in locs.iterdir() if d.is_dir()) if locs.is_dir() else []
        active = ''
        marker = common / 'active_location'
        if marker.exists():
            active = marker.read_text().strip()
        done.append(f'configs/locations/ carried over: {", ".join(sites) or "none"}'
                    f'{f" (active: {active})" if active else ""}')
        if sites:
            done.append('  NOTE: the fork now points at the SAME device endpoints as '
                        f'{src_pkg or "the source"}. Running both against one robot at '
                        'once is unsafe.')
            done.append('  Pass --reset-sites when the fork targets different hardware.')

    if common.is_dir():
        for name in stale:
            f = common / name
            if f.exists():
                f.unlink()
                done.append(f'removed configs/common/{name} (re-seeds on first run)')

    tasks = cfg / 'tasks.py'
    if blank_configs and tasks.exists():
        tasks.write_text(
            f'"""Runtime configs for {pkg} — this robot\'s own numbers.\n\n'
            'Resolution order (see robot_agent.skill_config_defaults):\n'
            '  1. configs/locations/<site>/skill_configs_override.json\n'
            '  2. this module\n'
            '  3. robot_agent.skill_config_defaults (neutral shapes only)\n\n'
            'An override replaces a whole group outright — there is no per-key\n'
            'merge — so declare a full group here, not a fragment.\n'
            'Start by filling in ARM_CONFIGS / LIFT_CONFIGS / ENV for your\n'
            'hardware; units are metres, degrees, seconds.\n'
            '"""\n')
        done.append('configs/tasks.py -> blanked (--blank-configs)')
    return done


def main() -> int:
    ap = argparse.ArgumentParser(prog='python -m robot_agent.new_robot')
    ap.add_argument('new_pkg', help='package name for the new robot, e.g. pnp_robot')
    ap.add_argument('--from', dest='src_pkg', default='kcare_robot',
                    help='robot package to fork (default: kcare_robot)')
    ap.add_argument('--dest', default=None,
                    help='destination dir (default: sibling of the source repo)')
    ap.add_argument('--port', default=None, help='default agent port for the new robot')
    ap.add_argument('--blank-configs', action='store_true',
                    help="empty configs/tasks.py instead of keeping the source robot's numbers")
    ap.add_argument('--reset-sites', action='store_true',
                    help='wipe configs/locations/ down to one empty "default" site. Use when '
                         'the fork targets DIFFERENT hardware; the default keeps the source\'s '
                         'device endpoints so the fork runs as-is.')
    ap.add_argument('--force', action='store_true', help='overwrite an existing destination')
    a = ap.parse_args()

    if not IDENT_RE.match(a.new_pkg):
        print(f'[new_robot] {a.new_pkg!r} is not a valid python package name', file=sys.stderr)
        return 2
    if a.new_pkg == a.src_pkg:
        print('[new_robot] source and destination package are the same', file=sys.stderr)
        return 2

    try:
        src = _src_root(a.src_pkg)
    except (ImportError, SystemExit) as e:
        print(f'[new_robot] cannot locate {a.src_pkg}: {e}', file=sys.stderr)
        return 2

    dst = Path(a.dest).resolve() if a.dest else src.parent / a.new_pkg
    if dst.exists():
        if not a.force:
            print(f'[new_robot] {dst} already exists (use --force to overwrite)', file=sys.stderr)
            return 2
        shutil.rmtree(dst)

    print(f'[new_robot] {a.src_pkg}  ({src})')
    print(f'[new_robot]   -> {a.new_pkg}  ({dst})')

    n = _copy_tree(src, dst)
    print(f'[new_robot] copied {n} files')

    inner = dst / a.src_pkg
    if inner.is_dir():
        inner.rename(dst / a.new_pkg)
        print(f'[new_robot] package dir {a.src_pkg}/ -> {a.new_pkg}/')

    changed, notes = _rewrite(dst, a.src_pkg, a.new_pkg)
    print(f'[new_robot] rewrote the package identifier in {changed} files')

    for line in _reset_locations(dst, a.new_pkg, a.blank_configs, a.reset_sites,
                                 src_pkg=a.src_pkg):
        print(f'[new_robot] {line}')

    mk = dst / 'Makefile'
    if mk.exists():
        # Without this the fork's `make install` would populate the SOURCE
        # robot's conda env — same CONDA_ENV, different package.
        env_name = a.new_pkg[:-len('_robot')] if a.new_pkg.endswith('_robot') else a.new_pkg
        txt = mk.read_text()
        new_txt, n_env = re.subn(r'^(CONDA_ENV\s*\?=\s*)\S+', rf'\g<1>{env_name}', txt, flags=re.M)
        if n_env:
            mk.write_text(new_txt)
            print(f'[new_robot] CONDA_ENV -> {env_name}')

    if a.port:
        if mk.exists():
            mk.write_text(re.sub(r'^PORT \?= \d+', f'PORT ?= {a.port}', mk.read_text(), flags=re.M))
            print(f'[new_robot] default PORT -> {a.port}')

    # Sanity: the registry must point at the new package, or nothing will load.
    sc = dst / a.new_pkg / 'configs' / 'skills_config.py'
    if sc.exists():
        txt = sc.read_text()
        if a.src_pkg in txt:
            print(f'[new_robot] WARNING: {a.src_pkg} still referenced in skills_config.py')
        else:
            n_skills = len(re.findall(r"^\s*'[^']+'\s*:\s*\(", txt, re.M))
            print(f'[new_robot] skills_config.py points at {a.new_pkg} ({n_skills} skills)')

    if notes:
        print(f'\n[new_robot] {len(notes)} line(s) still mention the source robot by name '
              f'(prose, not imports) — review by hand:')
        for line in notes[:15]:
            print(f'    {line}')
        if len(notes) > 15:
            print(f'    ... and {len(notes) - 15} more')

    print(f'''
Next:
    cd {dst}
    make install                  # or: make install USE_CURRENT=1
    make doctor                   # verifies ROS + every skill imports
    # then fill in configs/tasks.py and add devices in the Connection panel
''')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
