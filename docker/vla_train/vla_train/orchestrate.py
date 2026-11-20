"""The stages of a run, in order: preflight -> norm stats (openpi, when missing) -> short smoke test -> long training -> validation hook -> report.
Every stage writes state.json; a failure says which stage and why (the last log line), never an empty message. The subprocess runner is injected so
the order and the messages are tested with fake commands."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import families, preflight, report, state
from .paths import Paths
from .runfile import Run

Runner = Callable[..., int]


def subprocess_runner(cmd: list[str], log: Path, run_dir: Path, env: dict | None = None, cwd: str | None = None) -> int:
    """Run `cmd`, appending its output to `log` line by line with the epoch seconds in front (what `status` reads); records the child pid for `stop`."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", buffering=1) as f:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, **(env or {})}, cwd=cwd)
        state.write(run_dir, child_pid=p.pid)
        for line in p.stdout:
            f.write(f"{int(time.time())} {line.rstrip()}\n")
        return p.wait()


def _tail(log: Path, n: int = 1) -> str:
    try:
        return " | ".join(log.read_text().splitlines()[-n:])[-300:]
    except OSError:
        return "(no log)"


def _fail(run_dir: Path, stage: str, why: str, out=print) -> int:
    state.write(run_dir, stage="failed", failed_at=stage, message=why)
    out(f"vla-train: {stage} FAILED: {why}")
    return 1


def _env(run: Run, paths: Paths) -> dict:
    return {"VLA_RUNS": str(paths.runs), "VLA_DATA": str(paths.data), "VLA_CODE": str(paths.code), "VLA_RUN_NAME": run.name}


def _norm_stats(run: Run, paths: Paths, runner: Runner, run_dir: Path, out) -> str:
    """'' when the norm stats exist or were computed, else why not."""
    cmd, want = families.norm_stats_cmd(run), run.openpi.get("norm_stats")
    if cmd is None or not want or Path(want).is_file():
        return ""
    state.write(run_dir, stage="norm_stats", message="computing the normalisation statistics")
    out("vla-train: computing norm stats (the file does not exist yet)")
    rc = runner(cmd, run_dir / "norm_stats.log", run_dir, _env(run, paths))
    return "" if rc == 0 and Path(want).is_file() else f"norm stats were not written to {want} (exit code {rc}): {_tail(run_dir / 'norm_stats.log')}"


def _smoke(run: Run, paths: Paths, runner: Runner, run_dir: Path, out) -> str:
    state.write(run_dir, stage="smoke", message=f"{run.smoke_steps} steps")
    out(f"vla-train: smoke test, {run.smoke_steps} steps")
    log = run_dir / "smoke.log"
    rc = runner(families.train_cmd(run, paths, smoke=True), log, run_dir, _env(run, paths))
    return families.smoke_problem(run, paths, rc, log.read_text() if log.is_file() else "")


def _validate(run: Run, paths: Paths, runner: Runner, run_dir: Path, out) -> None:
    cmd = run.validate.get("command")
    if not cmd:
        return
    fill = {"run_dir": str(run_dir), "name": run.name, "ckpt_root": str(families.ckpt_root(run, paths))}
    state.write(run_dir, stage="validate", message="validating every checkpoint")
    out("vla-train: validation")
    rc = runner([c.format(**fill) for c in cmd], run_dir / "validate.log", run_dir, _env(run, paths), cwd=str(paths.code) if paths.code.is_dir() else None)
    val = run_dir / "val.log"
    if val.is_file():
        (run_dir / "validation.md").write_text(report.validation_md(f"Validation of {run.name}", val.read_text()))
    elif rc != 0:
        out(f"vla-train: the validation command ended with exit code {rc}: {_tail(run_dir / 'validate.log')}")


def execute(run: Run, paths: Paths, resume: bool = False, runner: Runner = subprocess_runner, check=preflight.problems, out=print) -> int:
    """The whole run. Returns 0 when it trained (and validated) cleanly."""
    run_dir = paths.run_dir(run.name)
    bad = check(paths, run.min_free_gpu_gb)
    if bad:
        return _fail(run_dir, "preflight", "; ".join(bad), out)
    if resume and not families.has_checkpoint(run, paths):
        return _fail(run_dir, "preflight", f"nothing to resume: no checkpoint in {families.ckpt_root(run, paths)}", out)
    if not resume and families.has_checkpoint(run, paths):
        return _fail(run_dir, "preflight", f"'{run.name}' already has checkpoints: use `vla-train resume {run.name}` or choose a new name", out)
    try:
        lock = state.lock(paths.runs, run.name)
    except state.Busy as e:
        return _fail(run_dir, "preflight", str(e), out)
    try:
        state.write(run_dir, name=run.name, family=run.family, steps=run.steps, pid=os.getpid(), started=round(time.time(), 1), resume=resume)
        return _stages(run, paths, resume, runner, run_dir, out)
    finally:
        state.unlock(lock)


def _stages(run: Run, paths: Paths, resume: bool, runner: Runner, run_dir: Path, out) -> int:
    why = _norm_stats(run, paths, runner, run_dir, out) if not resume else ""
    if why:
        return _fail(run_dir, "norm_stats", why, out)
    if not resume and run.smoke_steps > 0:
        why = _smoke(run, paths, runner, run_dir, out)
        if why:
            return _fail(run_dir, "smoke", why, out)
    state.write(run_dir, stage="train", message=f"{run.steps} steps" + (" (resumed)" if resume else ""))
    out(f"vla-train: training {run.name}, {run.steps} steps; log {run_dir / 'train.log'}")
    rc = runner(families.train_cmd(run, paths, resume=resume), run_dir / "train.log", run_dir, _env(run, paths))
    with (run_dir / "train.log").open("a") as f:
        f.write(f"{int(time.time())} EXIT rc={rc}\n")
    if rc != 0:
        return _fail(run_dir, "train", f"exit code {rc}: {_tail(run_dir / 'train.log', 2)} (resume with `vla-train resume {run.name}`)", out)
    _validate(run, paths, runner, run_dir, out)
    state.write(run_dir, stage="done", message="trained" + (" and validated" if (run_dir / "validation.md").is_file() else ""))
    out(f"vla-train: done ({run_dir})")
    return 0


def stop(paths: Paths, name: str, out=print) -> int:
    """Stop a run: SIGTERM to the training process (and the runner), SIGKILL if it stays."""
    run_dir = paths.run_dir(name)
    st = state.read(run_dir)
    pids = [p for p in (st.get("child_pid"), st.get("pid")) if state.alive(p) and p != os.getpid()]
    if not pids:
        out(f"vla-train: '{name}' has no running process (stage: {st.get('stage', 'unknown')})")
        return 1
    for sig in (15, 9):
        for p in pids:
            try:
                os.kill(p, sig)
            except OSError:
                pass
        time.sleep(5 if sig == 15 else 0)
    state.write(run_dir, stage="stopped", message="stopped by `vla-train stop` (resume with `vla-train resume`)")
    out(f"vla-train: stopped '{name}'")
    return 0
