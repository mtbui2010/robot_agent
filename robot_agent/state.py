"""Per-app state container.

`AgentState` bundles the four singletons that used to live as module-level
globals (dm / sr / cm / ua) so that each FastAPI app instance owns its own.

`create_app()` builds one `AgentState` per app and:
  - attaches it to `app.state.agent_state` (the FastAPI-idiomatic way; API
    routers read it via `request.app.state.agent_state`),
  - and stores it in `_CURRENT` as a per-process reference for code that runs
    outside the request scope (e.g. skill functions calling `utils.voice2text`,
    `skill_configs` proxies).

E1 = one robot per Python process, so a single process-global is sufficient.
Running multiple robots in parallel means multiple processes (different ports),
each with its own `_CURRENT`.

Config layout (see runtime._resolve_layout)
-------------------------------------------
A robot's configs are split into:

  * ``common_dir``   — shared across all deployment sites: skills.json,
    buttons.json, the ``active_location`` marker. Owned by SkillRegistry +
    ButtonManager.
  * ``locations_dir/<location>`` — per-site config: connections.json (device
    endpoints), skill_configs_override.json (global config), .env (API keys).
    Owned by DeviceManager + ConfigManager. The active site can be switched at
    runtime via :meth:`AgentState.switch_location`.

The legacy single-``data_dir`` layout (everything in one folder) still works:
runtime passes ``common_dir == locations_dir`` and ``location == ''`` so every
manager reads the same folder, exactly as before.
"""

import os
import re
import shutil
from pathlib import Path

from .core.device_manager import DeviceManager
from .core.skill_registry import SkillRegistry
from .core.unified_agent import UnifiedAgent
from .core.config_manager import ConfigManager
from .core.button_manager import ButtonManager
from .core.guide_manager import GuideManager
from .core.planning.base import WorldState

DEFAULT_LOCATION = 'default'

# .env keys we mirror into os.environ when a location is (re)loaded.
_ENV_INLINE_RE = re.compile(r'^\s*([^#=\s][^=]*)=(.*)$')


def _safe_location_name(name: str) -> str:
    """Validate a location name so it can only ever be a single path segment.

    Rejects empty names, path separators, '.'/'..', and leading dots so a
    request can never escape ``locations_dir`` or shadow ``.env``-style files.
    """
    name = (name or '').strip()
    if not name or name in ('.', '..') or name.startswith('.'):
        raise ValueError(f'invalid location name: {name!r}')
    if '/' in name or '\\' in name or os.sep in name:
        raise ValueError(f'location name may not contain path separators: {name!r}')
    if not re.fullmatch(r'[A-Za-z0-9._\- ]+', name):
        raise ValueError(f'location name has illegal characters: {name!r}')
    return name


