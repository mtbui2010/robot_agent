"""vla-train: init / run / resume / status / stop. Run it inside a training container (docs/training_images.md).

    vla-train init [--weights URL_OR_REPO]     check the container (GPU, RAM cap, disk, mounts); fetch the base weights into /cache
    vla-train run RUN.yaml                     preflight, norm stats, smoke test, training, validation (use `docker exec -d` for a long run)
    vla-train resume NAME                      continue a run from its last checkpoint
    vla-train status [NAME]                    progress, loss, speed, alerts; no name = every run
    vla-train stop NAME                        stop a run (it can be resumed)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from . import orchestrate, preflight, state, status
from .paths import Paths
from .runfile import FAMILIES, RunFileError, load

DEFAULT_WEIGHTS = {"openpi": "gs://openpi-assets/checkpoints/pi05_base/params", "lerobot": ""}


def _family() -> str:
    f = os.environ.get("VLA_FAMILY", "")
    return f if f in FAMILIES else ""


def cmd_init(args, paths: Paths) -> int:
    bad = preflight.problems(paths, min_gpu_gb=args.min_gpu_gb)
    cap, gpu = preflight.memory_cap_gb(), preflight.gpu_free_gb()
    print(f"container family: {_family() or 'unknown'}; GPU free: {gpu if gpu is None else round(gpu, 1)} GB; RAM limit: {cap if cap is None else round(cap, 1)} GB; "
          f"free disk on {paths.runs}: {round(preflight.free_disk_gb(paths.runs), 1) if paths.runs.is_dir() else '?'} GB")
    for p in bad:
        print(f"PROBLEM: {p}")
    weights = args.weights if args.weights is not None else DEFAULT_WEIGHTS.get(_family(), "")
    if weights and not bad:
        print(f"fetching {weights} into {paths.cache} (first time only; large)")
        fetch_weights(_family(), weights)
    print("vla-train init: " + ("not ready" if bad else "ready"))
    return 1 if bad else 0


def fetch_weights(family: str, weights: str) -> None:
    if family == "openpi":
        from openpi.shared import download

        print("weights at", download.maybe_download(weights))
    else:
        from huggingface_hub import snapshot_download

        print("weights at", snapshot_download(weights))


def _load(arg: str, paths: Paths):
    """A run file path, or the name of an earlier run (its saved copy)."""
    p = Path(arg)
    return load(p if p.is_file() else paths.run_dir(arg) / "run.yaml")


def cmd_run(args, paths: Paths, resume: bool = False) -> int:
    run = _load(args.target, paths)
    if run.family != _family() and _family():
        print(f"vla-train: this is the {_family()} container but the run file says family: {run.family}")
        return 2
    run_dir = paths.run_dir(run.name)
    run_dir.mkdir(parents=True, exist_ok=True)
    if Path(args.target).is_file():
        shutil.copyfile(args.target, run_dir / "run.yaml")        # `resume` and `status` need no path afterwards
    return orchestrate.execute(run, paths, resume=resume)


def cmd_status(args, paths: Paths) -> int:
    names = [args.name] if args.name else sorted(p.parent.name for p in paths.runs.glob("*/state.json"))
    if not names:
        print("no runs yet")
    for name in names:
        print(describe(paths, name))
    return 0


def describe(paths: Paths, name: str) -> str:
    run_dir = paths.run_dir(name)
    st = state.read(run_dir)
    if not st:
        return f"{name}: no such run"
    log = run_dir / "train.log"
    text = log.read_text(errors="replace") if log.is_file() else ""
    s = status.summarize(text, int(st.get("steps", 0)), alive=state.alive(st.get("child_pid")), mtime=log.stat().st_mtime if log.is_file() else None)
    line = f"{name}: {st.get('stage', '?')} - {st.get('message', '')}"
    if s["step"]:
        line += f"; step {s['step']}/{s['total']}, loss {s['loss']}"
        line += f", {s['steps_per_min']} steps/min, about {s['eta_min']} min left" if s["steps_per_min"] else ""
    if st.get("stage") == "failed":
        line += f"  [failed in {st.get('failed_at')}]"
    return line + "".join(f"\n  ALERT {a}" for a in s["alerts"])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vla-train", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--weights", default=None, help="a gs:// path (openpi) or a Hugging Face repo id (lerobot); '' = none")
    i.add_argument("--min-gpu-gb", type=float, default=1.0)
    sub.add_parser("run").add_argument("target", help="the run file")
    sub.add_parser("resume").add_argument("target", help="a run name (or its run file)")
    sub.add_parser("status").add_argument("name", nargs="?")
    sub.add_parser("stop").add_argument("name")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = Paths.from_env()
    try:
        if args.cmd == "init":
            return cmd_init(args, paths)
        if args.cmd in ("run", "resume"):
            return cmd_run(args, paths, resume=args.cmd == "resume")
        if args.cmd == "status":
            return cmd_status(args, paths)
        return orchestrate.stop(paths, args.name)
    except RunFileError as e:
        print(f"vla-train: the run file cannot be used: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
