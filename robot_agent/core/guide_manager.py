"""Versioned planner guides — data-backed, UI-editable.

The robot's planning *guide* (the LLM prompt that turns a natural-language task
into a plan) historically lived as Python modules (``<pkg>.configs.guide`` /
``guide_struct`` / …). That made it un-editable from the dashboard and offered
no version selection.

``GuideManager`` keeps the guides as **data** in ``common_dir/guides.json`` so an
operator can list / select / edit / add / delete versions live. Each version is::

    {"name": str, "guide": str, "format": dict | None}

``format`` mirrors the modules' ``FORMAT``: ``None`` → freeform
(``llm.chat_guide``), a JSON schema dict → structured (``llm.chat(..., format)``).

On first run (no ``guides.json``) the store is **seeded** from the robot's
existing guide modules so the operator starts with the as-shipped guides and the
planner's behavior is unchanged.

This module is robot-agnostic: the robot package is only referenced by name
(``importlib``), exactly like the rest of the planning layer.
"""

import importlib
import json
from pathlib import Path

# Guide modules to seed from on first run (best-effort; missing ones are skipped).
# The first one that imports cleanly becomes the initial active version — keep
# ``guide_struct`` first so the seeded default matches the legacy first-choice.
_SEED_MODULES = ['guide_struct', 'guide', 'guide_short', 'guide_struct_kr']


class GuideManager:
    def __init__(self, data_dir, robot_pkg: str):
        self.data_dir = Path(data_dir)
        self.robot_pkg = robot_pkg
        self._file = self.data_dir / 'guides.json'
        # versions: {name: {'guide': str, 'format': dict | None}}
        self._data: dict = {'active': None, 'versions': {}}
        self._load()

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self) -> None:
        if self._file.exists():
            try:
                d = json.loads(self._file.read_text())
                if isinstance(d, dict) and isinstance(d.get('versions'), dict):
                    self._data = {'active': d.get('active'), 'versions': d['versions']}
                    return
            except Exception as e:
                print(f'[GuideManager] could not read guides.json ({e}); reseeding')
        self._seed()

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._file.write_text(json.dumps(self._data, ensure_ascii=False, indent=1))
        except Exception as e:
            print(f'[GuideManager] could not persist guides: {e}')

    def _seed(self) -> None:
        versions: dict = {}
        active = None
        for name in _SEED_MODULES:
            try:
                m = importlib.import_module(f'{self.robot_pkg}.configs.{name}')
            except Exception:
                continue
            guide = getattr(m, 'GUIDE', None)
            if not isinstance(guide, str) or not guide.strip():
                continue
            fmt = getattr(m, 'FORMAT', None)
            if fmt is not None and not isinstance(fmt, (dict, list)):
                fmt = None  # only keep JSON-serializable formats
            versions[name] = {'guide': guide, 'format': fmt}
            if active is None:
                active = name
        self._data = {'active': active, 'versions': versions}
        self._save()

    # ── CRUD (used by api/guides.py) ─────────────────────────────────────
    def list(self) -> dict:
        return {
            'active': self._data.get('active'),
            'versions': [{'name': n, **v} for n, v in self._data['versions'].items()],
        }

    def get(self, name: str):
        v = self._data['versions'].get(name)
        return {'name': name, **v} if v else None

    def upsert(self, name: str, guide: str, format=None) -> dict:
        name = (name or '').strip()
        if not name:
            raise ValueError('guide version name is required')
        self._data['versions'][name] = {'guide': guide or '', 'format': format or None}
        if self._data.get('active') is None:
            self._data['active'] = name
        self._save()
        return self.get(name)

    def rename(self, old: str, new: str) -> dict:
        new = (new or '').strip()
        if not new:
            raise ValueError('new name is required')
        if old not in self._data['versions']:
            raise ValueError(f'unknown guide version: {old}')
        if new != old and new in self._data['versions']:
            raise ValueError(f'guide version {new!r} already exists')
        self._data['versions'][new] = self._data['versions'].pop(old)
        if self._data.get('active') == old:
            self._data['active'] = new
        self._save()
        return self.get(new)

    def delete(self, name: str) -> None:
        self._data['versions'].pop(name, None)
        if self._data.get('active') == name:
            self._data['active'] = next(iter(self._data['versions']), None)
        self._save()

    def activate(self, name: str) -> str:
        if name not in self._data['versions']:
            raise ValueError(f'unknown guide version: {name}')
        self._data['active'] = name
        self._save()
        return name

    # ── planner resolution ───────────────────────────────────────────────
    def active_guide(self):
        """``(guide_text, format)`` of the active version, or ``None`` if there
        is no usable active version (caller then falls back to the modules)."""
        name = self._data.get('active')
        v = self._data['versions'].get(name) if name else None
        if v and isinstance(v.get('guide'), str) and v['guide'].strip():
            return v['guide'], v.get('format')
        return None


def resolve_guide(robot_pkg: str):
    """``(guide_text, format)`` to plan with: the active stored version if any,
    else the robot's ``guide_struct`` / ``guide`` module (legacy fallback).
    ``format`` is ``None`` for a freeform guide, a JSON schema dict otherwise.
    """
    try:
        from ..state import current
        ag = current().guides.active_guide()
        if ag is not None:
            return ag
    except Exception:
        pass
    for name in ('guide_struct', 'guide'):
        try:
            m = importlib.import_module(f'{robot_pkg}.configs.{name}')
            g = getattr(m, 'GUIDE', None)
            if isinstance(g, str) and g.strip():
                return g, getattr(m, 'FORMAT', None)
        except Exception:
            continue
    return '', None
