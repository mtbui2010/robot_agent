"""The commands of each policy family: what to run for norm stats, a training (fresh or resumed) and where its checkpoints land, and the test of a smoke run.
Everything is a pure function of the run file and the paths, so it is testable without a GPU or the frameworks."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .paths import Paths
from .runfile import Run

_BAD_LOG = re.compile(r"RESOURCE_EXHAUSTED|out of memory|OutOfMemory|Traceback|\bnan\b|\binf\b", re.I)


def smoke_name(run: Run) -> str:
    return f"{run.name}_smoke"


def ckpt_root(run: Run, paths: Paths, smoke: bool = False) -> Path:
    """The folder whose subfolders are the saved steps (what a validation job is pointed at)."""
    base = paths.run_dir(run.name) / ("smoke_checkpoints" if smoke else "checkpoints")
    if run.family == "openpi":
        return base / run.openpi["config"] / (smoke_name(run) if smoke else run.name)
    return base / "checkpoints"                                  # lerobot: <output_dir>/checkpoints/<step>


def _openpi_script(run: Run, script: str, *rest: str) -> list[str]:
    return [sys.executable, "-m", "vla_train.openpi_runner", run.openpi.get("code_module") or "-", script, *rest]


def norm_stats_cmd(run: Run) -> list[str] | None:
    """The norm-stats command of a family that needs one (openpi), else None."""
    if run.family != "openpi":
        return None
    return _openpi_script(run, "compute_norm_stats.py", "--config-name", run.openpi["config"])


def train_cmd(run: Run, paths: Paths, smoke: bool = False, resume: bool = False) -> list[str]:
    steps = run.smoke_steps if smoke else run.steps
    if run.family == "openpi":
        base = paths.run_dir(run.name) / ("smoke_checkpoints" if smoke else "checkpoints")
        flag = "--resume" if resume else ("--overwrite" if smoke else None)
        return _openpi_script(run, "train.py", run.openpi["config"], f"--exp-name={smoke_name(run) if smoke else run.name}",
                              f"--batch-size={run.batch_size}", f"--num-train-steps={steps}", f"--checkpoint-base-dir={base}", *([flag] if flag else []))
    out = paths.run_dir(run.name) / ("smoke_checkpoints" if smoke else "checkpoints")
    if resume:
        return ["lerobot-train", f"--config_path={out}/checkpoints/last/pretrained_model/train_config.json", "--resume=true"]
    return ["lerobot-train", *run.lerobot["args"], f"--steps={steps}", f"--batch_size={run.batch_size}", f"--output_dir={out}", f"--job_name={run.name}"]


def has_checkpoint(run: Run, paths: Paths, smoke: bool = False) -> bool:
    root = ckpt_root(run, paths, smoke)
    return root.is_dir() and any(p.is_dir() and p.name.isdigit() for p in root.iterdir())      # step folders are numbers; a folder being written has a suffix


def smoke_problem(run: Run, paths: Paths, rc: int, log_text: str) -> str:
    """'' when the smoke run was good, else why not: it ended badly, the log shows a nan / out-of-memory / traceback, or it wrote no checkpoint."""
    if rc != 0:
        return f"the test run ended with exit code {rc}"
    bad = _BAD_LOG.search(log_text)
    if bad:
        return f"the test run's log has '{bad.group(0)}'"
    if not has_checkpoint(run, paths, smoke=True):
        return f"the test run wrote no checkpoint in {ckpt_root(run, paths, True)}"
    return ""
