"""Per-robot shortcut-button registry.

Each robot persists its own list of named "shortcut buttons" (label + multi-line
plan) in ``<data_dir>/buttons.json``. The UI's ButtonPanel reads/writes this
list through ``/buttons`` endpoints rather than localStorage so the buttons
travel with the robot across browsers, machines, and operators.

The order of buttons in the JSON file IS the display order; there is no
separate ``order`` field. ``reorder()`` accepts the new full list of ids.

Persistence is atomic (tmp + replace) with a backup, mirroring the pattern
used by DeviceManager._save.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


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
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def all(self) -> list[dict]:
        with self._lock:
            return [asdict(b) for b in self._buttons]

    def get(self, btn_id: str) -> Optional[dict]:
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
            self._buttons.append(btn)
        self._save()
        return asdict(btn)

    def update(self, btn_id: str, label: Optional[str] = None,
               plan: Optional[str] = None) -> Optional[dict]:
        with self._lock:
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
    # Persistence (atomic write + .bak fallback, mirrors DeviceManager)
    # ------------------------------------------------------------------
    def _save(self):
        with self._lock:
            data = [asdict(b) for b in self._buttons]
        text = json.dumps(data, indent=2)
        persist = self._persist_file
        tmp = persist.with_suffix('.json.tmp')
        bak = persist.with_suffix('.json.bak')
        try:
            persist.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text)
            if persist.exists():
                persist.replace(bak)
            tmp.replace(persist)
        except Exception as e:
            print(f'[ButtonManager] Could not save buttons: {e}')

    def load_saved(self) -> bool:
        persist = self._persist_file
        if not persist.exists():
            return False
        try:
            data = json.loads(persist.read_text())
        except Exception as e:
            print(f'[ButtonManager] Could not load {persist.name}: {e}')
            bak = persist.with_suffix('.json.bak')
            if bak.exists():
                try:
                    data = json.loads(bak.read_text())
                    print('[ButtonManager] Recovered from backup')
                except Exception:
                    return False
            else:
                return False
        if not isinstance(data, list):
            return False
        with self._lock:
            self._buttons = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                btn_id = item.get('id') or uuid.uuid4().hex[:12]
                label = (item.get('label') or '').strip()
                if not label:
                    continue
                self._buttons.append(BtnDef(
                    id=btn_id,
                    label=label,
                    plan=item.get('plan') or '',
                ))
        return True
