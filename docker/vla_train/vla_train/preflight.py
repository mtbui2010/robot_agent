"""Checks before a run starts: the GPU is there and free, the disk has room, RAM has a cap, the mounts are writable. Each returns what is wrong, never raises."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .paths import Paths


def gpu_free_gb(run=subprocess.run) -> float | None:
    """Free memory of the first GPU in GB, None when nvidia-smi is not usable (no GPU or no driver in the container)."""
    try:
        out = run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
        return float(out.stdout.strip().splitlines()[0]) / 1024 if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def memory_cap_gb(cgroup: Path = Path("/sys/fs/cgroup/memory.max")) -> float | None:
    """The RAM limit of this container in GB (docker --memory), None when there is none."""
    try:
        v = cgroup.read_text().strip()
        return None if v == "max" else int(v) / 2**30
    except (OSError, ValueError):
        return None


def free_disk_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 2**30


def problems(paths: Paths, min_gpu_gb: float, min_disk_gb: float = 20.0, gpu=gpu_free_gb, cap=memory_cap_gb) -> list[str]:
    out = []
    g = gpu()
    if g is None:
        out.append("no GPU: run the container with --gpus all (and the NVIDIA container toolkit on the host)")
    elif g < min_gpu_gb:
        out.append(f"the GPU has only {g:.1f} GB free, {min_gpu_gb:g} GB needed: something else is using it (nvidia-smi on the host)")
    if cap() is None:
        out.append("the container has no RAM limit: create it with --memory and --memory-swap (a training once took 44 GB of host RAM)")
    for label, p in (("runs", paths.runs), ("cache", paths.cache)):
        if not p.is_dir() or not _writable(p):
            out.append(f"{label} folder {p} is missing or not writable: mount it (see docs/training_images.md)")
    if paths.runs.is_dir() and free_disk_gb(paths.runs) < min_disk_gb:
        out.append(f"less than {min_disk_gb:g} GB free on {paths.runs}")
    if not paths.data.is_dir():
        out.append(f"data folder {paths.data} is not mounted")
    return out


def _writable(p: Path) -> bool:
    try:
        t = p / ".vla_train_write_test"
        t.write_text("x")
        t.unlink()
        return True
    except OSError:
        return False
