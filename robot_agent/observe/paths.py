"""One root for the runtime data the agent writes (run logs, memories, world belief).

Today these files live next to the robot's configs, in ``common_dir`` (see
``robot_agent.state``), named per machine. ``ROBOT_AGENT_DATA_DIR`` moves all of
them under one root (a ``data/`` folder or a NAS mount) without touching the
configs. Unset, every path is exactly where it was, so nothing moves on a robot
that does not set it.

=================  ======================================  =========================================
kind               today (``common_dir`` = C, host = H)    with ``ROBOT_AGENT_DATA_DIR`` = R
=================  ======================================  =========================================
``task_runs``      ``C/task_runs/H/`` (dir)                ``R/task_runs/H/``
``run_records``    ``C/run_records/H/`` (dir)              ``R/run_records/H/``
``metrics``        ``C/run_records/H/metrics.jsonl``       ``R/run_records/H/metrics.jsonl``
``grace_memory``   ``C/grace_memory.H.jsonl``              ``R/grace_memory.H.jsonl``
``object_memory``  ``C/object_memory.H.json``              ``R/object_memory.H.json``
``world_state``    ``C/world_state.H.json``                ``R/world_state.H.json``
=================  ======================================  =========================================

Reading falls back to the old location: a file kind that is read and written in
place (the memories, the world belief) is copied from its old path the first
time the new one is asked for and does not exist yet, so a robot that starts
setting the variable keeps what it had learned. :func:`read_path` is the
explicit fallback for lookups (a run log by id).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional, Union

ENV_VAR = 'ROBOT_AGENT_DATA_DIR'

# kind -> (is_dir, name under the root / common_dir; {host} is substituted)
_KINDS = {
    'task_runs':     (True,  'task_runs/{host}'),
    'run_records':   (True,  'run_records/{host}'),
    'metrics':       (False, 'run_records/{host}/metrics.jsonl'),
    'grace_memory':  (False, 'grace_memory.{host}.jsonl'),
    'object_memory': (False, 'object_memory.{host}.json'),
    'world_state':   (False, 'world_state.{host}.json'),
}
# files a consumer reads and rewrites in place: seeded from the old location
_IN_PLACE = {'grace_memory', 'object_memory', 'world_state'}

PathLike = Union[str, os.PathLike]


def kinds() -> list[str]:
    return list(_KINDS)


def _host(host: Optional[str]) -> str:
    if host:
        return host
    from ..state import host_id
    return host_id()


def _common(common_dir: Optional[PathLike]) -> Path:
    if common_dir is not None:
        return Path(common_dir)
    from .. import state
    return Path(getattr(state.current(), 'common_dir', '.'))


def root() -> Optional[Path]:
    """The configured data root, or None (today's layout)."""
    v = os.environ.get(ENV_VAR, '').strip()
    return Path(v).expanduser() if v else None


def _rel(kind: str, host: str) -> str:
    try:
        return _KINDS[kind][1].format(host=host)
    except KeyError:
        raise ValueError(f'unknown data kind {kind!r}; known: {", ".join(_KINDS)}') from None


def legacy_path(kind: str, common_dir: Optional[PathLike] = None, host: Optional[str] = None) -> Path:
    """Where ``kind`` lived before ``ROBOT_AGENT_DATA_DIR`` (under ``common_dir``)."""
    return _common(common_dir) / _rel(kind, _host(host))


def data_dir(kind: str, common_dir: Optional[PathLike] = None, host: Optional[str] = None) -> Path:
    """The path to use for ``kind`` now: a directory for ``task_runs`` /
    ``run_records``, a file for the others (see the module table).

    ``common_dir`` / ``host`` default to ``current().common_dir`` and
    ``host_id()``; pass them when the caller already has them. Nothing is
    created, except that an in-place file kind missing under the new root is
    copied from its old location (best-effort).
    """
    host = _host(host)
    r = root()
    if r is None:
        return _common(common_dir) / _rel(kind, host)
    p = r / _rel(kind, host)
    if kind in _IN_PLACE and not p.exists():
        try:
            old = _common(common_dir) / _rel(kind, host)
        except Exception:
            old = None
        if old is not None and old.is_file() and old.resolve() != p.resolve():
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, p)
            except OSError:
                pass
    return p


def read_path(kind: str, common_dir: Optional[PathLike] = None, host: Optional[str] = None) -> Path:
    """The new path when it exists, else the old one when that exists, else the new one."""
    new = data_dir(kind, common_dir, host)
    if new.exists():
        return new
    try:
        old = legacy_path(kind, common_dir, host)
    except Exception:
        return new
    return old if old.exists() else new


def search_dirs(kind: str, common_dir: Optional[PathLike] = None, host: Optional[str] = None) -> list[Path]:
    """The new and the old directory of a dir kind (deduplicated, new first)."""
    out = [data_dir(kind, common_dir, host)]
    try:
        old = legacy_path(kind, common_dir, host)
        if old not in out:
            out.append(old)
    except Exception:
        pass
    return out
