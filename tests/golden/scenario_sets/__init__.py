"""Scenario sets: one file per step (``<step>_<topic>.py``, e.g. ``s01_kinds.py``), each with a ``SCENARIOS`` list of
``(scenario, tags, about)`` in the form of ``tests/golden/scenarios.py``.

Why: steps that run in parallel add goldens without editing a common file (docs/proposals/generalize_core_design.md,
"Migration steps"). ``all_scenarios()`` is the list ``tools/golden_record.py`` records from: the entries of
``scenarios.py`` first, in their order, then every set in file-name order. A cassette keeps its own copy of its scenario,
so editing a set never changes a golden. Switch off: delete the set file (its cassettes stay and keep replaying).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def set_files() -> list:
    return sorted(p for p in HERE.glob("s[0-9]*_*.py"))


def load_set(path) -> list:
    spec = importlib.util.spec_from_file_location(f"golden_set_{Path(path).stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(getattr(mod, "SCENARIOS", []))


def set_scenarios() -> list:
    """Every scenario of the set files, in file-name order, then in the order of each file."""
    out = []
    for p in set_files():
        out += load_set(p)
    return out


def all_scenarios() -> list:
    import scenarios
    return list(scenarios.SCENARIOS) + set_scenarios()
