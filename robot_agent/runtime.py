"""Headless bootstrap for robot_agent.

Both `create_app()` (FastAPI/UI mode) and `cli.main()` (CLI mode) call
`bootstrap()` to do the heavy init: rclpy, AgentState, skills, configs,
devices. The same singleton state is reused — calling `bootstrap()` twice
is a no-op after the first time.

Python-API users do not call `bootstrap()` directly. The `@skill_entry`
decorator (in `robot_agent.skills`) triggers it lazily on first skill call.

Env vars honored:
    ROBOT_AGENT_LOG_LEVEL      INFO | DEBUG | ...
    ROBOT_AGENT_DEBUG_RESPONSE 1 to include tracebacks in skill error returns
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

_BOOTED = False
_LOCK = threading.Lock()
_BOOT_ROBOT_PKG: Optional[str] = None


def _default_data_dir(robot_pkg: str) -> Path:
    mod = importlib.import_module(robot_pkg)
    return Path(mod.__file__).parent / 'data'


def bootstrap(robot_pkg: str,
              data_dir: Optional[Path] = None,
              load_devices: bool = True,
              node_name: Optional[str] = None,
              verbose: bool = True):
    """Init ROS + AgentState + skills/configs/devices. Idempotent.

    Args:
        robot_pkg: importable package name that exposes
            ``<robot_pkg>.configs.skills_config.SKILL_CONFIGS``.
        data_dir: where to read/write skills.json, connections.json, logs/.
            Defaults to ``<robot_pkg>/data``.
        load_devices: if True, blocks until all devices in connections.json
            have been (re)connected. CLI and Python-API normally want this;
            the FastAPI app loads devices in a background thread for snappy
            startup and passes False.
        node_name: ROS2 node name. Defaults to ``robot_pkg``. CLI sets
            ``<robot_pkg>_cli_<pid>`` to avoid clashing with a running UI.
        verbose: print boot errors to stderr (useful in CLI/Python mode where
            there is no /diagnostics endpoint).

    Returns:
        The active ``AgentState``.
    """
    global _BOOTED, _BOOT_ROBOT_PKG
    with _LOCK:
        if _BOOTED:
            from .state import current
            if _BOOT_ROBOT_PKG != robot_pkg:
                raise RuntimeError(
                    f'bootstrap already ran for {_BOOT_ROBOT_PKG!r}; '
                    f'cannot re-bootstrap for {robot_pkg!r} in the same process'
                )
            return current()

        data_dir = Path(data_dir or _default_data_dir(robot_pkg)).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        try:
            import rclpy
            if not rclpy.ok():
                rclpy.init()
        except ImportError:
            print('ERROR: ROS2 not found. Source /opt/ros/*/setup.bash before running.',
                  file=sys.stderr)
            raise

        from .logging_config import setup_logging
        setup_logging(log_dir=data_dir / 'logs', log_name=robot_pkg)

        from .state import AgentState, set_current
        state = AgentState(
            robot_pkg=robot_pkg,
            data_dir=data_dir,
            node_name=node_name or robot_pkg,
        )
        set_current(state)

        _load_skills(state, robot_pkg)
        _load_configs(state)
        _load_buttons(state)
        if load_devices:
            _load_devices_sync(state)
        else:
            _load_devices_async(state)

        if verbose and state.boot_errors:
            for err in state.boot_errors:
                print(f"[bootstrap WARN {err['phase']}] {err['msg']}",
                      file=sys.stderr)

        _BOOTED = True
        _BOOT_ROBOT_PKG = robot_pkg
        return state


def _record(state, phase: str, exc: Exception):
    import time
    state.boot_errors.append({
        'phase': phase,
        'msg': str(exc),
        'traceback': traceback.format_exc(),
        'timestamp': time.time(),
    })


def _load_skills(state, robot_pkg: str):
    sr = state.sr
    if sr.load_saved():
        return
    try:
        skills_mod = importlib.import_module(f'{robot_pkg}.configs.skills_config')
        sr.load_from_skill_configs(skills_mod.SKILL_CONFIGS)
        sr._save()
    except Exception as e:
        _record(state, 'skills_load', e)


def _load_configs(state):
    try:
        state.cm.load_saved()
    except Exception as e:
        _record(state, 'configs_load', e)


def _load_buttons(state):
    try:
        state.bm.load_saved()
    except Exception as e:
        _record(state, 'buttons_load', e)


def _load_devices_sync(state):
    try:
        state.dm.load_saved()
    except Exception as e:
        _record(state, 'devices_load', e)


def _load_devices_async(state):
    def _run():
        try:
            state.dm.load_saved()
        except Exception as e:
            _record(state, 'devices_load', e)
    threading.Thread(target=_run, daemon=True).start()


def shutdown():
    """Best-effort teardown for CLI/script use. UI mode uses FastAPI lifespan."""
    global _BOOTED, _BOOT_ROBOT_PKG
    try:
        from .state import current
        state = current()
        if state.dm._ros_node is not None:
            try:
                state.dm._ros_node.stop()
            except Exception:
                pass
    except Exception:
        pass
    try:
        import rclpy
        if rclpy.ok():
            rclpy.shutdown()
    except Exception:
        pass
    _BOOTED = False
    _BOOT_ROBOT_PKG = None
