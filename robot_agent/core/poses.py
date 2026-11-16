"""Poses the dashboard's "Go to" list runs: each is a label and a PLAN of one or more steps, written like a shortcut button's plan (``movej::approach_drawer``,
``lift::0.5&&movej::dr6=-90`` ...), stored in ``<common>/poses.json`` shared by every machine (merged on save, like the buttons). The operator adds, edits and deletes them;
none is built in. A robot package can SEED the first list from a config group (``POSES['seed']``: the named joint poses of its arm config), once, when the file does not exist;
``restore_defaults`` adds the missing seeds back.

One speed (0..1) applies to every pose: each step of a speed-capable skill (``POSES['speed_skills']``, e.g. movej / movel / movet) gets ``speed=<s>`` unless the step sets its own.
The reason: a pose is often recorded as training data; a fast move (several rad/s) next to slow teleop does not look like one demonstration.

``POSES`` config: ``{seed: {from, skill, param, prefix?, keys?, names?}, speed_skills: [...], default_speed: 0.25}``; none: no seed, no speed rewriting, speed 1.
"""
from __future__ import annotations

import re
import threading
import uuid
from types import SimpleNamespace

from .shared_json import SharedJson, merge_list

SPEED_MIN = 0.05


def _is_pose(value) -> bool:
    """A named pose in a robot config: {mode: [joint angles]} (or a list of such lists): at least one numeric list."""
    def nums(v):
        return isinstance(v, (list, tuple)) and bool(v) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
    return isinstance(value, dict) and any(nums(v) or (isinstance(v, (list, tuple)) and v and all(nums(x) for x in v)) for v in value.values())


def seed_poses(spec, group) -> list:
    """[{label, plan}] read from the robot's own config group (no pose is named here): spec {skill, param?, prefix?, keys?, names?}."""
    if not isinstance(spec, dict) or not isinstance(group, dict):
        return []
    skill = str(spec.get('skill') or '').strip()
    if not skill:
        return []
    prefix, keys, names = str(spec.get('prefix') or ''), spec.get('keys'), spec.get('names')
    out = []
    for key, value in group.items():
        if not isinstance(key, str) or not key.startswith(prefix) or not _is_pose(value):
            continue
        if isinstance(keys, (list, tuple)) and not set(value) <= set(keys):
            continue
        if isinstance(names, (list, tuple)) and key not in names:
            continue
        out.append({'label': key.replace('_', ' '), 'plan': f'{skill}::{key}'})
    return out


_HAS_SPEED = re.compile(r'(^|[,\s])speed\s*=')


def apply_speed(plan: str, speed: float, skills) -> str:
    """The plan with ``speed=<speed>`` added to every step of a skill in ``skills`` that does not set its own (``movej::fold`` -> ``movej::inputs="fold", speed=0.25``)."""
    if not skills or speed is None or speed >= 1.0:
        return plan
    skills = set(skills)
    lines = []
    for raw in plan.replace('\\n', '\n').split('\n'):
        code, hash_, comment = raw.partition('#')
        parts = []
        for part in code.split('&&'):
            bits = part.split('::', 1)
            if len(bits) == 2 and bits[0].strip().lstrip('!~') in skills and bits[1].strip() and not _HAS_SPEED.search(bits[1]):
                args = bits[1].strip()
                args = (f'{args}, speed={speed:g}' if '=' in args else f'inputs="{args.replace(chr(34), chr(92) + chr(34))}", speed={speed:g}')
                part = f'{bits[0]}::{args}' + part[len(part.rstrip()):]          # keep the spaces before a comment
            parts.append(part)
        lines.append('&&'.join(parts) + (hash_ + comment if hash_ else ''))
    return '\n'.join(lines)


def _merge(base, mine, theirs):
    b, m, t = (d if isinstance(d, dict) else {} for d in (base, mine, theirs))
    poses = merge_list(lambda it: it.get('id'))(b.get('poses'), m.get('poses'), t.get('poses'))
    speed = m.get('speed') if m.get('speed') != b.get('speed') else t.get('speed', m.get('speed'))
    return {'speed': speed, 'poses': poses, 'seeded': bool(m.get('seeded') or t.get('seeded'))}


