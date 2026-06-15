"""Pre-flight diagnostic for robot_agent.

Run before starting a robot agent server:

    python -m robot_agent.diagnose <robot_pkg> [<data_dir>]

Example:
    python -m robot_agent.diagnose kcare_robot \
        ../robot_skills_and_configs/kcare_robot/kcare_robot/data

Checks performed:
    1. Python version >= 3.10
    2. rclpy importable (ROS2 sourced)
    3. pyconnect importable
    4. <robot_pkg>.configs.skills_config imports + has SKILL_CONFIGS
    5. Each entry in SKILL_CONFIGS can be imported
    6. <data_dir>/connections.json parses (if present)

Exit code 0 if all checks pass, 1 otherwise.
"""

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

_OK   = '[OK]   '
_FAIL = '[FAIL] '
_WARN = '[WARN] '
_INFO = '[INFO] '

_failures = 0


def _ok(msg):    print(_OK + msg)
def _info(msg):  print(_INFO + msg)
def _warn(msg):  print(_WARN + msg)
def _fail(msg):
    global _failures
    _failures += 1
    print(_FAIL + msg)


def main() -> int:
    parser = argparse.ArgumentParser(prog='python -m robot_agent.diagnose')
    parser.add_argument('robot_pkg', help='importable robot package, e.g. kcare_robot')
    parser.add_argument('data_dir', nargs='?', default=None,
                        help='data dir (defaults to <robot_pkg>/data inside the package)')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()

    print('=' * 64)
    print(f' robot_agent diagnostics ({args.robot_pkg})')
    print('=' * 64)

    # ── 1. Python ──────────────────────────────────────────────────────
    ver = '.'.join(map(str, sys.version_info[:3]))
    if sys.version_info >= (3, 10):
        _ok(f'Python {ver}')
    else:
        _fail(f'Python {ver}  (need >= 3.10)')

    # ── 2. rclpy ───────────────────────────────────────────────────────
    try:
        import rclpy  # noqa: F401
        distro = os.environ.get('ROS_DISTRO', 'unknown')
        _ok(f'rclpy importable  (ROS_DISTRO={distro})')
    except Exception as e:
        _fail(f'rclpy NOT importable: {type(e).__name__}: {e}')
        _info('  Hint: source /opt/ros/humble/setup.bash')

    # ── 3. pyconnect ───────────────────────────────────────────────────
    try:
        import pyconnect  # noqa: F401
        _ok('pyconnect importable')
    except Exception as e:
        _fail(f'pyconnect NOT importable: {type(e).__name__}: {e}')
        _info('  Hint: pip install -e <path-to-pyconnect>')

    # ── 4. SKILL_CONFIGS module ────────────────────────────────────────
    mod_name = f'{args.robot_pkg}.configs.skills_config'
    skill_configs = {}
    try:
        skills_mod = importlib.import_module(mod_name)
        skill_configs = getattr(skills_mod, 'SKILL_CONFIGS', None)
        if skill_configs is None:
            _fail(f'{mod_name} has no SKILL_CONFIGS attribute')
            skill_configs = {}
        else:
            _ok(f'{mod_name}  -- {len(skill_configs)} entries')
    except Exception as e:
        _fail(f'Cannot import {mod_name}: {type(e).__name__}: {e}')
        _info(f'  Hint: pip install -e <path-to-{args.robot_pkg}>')

    # ── 5. Each skill module ───────────────────────────────────────────
    if skill_configs:
        ok_count = fail_count = 0
        for name, entry in skill_configs.items():
            if isinstance(entry, tuple):
                mp, fn = entry
            else:
                mp, fn = entry, name
            try:
                mod = importlib.import_module(mp)
                if hasattr(mod, fn):
                    ok_count += 1
                    if args.verbose:
                        _ok(f'  {name:24s}  <-  {mp}:{fn}')
                else:
                    fail_count += 1
                    _fail(f'  {name:24s}  <-  {mp}:{fn}  (no such function)')
            except Exception as e:
                fail_count += 1
                _fail(f'  {name:24s}  <-  {mp}:{fn}  ({type(e).__name__}: {e})')
        if fail_count == 0:
            _ok(f'All {ok_count} skill module(s) importable')
        else:
            _info(f'{ok_count} ok, {fail_count} failed')

    # ── 6. connections.json ─────────────────────────────────────────────
    # Split layout: configs/locations/<active|default>/connections.json.
    # Legacy layout: <pkg>/data/connections.json. Explicit --data_dir wins.
    connections_file = None
    if args.data_dir:
        connections_file = Path(args.data_dir).resolve() / 'connections.json'
    else:
        try:
            pkg_mod = importlib.import_module(args.robot_pkg)
            pkg_dir = Path(pkg_mod.__file__).parent
            locations = pkg_dir / 'configs' / 'locations'
            if locations.is_dir():
                active = 'default'
                marker = pkg_dir / 'configs' / 'common' / 'active_location'
                if marker.exists():
                    name = marker.read_text().strip()
                    if name and (locations / name).is_dir():
                        active = name
                connections_file = locations / active / 'connections.json'
            else:
                connections_file = pkg_dir / 'data' / 'connections.json'
        except Exception:
            connections_file = None

    if connections_file is not None:
        if connections_file.exists():
            try:
                connections = json.loads(connections_file.read_text())
                _ok(f'connections.json parses  -- {len(connections)} entries')
                for d in connections:
                    print(f'         - {d.get("name","?"):20s}  ({d.get("type","?")})')
            except Exception as e:
                _fail(f'connections.json invalid: {type(e).__name__}: {e}')
                _info(f'  Path: {connections_file}')
        else:
            _info(f'connections.json not present at {connections_file}')

    # ── summary ────────────────────────────────────────────────────────
    print('=' * 64)
    if _failures == 0:
        _ok('All checks passed.')
        return 0
    _fail(f'{_failures} check(s) failed.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
