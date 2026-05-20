import importlib, json, logging, requests, traceback
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Literal

from ..logging_config import debug_response_enabled

logger = logging.getLogger(__name__)


@dataclass
class SkillDef:
    name: str
    type: Literal['internal', 'external']
    description: str = ''
    module_path: str = ''
    func_name: str = ''
    url: str = ''
    timeout: float = 30.0
    method: str = 'POST'
    headers: dict = field(default_factory=dict)


class SkillRegistry:
    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._persist_file = self._data_dir / 'skills.json'
        self._skills: dict[str, SkillDef] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_internal(self, name: str, module_path: str,
                          func_name: str = '', description: str = ''):
        self._skills[name] = SkillDef(
            name=name, type='internal',
            module_path=module_path,
            func_name=func_name or name,
            description=description,
        )

    def register_external(self, name: str, url: str, description: str = '',
                          timeout: float = 30.0, method: str = 'POST',
                          headers: dict | None = None):
        self._skills[name] = SkillDef(
            name=name, type='external',
            url=url, description=description,
            timeout=timeout, method=method,
            headers=headers or {},
        )

    def remove(self, name: str):
        self._skills.pop(name, None)
        self._save()

    def update(self, name: str, **kwargs) -> bool:
        skill = self._skills.get(name)
        if skill is None:
            return False
        for k, v in kwargs.items():
            if hasattr(skill, k) and v is not None:
                setattr(skill, k, v)
        self._save()
        return True

    def all(self) -> list[dict]:
        return [
            {
                'name': s.name,
                'type': s.type,
                'description': s.description,
                'module_path': s.module_path,
                'func_name': s.func_name,
                'url': s.url,
                'timeout': s.timeout,
                'method': s.method,
                'headers': s.headers,
            }
            for s in self._skills.values()
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute(self, name: str, params: dict, node: Any = None,
                log_fn=None) -> dict:
        skill = self._skills.get(name)
        if skill is None:
            return {'isdone': False, 'msg': f'Skill "{name}" not registered'}

        if skill.type == 'internal':
            from ..skills import _set_emitter, _clear_emitter
            try:
                mod = importlib.import_module(skill.module_path)
                func = getattr(mod, skill.func_name)
                if log_fn is not None:
                    _set_emitter(log_fn)
                try:
                    return func(node=node, **params)
                finally:
                    if log_fn is not None:
                        _clear_emitter()
            except Exception as e:
                tb = traceback.format_exc()
                logger.error(
                    f"Internal skill '{name}' ({skill.module_path}:{skill.func_name}) "
                    f"failed: {e}\n{tb}"
                )
                result = {'isdone': False, 'msg': str(e)}
                if debug_response_enabled():
                    result.update({
                        'skill': name,
                        'module_path': skill.module_path,
                        'func_name': skill.func_name,
                        'traceback': tb,
                    })
                return result
        else:
            try:
                method = (skill.method or 'POST').upper()
                resp = requests.request(
                    method=method,
                    url=skill.url,
                    json=params if method in ('POST', 'PUT', 'PATCH') else None,
                    params=params if method == 'GET' else None,
                    headers=skill.headers or {},
                    timeout=skill.timeout or 30.0,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                tb = traceback.format_exc()
                logger.error(
                    f"External skill '{name}' ({skill.method} {skill.url}) "
                    f"failed: {e}\n{tb}"
                )
                result = {'isdone': False, 'msg': str(e)}
                if debug_response_enabled():
                    result.update({
                        'skill': name,
                        'url': skill.url,
                        'method': skill.method,
                        'timeout': skill.timeout,
                        'traceback': tb,
                    })
                return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save(self):
        try:
            data = [asdict(s) for s in self._skills.values()]
            self._persist_file.parent.mkdir(parents=True, exist_ok=True)
            self._persist_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f'[SkillRegistry] Could not save skills: {e}')

    def load_saved(self) -> bool:
        """Load from skills.json. Returns True if file existed and was loaded."""
        if not self._persist_file.exists():
            return False
        try:
            data = json.loads(self._persist_file.read_text())
        except Exception as e:
            print(f'[SkillRegistry] Could not load skills.json: {e}')
            return False
        for item in data:
            self._skills[item['name']] = SkillDef(**item)
        print(f'[SkillRegistry] Loaded {len(data)} skills from skills.json')
        return True

    # ------------------------------------------------------------------
    # Bulk load from config dict: {skill_name: (module_path, func_name)}
    # ------------------------------------------------------------------
    def load_from_skill_configs(self, skill_configs: dict):
        for skill_name, entry in skill_configs.items():
            if isinstance(entry, tuple):
                module_path, func_name = entry
            else:
                module_path, func_name = entry, skill_name
            self.register_internal(
                name=skill_name,
                module_path=module_path,
                func_name=func_name,
                description=f'from {module_path}:{func_name}',
            )