class PoseManager:
    def __init__(self, data_dir, get_cfg=lambda: None, get_group=lambda name: None):
        """``get_cfg()`` -> the POSES config group (or None); ``get_group(name)`` -> a config group to read the seed poses from."""
        from pathlib import Path
        self._get_cfg, self._get_group = get_cfg, get_group
        self._lock = threading.RLock()
        self._store = SharedJson(Path(data_dir) / 'poses.json', merge=_merge)
        self._doc: dict = {'speed': None, 'poses': [], 'seeded': False}
        self._loaded = False

    # ── config ────────────────────────────────────────────────────────────────────
    def _cfg(self) -> dict:
        c = self._get_cfg()
        return c if isinstance(c, dict) else {}

    def default_speed(self) -> float:
        try:
            return max(SPEED_MIN, min(1.0, float(self._cfg().get('default_speed', 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def seeds(self) -> list:
        spec = self._cfg().get('seed')
        return seed_poses(spec, self._get_group(str((spec or {}).get('from') or '')))

    # ── state ─────────────────────────────────────────────────────────────────────
    def _load(self, force: bool = False) -> None:
        with self._lock:
            if self._loaded and not self._store.changed(force):
                return
            doc = self._store.read()
            if isinstance(doc, dict):
                self._doc = {'speed': doc.get('speed'), 'poses': [p for p in doc.get('poses') or [] if isinstance(p, dict) and p.get('id') and p.get('label')],
                             'seeded': bool(doc.get('seeded'))}
            elif doc is None:
                self._doc = {'speed': None, 'poses': [], 'seeded': False}
            self._loaded = True
            if not self._doc['seeded']:                          # first use: the robot's own named poses become the starting list
                self._doc['poses'] += [{'id': uuid.uuid4().hex[:10], **p} for p in self.seeds()]
                self._doc['seeded'] = True
                self._save()

    def _save(self) -> None:
        try:
            doc = self._store.save(self._doc)
            if isinstance(doc, dict):
                self._doc = {'speed': doc.get('speed'), 'poses': list(doc.get('poses') or []), 'seeded': bool(doc.get('seeded'))}
        except Exception as e:
            print(f'[PoseManager] Could not save poses: {e}')

    # ── API ───────────────────────────────────────────────────────────────────────
    def speed(self) -> float:
        self._load()
        s = self._doc.get('speed')
        return float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else self.default_speed()

    def all(self) -> dict:
        self._load()
        have = {p['label'].lower() for p in self._doc['poses']}
        return {'speed': self.speed(), 'poses': [dict(p) for p in self._doc['poses']],
                'defaults_missing': sum(1 for s in self.seeds() if s['label'].lower() not in have)}

    def get(self, pid: str):
        self._load()
        return next((dict(p) for p in self._doc['poses'] if p['id'] == pid), None)

    @staticmethod
    def _clean(label, plan):
        label, plan = (label or '').strip(), (plan or '').strip()
        if not label:
            raise ValueError('label is required')
        if '::' not in plan:
            raise ValueError('the plan needs at least one step, written skill::argument (for example movej::fold)')
        return label, plan

    def _unique(self, label: str, except_id: str = '') -> None:
        if any(p['label'].lower() == label.lower() and p['id'] != except_id for p in self._doc['poses']):
            raise ValueError(f'a pose named "{label}" already exists')

    def add(self, label: str, plan: str) -> dict:
        label, plan = self._clean(label, plan)
        with self._lock:
            self._load(True)
            self._unique(label)
            p = {'id': uuid.uuid4().hex[:10], 'label': label, 'plan': plan}
            self._doc['poses'].append(p)
            self._save()
        return dict(p)

    def update(self, pid: str, label=None, plan=None):
        with self._lock:
            self._load(True)
            for p in self._doc['poses']:
                if p['id'] == pid:
                    lab, pl = self._clean(p['label'] if label is None else label, p['plan'] if plan is None else plan)
                    self._unique(lab, pid)
                    p['label'], p['plan'] = lab, pl
                    self._save()
                    return dict(p)
        return None

    def remove(self, pid: str) -> bool:
        with self._lock:
            self._load(True)
            n = len(self._doc['poses'])
            self._doc['poses'] = [p for p in self._doc['poses'] if p['id'] != pid]
            if len(self._doc['poses']) == n:
                return False
            self._save()
        return True

    def set_speed(self, speed: float) -> float:
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not SPEED_MIN <= speed <= 1.0:
            raise ValueError(f'speed must be between {SPEED_MIN} and 1')
        with self._lock:
            self._load(True)
            self._doc['speed'] = round(float(speed), 3)
            self._save()
        return self._doc['speed']

    def restore_defaults(self) -> int:
        """Add the seed poses that are not in the list (by label); returns how many."""
        with self._lock:
            self._load(True)
            have = {p['label'].lower() for p in self._doc['poses']}
            new = [{'id': uuid.uuid4().hex[:10], **s} for s in self.seeds() if s['label'].lower() not in have]
            self._doc['poses'] += new
            if new:
                self._save()
        return len(new)

    def plan_to_run(self, pid: str):
        """(label, the plan with the speed applied) or None."""
        p = self.get(pid)
        if p is None:
            return None
        return p['label'], apply_speed(p['plan'], self.speed(), self._cfg().get('speed_skills'))


def run_plan(registry, plan: str, node=None, name: str = 'pose') -> dict:
    """Run a plan text step by step (the plan-skill engine: cancel, parallel ``&&`` steps, stop at the first failure)."""
    from .plan_skill import run_plan_skill
    return run_plan_skill(registry, SimpleNamespace(name=name, plan=plan), {}, node=node)
