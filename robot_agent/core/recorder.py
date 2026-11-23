"""The raw-episode recorder the dashboard starts and stops (``/recorder`` API).

The recorder is a node outside this package (``robot_teleop.recorder``): it speaks two JSON-in-String topics,
``/record/state`` (latched) and ``/record/command``  {"cmd": "start", "prompt", "meta"} | {"cmd": "stop", "label"} | {"cmd": "discard"}.
This module names no robot: whether it exists, where the episodes are and which teleop function it drives come from the ``RECORDER`` config
group (the robot package's ``configs/tasks.py`` or a site override)::

    RECORDER = {'data_dir': '../vla_data', 'teleop': 'arm', 'prompts': {...see core/prompts.py}}

``data_dir`` is the folder holding raw/ edits/ exports/ trash/. A RELATIVE path is taken from the robot package's repo root (``../vla_data`` = the ``vla_data`` folder next to
the repo), so the same config works wherever the repos are mounted (/remote_dir on the robot, /media/.../remote_dir on a dev PC). An absolute path is used as it is. The older
``dir`` (the raw folder itself) still works.

No group: the feature does nothing (the dashboard shows nothing).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

log = logging.getLogger('robot_agent.recorder')

STATE_TOPIC, COMMAND_TOPIC = '/record/state', '/record/command'
STALE_S = 3.0


def resolve_dirs(cfg: dict, base) -> dict:
    """The config with the data folder made absolute: adds ``data_dir`` (absolute) and ``dir`` (its raw/ folder), and ``data_dir_given`` as written. ``cfg['data_dir']`` wins over the
    legacy ``cfg['dir']`` (the raw folder). A relative path is taken from ``base`` (the robot package's repo root)."""
    from pathlib import Path
    given = cfg.get('data_dir')
    legacy = cfg.get('dir')
    if not given and not legacy:
        return dict(cfg)
    def absolute(p):
        p = Path(str(p)).expanduser()
        return Path(os.path.normpath(p if p.is_absolute() else Path(base or '.') / p))
    data = absolute(given) if given else absolute(legacy).parent
    raw = data / 'raw' if given else absolute(legacy)
    return {**cfg, 'data_dir': str(data), 'dir': str(raw), **({'data_dir_given': str(given)} if given else {})}


class RecorderBridge:
    def __init__(self, get_cfg, get_transport, get_base=lambda: None):
        self._get_cfg, self._get_transport, self._get_base = get_cfg, get_transport, get_base
        self._lock = threading.Lock()
        self._state = None                  # (decoded state, monotonic receive time)
        self._subscribed = False
        self._transport = None

    def config(self) -> dict | None:
        c = self._get_cfg()
        return resolve_dirs(c, self._get_base()) if isinstance(c, dict) and c else None

    def _ensure(self):
        tr = self._get_transport()
        if tr is None:
            return None
        if tr is not self._transport:
            self._transport, self._subscribed = tr, False
        if not self._subscribed:
            self._subscribed = True
            tr.subscribe(STATE_TOPIC, self._on_state)
        return tr

    def _on_state(self, text: str) -> None:
        try:
            d = json.loads(text)
        except (TypeError, ValueError):
            return
        if isinstance(d, dict):
            with self._lock:
                self._state = (d, time.monotonic())

    def status(self) -> dict | None:
        """None when nothing is configured; else {available, state?, age_s?, dir, teleop, fields}."""
        cfg = self.config()
        if cfg is None:
            return None
        self._ensure()
        with self._lock:
            got = self._state
        age = None if got is None else time.monotonic() - got[1]
        out = {'available': got is not None and age <= STALE_S, 'dir': cfg.get('dir'), 'teleop': cfg.get('teleop'),
               'fields': list(((cfg.get('prompts') or {}).get('fields')) or [])}
        if got is not None:
            out.update(state=got[0], age_s=round(age, 2))
        return out

    def send(self, cmd: dict) -> bool:
        """Publish a command; False when there is no ROS."""
        tr = self._ensure()
        if tr is None:
            return False
        tr.publish(COMMAND_TOPIC, json.dumps(cmd))
        return True


_BRIDGE: RecorderBridge | None = None


def bridge() -> RecorderBridge:
    global _BRIDGE
    if _BRIDGE is None:
        from .teleop import RosTransport
        cache: dict = {}

        def cfg():
            from ..state import current
            return current().cm.get('RECORDER')

        def transport():
            from ..state import current
            node = getattr(current().dm, '_ros_node', None)
            if node is None:
                return None
            if cache.get('node') is not node:
                cache['node'], cache['tr'] = node, RosTransport(node)
            return cache['tr']
        def base():
            """The robot package's repo root (the folder above the package): a relative data_dir is taken from here."""
            import importlib
            from pathlib import Path
            from ..state import current
            try:
                return Path(importlib.import_module(current().robot_pkg).__file__).resolve().parent.parent
            except Exception:
                return None
        _BRIDGE = RecorderBridge(cfg, transport, base)
    return _BRIDGE
