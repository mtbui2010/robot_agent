"""Per-robot shortcut-button registry.

Each robot persists its own list of named "shortcut buttons" (label + multi-line
plan) in ``<data_dir>/buttons.json``. The UI's ButtonPanel reads/writes this
list through ``/buttons`` endpoints rather than localStorage so the buttons
travel with the robot across browsers, machines, and operators.

The order of buttons in the JSON file IS the display order; there is no
separate ``order`` field. ``reorder()`` accepts the new full list of ids.

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


class ButtonManager:
    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._persist_file = self._data_dir / 'buttons.json'
        self._buttons: list[BtnDef] = []
        self._lock = threading.RLock()
        self._store = SharedJson(self._persist_file, merge=merge_list(lambda it: it.get('id')))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def all(self) -> list[dict]:
        self.refresh()
        with self._lock:
            return [asdict(b) for b in self._buttons]

    def get(self, btn_id: str) -> Optional[dict]:
        self.refresh()
        with self._lock:
            for b in self._buttons:
                if b.id == btn_id:
                    return asdict(b)
        return None

    def add(self, label: str, plan: str) -> dict:
        label = (label or '').strip()
        plan = plan or ''
        if not label:
            raise ValueError('label is required')
        btn = BtnDef(id=uuid.uuid4().hex[:12], label=label, plan=plan)
        with self._lock:
            self.refresh(force=True)
            self._buttons.append(btn)
            self._save()
        return asdict(btn)

    def update(self, btn_id: str, label: Optional[str] = None,
               plan: Optional[str] = None) -> Optional[dict]:
        with self._lock:
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
                    result = asdict(b)
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
                btn = BtnDef(id=uuid.uuid4().hex[:12], label=label, plan=plan)
                self._buttons.append(btn)
                added.append(btn)
            if added:
                self._save()
        return [asdict(b) for b in added]

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
            out.append(BtnDef(id=bid, label=label, plan=item.get('plan') or ''))
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
            data = self._store.read()
            if not isinstance(data, list):
                return False
            self._buttons = self._defs(data)
            return True
