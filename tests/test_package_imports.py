"""`import robot_agent` and the planning modules stay light; create_app stays importable."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _loaded_after(stmt: str) -> set:
    code = f"import sys; {stmt}; print(' '.join(sorted(m.split('.')[0] for m in sys.modules)))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True,
                         env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
    return set(out.stdout.split())


@pytest.mark.parametrize("stmt", [
    "import robot_agent",
    "import robot_agent.core.planning",
    "import robot_agent.core.planning.assist_loop",
    "import robot_agent.safe_parse",
])
def test_no_web_stack_or_numpy_on_import(stmt):
    heavy = _loaded_after(stmt) & {"fastapi", "starlette", "uvicorn", "pydantic", "numpy", "requests", "embodiplan",
                                   "rclpy"}
    assert heavy == set()


def test_create_app_still_importable():
    pytest.importorskip("fastapi")
    from robot_agent import create_app
    import robot_agent
    assert callable(create_app) and robot_agent.create_app is create_app
    with pytest.raises(AttributeError):
        robot_agent.no_such_name  # noqa: B018
