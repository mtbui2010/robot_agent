"""Roadmap step 7: the planning loops are split into phases. No function in
robot_agent/core/planning/*.py is longer than 80 lines (nested functions count
in their parent too). A function that must stay longer goes in LEGACY with the
reason; there is none today."""

import ast
from pathlib import Path

import pytest

PLANNING = Path(__file__).resolve().parents[1] / "robot_agent" / "core" / "planning"
MAX_LINES = 80
LEGACY: dict = {}          # "file.py:func": "why it stays long"


def _functions():
    for src in sorted(PLANNING.glob("*.py")):
        for n in ast.walk(ast.parse(src.read_text())):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield f"{src.name}:{n.name}", n.end_lineno - n.lineno + 1


def test_no_function_over_80_lines():
    long = {name: n for name, n in _functions() if n > MAX_LINES and name not in LEGACY}
    assert not long, f"split these into phases / helpers (> {MAX_LINES} lines): {long}"


@pytest.mark.parametrize("name", ["assist_loop.py:run_blocking", "loop.py:run_blocking", "assist_loop.py:_check"])
def test_the_former_giants_are_short(name):
    sizes = dict(_functions())
    assert sizes[name] <= 40, (name, sizes[name])
