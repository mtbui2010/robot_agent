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
import threading
from pathlib import Path

from .shared_json import SharedJson, merge_map

# Guide modules to seed from on first run (best-effort; missing ones are skipped).
# The first one that imports cleanly becomes the initial active version — keep
# ``guide_struct`` first so the seeded default matches the legacy first-choice.
_SEED_MODULES = ['guide_struct', 'guide', 'guide_short', 'guide_struct_kr']


def _merge_guides(base, mine, theirs) -> dict:
    """guides.json is shared by every machine using this configs folder: merge
    the versions one by one, and `active` if this process changed it."""
    base, mine, theirs = base or {}, mine or {}, theirs or {}
    versions = merge_map(base.get('versions'), mine.get('versions'), theirs.get('versions'))
    active = mine.get('active') if mine.get('active') != base.get('active') else theirs.get('active')
    if active not in versions:
        active = next(iter(versions), None)
    return {'active': active, 'versions': versions}


class GuideManager:
    def __init__(self, data_dir, robot_pkg: str):
        self.data_dir = Path(data_dir)
        self.robot_pkg = robot_pkg
        self._file = self.data_dir / 'guides.json'
        # versions: {name: {'guide': str, 'format': dict | None}}
        self._data: dict = {'active': None, 'versions': {}}
        # Saves merge into the shared file; reads pick up other machines' saves.
        self._store = SharedJson(self._file, merge=_merge_guides, indent=1, ensure_ascii=False)
        self._lock = threading.RLock()
        self._load()

    # ── persistence ──────────────────────────────────────────────────────
    @staticmethod
    def _valid(d) -> bool:
        return isinstance(d, dict) and isinstance(d.get('versions'), dict)

    def _load(self) -> None:
        d = self._store.read()
        if self._valid(d):
            self._data = {'active': d.get('active'), 'versions': d['versions']}
            return
        if d is not None:
            print('[GuideManager] could not read guides.json; reseeding')
        self._seed()

    def _save(self) -> None:
        with self._lock:
            try:
                merged = self._store.save(self._data)
                if self._valid(merged):
                    self._data = {'active': merged.get('active'), 'versions': merged['versions']}
            except Exception as e:
                print(f'[GuideManager] could not persist guides: {e}')

    def refresh(self, force: bool = False) -> bool:
        """Reload guides.json when another machine saved it since (skipped
        while this process has unsaved edits). True when reloaded."""
        if not self._store.changed(force):
            return False
        with self._lock:
            base = self._store.base or {}
            if self._data != {'active': base.get('active'), 'versions': base.get('versions')}:
                return False
            d = self._store.read()
            if not self._valid(d):
                return False
            self._data = {'active': d.get('active'), 'versions': d['versions']}
            return True

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
        self.refresh()
        return {
            'active': self._data.get('active'),
            'versions': [{'name': n, **v} for n, v in self._data['versions'].items()],
        }

    def get(self, name: str):
        self.refresh()
        v = self._data['versions'].get(name)
        return {'name': name, **v} if v else None

    def upsert(self, name: str, guide: str, format=None) -> dict:
        name = (name or '').strip()
        if not name:
            raise ValueError('guide version name is required')
        with self._lock:
            self.refresh(force=True)
            self._data['versions'][name] = {'guide': guide or '', 'format': format or None}
            if self._data.get('active') is None:
                self._data['active'] = name
            self._save()
        return self.get(name)

    def rename(self, old: str, new: str) -> dict:
        new = (new or '').strip()
        if not new:
            raise ValueError('new name is required')
        with self._lock:
            self.refresh(force=True)
            return self._rename(old, new)

    def _rename(self, old: str, new: str) -> dict:
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
        with self._lock:
            self.refresh(force=True)
            self._data['versions'].pop(name, None)
            if self._data.get('active') == name:
                self._data['active'] = next(iter(self._data['versions']), None)
            self._save()

    def activate(self, name: str) -> str:
        with self._lock:
            self.refresh(force=True)
            if name not in self._data['versions']:
                raise ValueError(f'unknown guide version: {name}')
            self._data['active'] = name
            self._save()
        return name

    # ── planner resolution ───────────────────────────────────────────────
    def active_guide(self):
        """``(guide_text, format)`` of the active version, or ``None`` if there
        is no usable active version (caller then falls back to the modules)."""
        self.refresh()
        name = self._data.get('active')
        v = self._data['versions'].get(name) if name else None
        if v and isinstance(v.get('guide'), str) and v['guide'].strip():
            return v['guide'], v.get('format')
        return None


