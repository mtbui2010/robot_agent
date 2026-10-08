import copy, importlib, threading
from pathlib import Path

from .shared_json import SharedJson

KNOWN_CONFIGS = [
    'GRIP_CONFIGS', 'LIFT_CONFIGS', 'HEAD_CONFIGS', 'ARM_CONFIGS',
    'MOBILE_CONFIGS', 'FIND_CONFIGS', 'CALIB_PARAMS',
    'ENV', 'HOME_LOC', 'LLM_SERVERS', 'KR2EN', 'EN2KR', 'QA_CONFIGS', 'HRI_CONFIGS',
]


class ConfigManager:
    def __init__(self, data_dir: Path, robot_pkg: str):
        self._robot_pkg = robot_pkg
        self._overrides: dict = {}
        self._lock = threading.RLock()
        self.set_data_dir(data_dir)

    def set_data_dir(self, new_data_dir: Path):
        """Repoint persistence at a new directory, keeping current overrides
        (used by rename, where the file moves with the dir)."""
        self._data_dir = Path(new_data_dir)
        self._persist_file = self._data_dir / 'skill_configs_override.json'
        # A site may be used by more than one machine: a save merges this
        # process's groups into the file (one group = one item, see
        # shared_json.py) and reads pick up the other machines' saves.
        base = getattr(getattr(self, '_store', None), 'base', None)
        self._store = SharedJson(self._persist_file, check_sec=2.0)
        self._store.base = copy.deepcopy(base) if base is not None else None

    def reload_from(self, new_data_dir: Path):
        """Hot-switch to a different location's skill_configs_override.json:
        drop the current overrides and load the new site's instead. Skills
        read live via the proxies, so the change is visible immediately.

        The swap is clean: `update` never writes into the robot's `tasks`
        module, so nothing of the previous site survives here.
        """
        self._overrides = {}
        self.set_data_dir(new_data_dir)
        self.load_saved()

    def _tasks(self):
        """Return the robot's tasks config module, or None if it can't be
        imported. Overrides and proxy defaults work without it."""
        try:
            return importlib.import_module(f'{self._robot_pkg}.configs.tasks')
        except ImportError:
            return None

    def get(self, name: str):
        # Overrides always take precedence over module defaults.
        self.refresh()
        if name in self._overrides:
            return copy.deepcopy(self._overrides[name])
        tasks = self._tasks()
        if tasks is not None:
            val = getattr(tasks, name, None)
            if val is not None:
                return copy.deepcopy(val)
        return None

    def get_all(self) -> dict:
        # The known groups, plus any other group this site has saved (MAP, written
        # by the Map tab's "Update map"; a sim's own groups …) — those used to be
        # stored and served by name but missing from the Global Configs list.
        result = {}
        for name in [*KNOWN_CONFIGS, *(k for k in self._overrides if k not in KNOWN_CONFIGS)]:
            val = self.get(name)
            if val is not None:
                result[name] = val
        return result

    def update(self, name: str, new_value: dict) -> str:
        # Overrides are the only place an edit is recorded. `get` consults them
        # first, so a running skill sees the new value on its next read without
        # a restart — there is nothing else to poke.
        #
        # This deliberately does NOT also deep-merge into the robot's `tasks`
        # module. That used to be a harmless no-op back when robot packages kept
        # `tasks` empty, but a robot that declares real dicts there would have
        # them mutated in place, and `reload_from` only clears `_overrides` — so
        # the next site would silently inherit this site's edits for every group
        # it does not override itself.
        if name == 'ENV':
            # Refuse a location name / alias that would point at two places.
            from ..env_names import validate_env
            error = validate_env(new_value)
            if error:
                return error
        with self._lock:
            self.refresh(force=True)
            self._overrides[name] = new_value
            self._save()
        return ''

    def _save(self):
        with self._lock:
            try:
                merged = self._store.save(self._overrides)
                if isinstance(merged, dict):
                    self._overrides = merged
            except Exception as e:
                print(f'[ConfigManager] Could not save: {e}')

    def refresh(self, force: bool = False) -> bool:
        """Reload the site's overrides when another machine saved them since
        (at most one stat per 2 s; skipped while this process has unsaved
        edits). True when reloaded."""
        if not self._store.changed(force):
            return False
        with self._lock:
            if self._overrides != (self._store.base or {}):
                return False
            data = self._store.read()
            if not isinstance(data, dict):
                return False
            self._overrides = data
            print(f'[ConfigManager] {self._persist_file.name} changed on disk: reloaded')
            return True

    def load_saved(self):
        # The overrides only live here — `get` consults them first. They are no
        # longer also deep-merged into the robot's `tasks` module: that was the
        # fallback `get` uses for groups a site does not override, so after a
        # site switch the next site inherited the previous one's values there.
        with self._lock:
            data = self._store.read()
            if not isinstance(data, dict):
                if data is not None:
                    print('[ConfigManager] Could not load overrides: not an object')
                return
            self._overrides.update(data)
        print(f'[ConfigManager] Applied {len(data)} config overrides')