class AgentState:
    def __init__(self, robot_pkg: str, common_dir: Path, locations_dir: Path,
                 location: str = DEFAULT_LOCATION, log_dir: Path | None = None,
                 node_name: str = 'robot_agent'):
        self.robot_pkg = robot_pkg
        self.common_dir = Path(common_dir)
        self.locations_dir = Path(locations_dir)
        self.location = location
        self.log_dir = Path(log_dir) if log_dir is not None else self.common_dir / 'logs'
        self.node_name = node_name

        self.common_dir.mkdir(parents=True, exist_ok=True)
        self.location_dir.mkdir(parents=True, exist_ok=True)

        self.dm = DeviceManager(data_dir=self.location_dir, node_name=node_name)
        self.sr = SkillRegistry(data_dir=self.common_dir)
        self.cm = ConfigManager(data_dir=self.location_dir, robot_pkg=robot_pkg)
        self.bm = ButtonManager(data_dir=self.common_dir)
        # Versioned planner guides (common_dir/guides.json); seeded from the
        # robot's guide modules on first run.
        self.guides = GuideManager(data_dir=self.common_dir, robot_pkg=robot_pkg)
        self.ua = UnifiedAgent(skill_registry=self.sr, device_manager=self.dm)
        # Symbolic world state that PERSISTS across plan runs (holding / arrived /
        # opened / on). The closed-loop driver reads & mutates this same object,
        # so a plan sees what the previous one left behind, and an operator can
        # edit it live via PUT /agent/world. E1 = one robot per process, so a
        # single instance here is sufficient.
        self.world = WorldState()
        self.load_world()   # restore persisted belief (arrived re-reconciled later)
        # Populated during create_app lifespan. Each item:
        #   {'phase': str, 'msg': str, 'traceback': str, 'timestamp': float}
        # Exposed via GET /diagnostics/boot.
        self.boot_errors: list[dict] = []

    # ------------------------------------------------------------------
    # World-state persistence (survives a process restart)
    # ------------------------------------------------------------------
    @property
    def _world_file(self) -> Path:
        return self.common_dir / 'world_state.json'

    def save_world(self) -> None:
        """Persist the symbolic world belief (best-effort). ``arrived`` is saved
        but NOT trusted on reload — it is re-reconciled from the localizer at the
        next plan. ``found_pose`` is kept for display but flagged stale once the
        base has moved (see :meth:`WorldState.found_pose_is_stale`)."""
        import json
        try:
            self._world_file.write_text(json.dumps(self.world.to_dict()))
        except Exception as e:
            print(f'[AgentState] could not persist world state: {e}')

    def load_world(self) -> None:
        """Reload the persisted belief into ``self.world`` (best-effort)."""
        import json
        try:
            if self._world_file.exists():
                d = json.loads(self._world_file.read_text())
                if isinstance(d, dict):
                    d.pop('arrived', None)           # re-reconciled from sensors
                    d.pop('found_pose_stale', None)  # transient display flag
                    self.world.update_from_dict(d)
        except Exception as e:
            print(f'[AgentState] could not load world state: {e}')

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    @property
    def location_dir(self) -> Path:
        # location == '' is the legacy single-dir mode → locations_dir itself.
        return self.locations_dir / self.location if self.location else self.locations_dir

    @property
    def data_dir(self) -> Path:
        """Backwards-compatible alias. Points at the active location dir, where
        connections.json / skill_configs_override.json / .env live."""
        return self.location_dir

    @property
    def _active_file(self) -> Path:
        return self.common_dir / 'active_location'

    # ------------------------------------------------------------------
    # Location discovery / persistence
    # ------------------------------------------------------------------
    def list_locations(self) -> list[str]:
        try:
            return sorted(p.name for p in self.locations_dir.iterdir() if p.is_dir())
        except FileNotFoundError:
            return []

    def read_active_location(self) -> str | None:
        """The persisted active location, if it still exists on disk."""
        try:
            if self._active_file.exists():
                name = self._active_file.read_text().strip()
                if name and (self.locations_dir / name).is_dir():
                    return name
        except Exception:
            pass
        return None

    def _write_active_location(self, name: str) -> None:
        try:
            self.common_dir.mkdir(parents=True, exist_ok=True)
            self._active_file.write_text(name + '\n')
        except Exception as e:
            print(f'[AgentState] could not persist active location: {e}')

    # ------------------------------------------------------------------
    # Location CRUD (used by api/locations.py)
    # ------------------------------------------------------------------
    def create_location(self, name: str, copy_from: str | None = None) -> str:
        name = _safe_location_name(name)
        target = self.locations_dir / name
        if target.exists():
            raise ValueError(f'location {name!r} already exists')
        target.mkdir(parents=True)
        if copy_from:
            src = self.locations_dir / _safe_location_name(copy_from)
            if not src.is_dir():
                raise ValueError(f'source location {copy_from!r} does not exist')
            for fn in ('connections.json', 'skill_configs_override.json', '.env'):
                s = src / fn
                if s.exists():
                    shutil.copy2(s, target / fn)
        return name

    def rename_location(self, old: str, new: str) -> str:
        new = _safe_location_name(new)
        src = self.locations_dir / old
        dst = self.locations_dir / new
        if not src.is_dir():
            raise ValueError(f'location {old!r} does not exist')
        if dst.exists():
            raise ValueError(f'location {new!r} already exists')
        src.rename(dst)
        # If we renamed the active location, repoint the managers in place —
        # the files are identical, so no teardown/reconnect is needed.
        if self.location == old:
            self.location = new
            self.dm.set_data_dir(dst)
            self.cm.set_data_dir(dst)
            self._write_active_location(new)
        return new

    def delete_location(self, name: str) -> None:
        if name == self.location:
            raise ValueError('cannot delete the active location; switch first')
        if name == DEFAULT_LOCATION:
            raise ValueError('cannot delete the default location')
        target = self.locations_dir / name
        if not target.is_dir():
            raise ValueError(f'location {name!r} does not exist')
        shutil.rmtree(target)

    def switch_location(self, name: str) -> str:
        """Hot-switch to a different location: tear down current device
        connections (keeping the shared ROS node alive), then reload
        connections / global configs / .env from the new site and reconnect."""
        target = self.locations_dir / name
        if not target.is_dir():
            raise ValueError(f'location {name!r} does not exist')
        if name == self.location:
            return self.location
        self.location = name
        self.dm.reload_from(target)
        self.cm.reload_from(target)
        self._reload_env(target)
        self._write_active_location(name)
        return name

    def _reload_env(self, location_dir: Path) -> None:
        """Mirror the location's .env into os.environ (API keys etc.)."""
        env_file = location_dir / '.env'
        if not env_file.exists():
            return
        try:
            for line in env_file.read_text().splitlines():
                m = _ENV_INLINE_RE.match(line)
                if m:
                    os.environ[m.group(1).strip()] = m.group(2).strip()
        except Exception as e:
            print(f'[AgentState] could not reload .env: {e}')


_CURRENT: AgentState | None = None


def set_current(state: AgentState) -> None:
    global _CURRENT
    _CURRENT = state


def current() -> AgentState:
    if _CURRENT is None:
        raise RuntimeError(
            'No active AgentState. robot_agent.create_app() must be called '
            'before code that depends on state.current().'
        )
    return _CURRENT