LOCATIONS_MARKER = 'LOCATIONS_HERE'


PLAN_SKILLS_MARKER = 'PLAN_SKILLS_HERE'


def _fill_plan_skills(guide: str) -> str:
    """Replace :data:`PLAN_SKILLS_MARKER` with the plan skills defined on the
    dashboard (name, parameters, description), so the planner can use them."""
    if PLAN_SKILLS_MARKER not in guide:
        return guide
    try:
        from ..state import current
        from .plan_skill import describe_plan_skills
        return guide.replace(PLAN_SKILLS_MARKER, describe_plan_skills(current().sr))
    except Exception:
        return guide


def _fill_locations(guide: str) -> str:
    """Replace :data:`LOCATIONS_MARKER` with the live ENV location list (with
    aliases), so a guide can name the places without copying them by hand.
    Guides without the marker are returned unchanged."""
    if LOCATIONS_MARKER not in guide:
        return guide
    try:
        from ..skill_configs import ENV
        from ..env_names import describe_env
        return guide.replace(LOCATIONS_MARKER, describe_env(ENV))
    except Exception:
        return guide


def resolve_guide(robot_pkg: str):
    """``(guide_text, format)`` to plan with: the active stored version if any,
    else the robot's ``guide_struct`` / ``guide`` module (legacy fallback).
    ``format`` is ``None`` for a freeform guide, a JSON schema dict otherwise.
    A ``LOCATIONS_HERE`` marker in the text is filled with the ENV locations,
    a ``PLAN_SKILLS_HERE`` marker with the plan skills.
    """
    guide, fmt = _resolve_guide(robot_pkg)
    return _fill_plan_skills(_fill_locations(guide)), fmt


def _resolve_guide(robot_pkg: str):
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


# ---------------------------------------------------------------------------
# Structured-guide answer → plan string
# ---------------------------------------------------------------------------

def _fix_len(alist: list, n: int) -> list:
    """Force *alist* to length *n*, padding with its last element."""
    nlen = len(alist)
    if nlen == 0:
        return ['None'] * n
    if nlen == n:
        return list(alist)
    if nlen > n:
        return list(alist[:n])
    return list(alist) + [alist[-1]] * (n - nlen)


def _fix_list(alist: list, target_n: int) -> list:
    """:func:`_fix_len` plus normalising empty / ``'None'`` entries to ``None``."""
    out = []
    for el in _fix_len(alist, target_n):
        el = str(el).strip()
        out.append(None if el in ('None', '') else el)
    return out


def reconstruct_plan(plan_dict: dict):
    """Turn a structured guide's JSON answer into the ``action::obj>>dest`` plan.

    A structured guide (``format`` is a JSON schema) makes the LLM answer with
    parallel lists::

        {"action_types": [...], "target_objects": [...], "destination_locations": [...]}

    ``target_objects`` sets the step count; the other two lists are padded or
    truncated to match. Returns *plan_dict* unchanged if it does not have that
    shape, so the caller can fall back to treating the answer as raw text.

    Absorbed from ``pyconnect.ros.node_taskmanager.recontruct_plan``. The
    padding branch there was dead (it concatenated a list with a string and
    always raised); it now pads as intended.
    """
    try:
        target_objs = [str(el).strip() for el in plan_dict['target_objects']]
        ntarget = len(target_objs)

        action_types = _fix_list(plan_dict['action_types'], ntarget)
        dest_locs = _fix_list(plan_dict['destination_locations'], ntarget)

        out = []
        for act, tob, dloc in zip(action_types, target_objs, dest_locs):
            tob = '' if tob is None else f'{tob}'
            dob = '' if dloc is None else f'>>{dloc}'
            out.append(f'{act}::{tob}{dob}')
        return '\n'.join(out).replace('None', '').replace('me@', '').replace('@unknown', '')

    except Exception as e:
        print(f'[guides] could not reconstruct structured plan: {e}')
        return plan_dict
