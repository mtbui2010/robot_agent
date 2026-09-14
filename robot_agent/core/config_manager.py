import copy, importlib, json
from pathlib import Path

KNOWN_CONFIGS = [
    'GRIP_CONFIGS', 'LIFT_CONFIGS', 'HEAD_CONFIGS', 'ARM_CONFIGS',
    'MOBILE_CONFIGS', 'FIND_CONFIGS', 'CALIB_PARAMS',
    'ENV', 'HOME_LOC', 'LLM_SERVERS', 'KR2EN', 'EN2KR',
]


def _deep_update(target: dict, source: dict):
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


class ConfigManager:
    def __init__(self, data_dir: Path, robot_pkg: str):
        self._data_dir = Path(data_dir)
        self._persist_file = self._data_dir / 'skill_configs_override.json'
        self._robot_pkg = robot_pkg
        self._overrides: dict = {}

    def set_data_dir(self, new_data_dir: Path):
        """Repoint persistence at a new directory, keeping current overrides
        (used by rename, where the file moves with the dir)."""
        self._data_dir = Path(new_data_dir)
        self._persist_file = self._data_dir / 'skill_configs_override.json'

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
        if name in self._overrides:
            return copy.deepcopy(self._overrides[name])
        tasks = self._tasks()
        if tasks is not None:
            val = getattr(tasks, name, None)
            if val is not None:
                return copy.deepcopy(val)
        return None

    def get_all(self) -> dict:
        result = {}
        for name in KNOWN_CONFIGS:
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
        self._overrides[name] = new_value
        self._save()
        return ''

    def _save(self):
        try:
            self._persist_file.parent.mkdir(parents=True, exist_ok=True)
            self._persist_file.write_text(json.dumps(self._overrides, indent=2))
        except Exception as e:
            print(f'[ConfigManager] Could not save: {e}')

    def load_saved(self):
        if not self._persist_file.exists():
            return
        try:
            data = json.loads(self._persist_file.read_text())
        except Exception as e:
            print(f'[ConfigManager] Could not load overrides: {e}')
            return
        for name, value in data.items():
            self._overrides[name] = value
            tasks = self._tasks()
            if tasks is not None:
                target = getattr(tasks, name, None)
                if target is not None and isinstance(target, dict):
                    _deep_update(target, value)
                elif target is not None:
                    setattr(tasks, name, value)
        print(f'[ConfigManager] Applied {len(data)} config overrides')
