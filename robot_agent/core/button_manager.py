"""Per-robot shortcut-button registry.

Each robot persists its own list of named "shortcut buttons" (label + multi-line
plan) in ``<data_dir>/buttons.json``. The UI's ButtonPanel reads/writes this
list through ``/buttons`` endpoints rather than localStorage so the buttons
travel with the robot across browsers, machines, and operators.

The order of buttons in the JSON file IS the display order; there is no
separate ``order`` field. ``reorder()`` accepts the new full list of ids.

Groups (2026-11-13): a button has an optional ``group`` (a group id; ``''`` = "Other", the implicit last group). The groups
themselves are an ordered list in ``<data_dir>/button_groups.json`` (``[{id, name}]``), so a group can exist with no button in
it. Deleting a group moves its buttons to "Other" (they are never deleted with it); a button whose group is unknown (another
machine deleted it) is listed under "Other". Both files are shared and merged like buttons.json.

buttons.json is shared by every machine using this configs folder: a save
merges this process's changes into the file (core/shared_json.py — atomic,
with a .bak), and reads pick up the other machines' saves.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .shared_json import SharedJson, merge_list


@dataclass
class BtnDef:
    id: str
    label: str
    plan: str
    group: str = ''              # a group id; '' = Other


@dataclass
class GroupDef:
    id: str
    name: str


class ButtonManager:
    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._persist_file = self._data_dir / 'buttons.json'
        self._buttons: list[BtnDef] = []
        self._lock = threading.RLock()
        self._store = SharedJson(self._persist_file, merge=merge_list(lambda it: it.get('id')))
        self._groups: list[GroupDef] = []
        self._gstore = SharedJson(self._data_dir / 'button_groups.json', merge=merge_list(lambda it: it.get('id')))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def _out(self, b: BtnDef) -> dict:
        """A button as the API shows it: a group that no longer exists reads as '' (Other)."""
        d = asdict(b)
        if d['group'] and d['group'] not in {g.id for g in self._groups}:
            d['group'] = ''
        return d

    def all(self) -> list[dict]:
        self.refresh()
        self.refresh_groups()
        with self._lock:
            return [self._out(b) for b in self._buttons]

    def get(self, btn_id: str) -> Optional[dict]:
        self.refresh()
        self.refresh_groups()
        with self._lock:
            for b in self._buttons:
                if b.id == btn_id:
                    return self._out(b)
        return None

    def _check_group(self, group: Optional[str]) -> str:
        group = (group or '').strip()
        if group and group not in {g.id for g in self._groups}:
            raise ValueError(f'unknown group "{group}"')
        return group

    def add(self, label: str, plan: str, group: str = '') -> dict:
        label = (label or '').strip()
        plan = plan or ''
        if not label:
            raise ValueError('label is required')
        with self._lock:
            self.refresh_groups(force=True)
            btn = BtnDef(id=uuid.uuid4().hex[:12], label=label, plan=plan, group=self._check_group(group))
            self.refresh(force=True)
            self._buttons.append(btn)
            self._save()
        return self._out(btn)

    def update(self, btn_id: str, label: Optional[str] = None,
               plan: Optional[str] = None, group: Optional[str] = None) -> Optional[dict]:
        """``group``: None = unchanged, '' = Other, else a group id."""
        with self._lock:
            self.refresh_groups(force=True)
            self.refresh(force=True)
            for b in self._buttons:
                if b.id == btn_id:
                    if label is not None:
                        label = label.strip()
                        if not label:
                            raise ValueError('label cannot be empty')
                        b.label = label
                    if plan is not None:
                        b.plan = plan
                    if group is not None:
                        b.group = self._check_group(group)
                    result = self._out(b)
                    break
            else:
                return None
            self._save()
        return result

    def remove(self, btn_id: str) -> bool:
        with self._lock:
            self.refresh(force=True)
            before = len(self._buttons)
            self._buttons = [b for b in self._buttons if b.id != btn_id]
            removed = len(self._buttons) < before
            if removed:
                self._save()
        return removed

    def reorder(self, ids: list[str]) -> bool:
        """Reorder buttons to match `ids`. Returns False if `ids` doesn't
        match the existing set exactly (no add/remove via reorder)."""
        with self._lock:
            self.refresh(force=True)
            existing = {b.id: b for b in self._buttons}
            if set(ids) != set(existing.keys()):
                return False
            self._buttons = [existing[i] for i in ids]
            self._save()
        return True

    def bulk_import(self, items: list[dict]) -> list[dict]:
        """Append a batch of {label, plan} items (used by the UI to migrate
        legacy localStorage buttons into the server). Returns the inserted
        BtnDefs. Items missing label/plan are skipped silently."""
        added: list[BtnDef] = []
        with self._lock:
            self.refresh(force=True)
            for it in items:
                label = (it.get('label') or '').strip()
                if not label:
                    continue
                plan = it.get('plan') or ''
                try:
                    group = self._check_group(it.get('group'))
                except ValueError:
                    group = ''
                btn = BtnDef(id=uuid.uuid4().hex[:12], label=label, plan=plan, group=group)
                self._buttons.append(btn)
                added.append(btn)
            if added:
                self._save()
        return [self._out(b) for b in added]

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------
    def groups(self) -> list[dict]:
        self.refresh_groups()
        with self._lock:
            return [asdict(g) for g in self._groups]

    def _name_free(self, name: str, except_id: str = '') -> str:
        name = (name or '').strip()
        if not name:
            raise ValueError('name is required')
        if name.lower() == 'other' or any(g.name.lower() == name.lower() and g.id != except_id for g in self._groups):
            raise ValueError(f'a group named "{name}" already exists')
        return name

    def add_group(self, name: str) -> dict:
        with self._lock:
            self.refresh_groups(force=True)
            g = GroupDef(id=uuid.uuid4().hex[:8], name=self._name_free(name))
            self._groups.append(g)
            self._save_groups()
        return asdict(g)

    def rename_group(self, gid: str, name: str) -> Optional[dict]:
        with self._lock:
            self.refresh_groups(force=True)
            for g in self._groups:
                if g.id == gid:
                    g.name = self._name_free(name, except_id=gid)
                    self._save_groups()
                    return asdict(g)
        return None

    def remove_group(self, gid: str) -> bool:
        """Delete a group; its buttons are kept and move to Other."""
        with self._lock:
            self.refresh_groups(force=True)
            before = len(self._groups)
            self._groups = [g for g in self._groups if g.id != gid]
            if len(self._groups) == before:
                return False
            self._save_groups()
            self.refresh(force=True)
            moved = False
            for b in self._buttons:
                if b.group == gid:
                    b.group, moved = '', True
            if moved:
                self._save()
        return True

    def reorder_groups(self, ids: list[str]) -> bool:
        with self._lock:
            self.refresh_groups(force=True)
            existing = {g.id: g for g in self._groups}
            if set(ids) != set(existing) or len(ids) != len(existing):
                return False
            self._groups = [existing[i] for i in ids]
            self._save_groups()
        return True

    @staticmethod
    def _gdefs(data) -> list[GroupDef]:
        out, seen = [], set()
        for it in data or []:
            if isinstance(it, dict) and it.get('id') and (it.get('name') or '').strip() and it['id'] not in seen:
                seen.add(it['id'])
                out.append(GroupDef(id=str(it['id']), name=it['name'].strip()))
        return out

    def _save_groups(self):
        with self._lock:
            try:
                self._groups = self._gdefs(self._gstore.save([asdict(g) for g in self._groups]))
            except Exception as e:
                print(f'[ButtonManager] Could not save button groups: {e}')

    def refresh_groups(self, force: bool = False) -> bool:
        if not self._gstore.changed(force):
            return False
        with self._lock:
            if [asdict(g) for g in self._groups] != [asdict(g) for g in self._gdefs(self._gstore.base)]:
                return False
            data = self._gstore.read()
            if not isinstance(data, list):
                return False
            self._groups = self._gdefs(data)
            return True

    # ------------------------------------------------------------------
    # Persistence (merged into the shared file, see core/shared_json.py)
    # ------------------------------------------------------------------
    @staticmethod
    def _defs(data) -> list[BtnDef]:
        import hashlib
        out = []
        for i, item in enumerate(data or []):
            if not isinstance(item, dict):
                continue
            label = (item.get('label') or '').strip()
            if not label:
                continue
            # A button without an id (hand-written file) gets one derived from
            # its place, so every machine reading the file gives it the same.
            bid = item.get('id') or hashlib.md5(f'{i}:{label}'.encode()).hexdigest()[:12]
            out.append(BtnDef(id=bid, label=label, plan=item.get('plan') or '', group=str(item.get('group') or '')))
        return out

    def _save(self):
        with self._lock:
            try:
                merged = self._store.save([asdict(b) for b in self._buttons])
                self._buttons = self._defs(merged)
            except Exception as e:
                print(f'[ButtonManager] Could not save buttons: {e}')

    def refresh(self, force: bool = False) -> bool:
        """Reload buttons.json when another machine saved it since; skipped
        while this process has unsaved edits. True when reloaded."""
        if not self._store.changed(force):
            return False
        with self._lock:
            if [asdict(b) for b in self._buttons] != [asdict(b) for b in self._defs(self._store.base)]:
                return False
            data = self._store.read()
            if not isinstance(data, list):
                return False
            self._buttons = self._defs(data)
            return True

    def load_saved(self) -> bool:
        with self._lock:
            gdata = self._gstore.read()
            if isinstance(gdata, list):
                self._groups = self._gdefs(gdata)
            data = self._store.read()
            if not isinstance(data, list):
                return False
            self._buttons = self._defs(data)
            return True
