"""The state of a run on disk (state.json), and the one-run-at-a-time lock. Plain files: `status` and `stop` work from another `docker exec`."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STAGES = ("preflight", "norm_stats", "smoke", "train", "validate", "done", "failed", "stopped")


def alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def write(run_dir: Path, **fields) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    cur = read(run_dir)
    cur.update(fields, updated=round(time.time(), 1))
    tmp = run_dir / "state.json.tmp"
    tmp.write_text(json.dumps(cur, indent=1))
    tmp.replace(run_dir / "state.json")
    return cur


def read(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / "state.json").read_text())
    except (OSError, ValueError):
        return {}


class Busy(RuntimeError):
    """Another run holds the lock."""


def lock(runs: Path, name: str) -> Path:
    """Take the lock of the runs folder for `name`; Busy (with who) when another live run has it. A lock of a dead process is taken over."""
    f = runs / ".vla_train.lock"
    runs.mkdir(parents=True, exist_ok=True)
    if f.exists():
        try:
            other = json.loads(f.read_text())
        except ValueError:
            other = {}
        if alive(other.get("pid")) and other.get("pid") != os.getpid():
            raise Busy(f"run '{other.get('name')}' is in progress (pid {other.get('pid')}): wait for it, or `vla-train stop {other.get('name')}`")
    f.write_text(json.dumps({"name": name, "pid": os.getpid()}))
    return f


def unlock(f: Path) -> None:
    try:
        if json.loads(f.read_text()).get("pid") == os.getpid():
            f.unlink()
    except (OSError, ValueError):
        pass
