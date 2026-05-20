"""CLI entry point shared by all robot packages.

Each robot package adds a thin shim::

    # kcare_robot/__main__.py
    from robot_agent.cli import main
    def cli(): main(robot_pkg='kcare_robot')

and registers it in ``pyproject.toml``::

    [project.scripts]
    kcare_robot = "kcare_robot.__main__:cli"

Usage::

    kcare_robot find::apple
    kcare_robot find::apple estimate_grasp=true camera=arm
    kcare_robot move::kitchen
    kcare_robot --list                # list registered skills
    kcare_robot --raw find inputs=apple    # explicit kwarg form
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _coerce(v: str) -> Any:
    if v == '':
        return ''
    low = v.lower()
    if low == 'true':
        return True
    if low == 'false':
        return False
    if low in ('none', 'null'):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    # try JSON-parsable (lists, dicts, quoted strings)
    if v[:1] in '[{"':
        try:
            return json.loads(v)
        except ValueError:
            pass
    return v


def _parse_args(argv: list[str]) -> tuple[str, dict]:
    """Parse argv into ``(skill_name, params)``.

    Accepted forms (mix freely):
        find::apple
        find::apple estimate_grasp=true camera=arm
        find inputs=apple
        find inputs=apple estimate_grasp=true
    """
    if not argv:
        raise SystemExit('usage: <pkg> <skill>[::<inputs>] [key=value ...]')

    head = argv[0]
    name, sep, inline = head.partition('::')
    params: dict[str, Any] = {}
    if sep and inline != '':
        params['inputs'] = _coerce(inline)

    for arg in argv[1:]:
        if '=' not in arg:
            raise SystemExit(f"bad argument {arg!r}: expected key=value")
        k, _, v = arg.partition('=')
        params[k.strip()] = _coerce(v)
    return name, params


def _print_skills(state) -> int:
    rows = sorted(state.sr.all(), key=lambda s: s['name'])
    width = max((len(r['name']) for r in rows), default=10)
    for r in rows:
        loc = f"{r['module_path']}:{r['func_name']}" if r['type'] == 'internal' else r['url']
        print(f"  {r['name']:<{width}}  {loc}")
    print(f"\n{len(rows)} skills")
    return 0


def _json_default(o: Any):
    if hasattr(o, 'item') and callable(o.item):
        try:
            return o.item()
        except Exception:
            pass
    if hasattr(o, 'tolist') and callable(o.tolist):
        return o.tolist()
    if isinstance(o, (bytes, bytearray)):
        return o.decode('utf-8', errors='replace')
    return str(o)


def main(robot_pkg: str, argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]

    if argv and argv[0] in ('-h', '--help'):
        print(f'usage: {robot_pkg} <skill>[::<inputs>] [key=value ...]')
        print(f'       {robot_pkg} --list')
        return 0

    import atexit
    from .runtime import bootstrap, shutdown
    atexit.register(shutdown)

    node_name = f'{robot_pkg}_cli_{os.getpid()}'

    if argv and argv[0] in ('-l', '--list'):
        state = bootstrap(robot_pkg, node_name=node_name, load_devices=False)
        return _print_skills(state)

    name, params = _parse_args(argv)
    state = bootstrap(robot_pkg=robot_pkg, node_name=node_name, load_devices=True)
    result = state.sr.execute(name, params, node=state.dm._ros_node)

    json.dump(result, sys.stdout, default=_json_default, indent=2, ensure_ascii=False)
    sys.stdout.write('\n')
    return 0 if (isinstance(result, dict) and result.get('isdone', True)) else 1
