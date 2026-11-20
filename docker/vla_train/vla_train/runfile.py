"""The run file: one YAML that says what to train. Strict like a vlaserve manifest: an unknown key or a wrong type is an error that names it.

    name: sd_v1                       # the run's name (letters, digits, _ -): its folder under /runs
    family: openpi                    # openpi | lerobot: must match the container it runs in
    steps: 5000
    batch_size: 16
    smoke_steps: 40                   # a short test first (0 = none); a failed test stops before the long run
    min_free_gpu_gb: 18               # refuse to start when the GPU has less free memory than this (something else is on it)
    openpi:                           # family openpi
      config: my_train_config         # the TrainConfig name
      code_module: /code/my_config.py # a file with register() that adds the config (optional; openpi itself is never edited)
      norm_stats: /runs/../assets/my_train_config/my_data/norm_stats.json   # computed first when this file is missing (optional)
    lerobot:                          # family lerobot
      args: [--dataset.repo_id=local/x, --dataset.root=/data/x, --policy.type=smolvla]   # passed to lerobot-train
    validate:                         # after a clean end, with {run_dir} {name} {ckpt_root} filled in (optional)
      command: [python, /code/val_loss.py, --out, "{run_dir}/val.log"]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FAMILIES = ("openpi", "lerobot")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,59}$")
_TOP = {"name", "family", "steps", "batch_size", "smoke_steps", "min_free_gpu_gb", "openpi", "lerobot", "validate"}
_SUB = {"openpi": {"config", "code_module", "norm_stats"}, "lerobot": {"args"}, "validate": {"command"}}


class RunFileError(ValueError):
    """The run file cannot be used; the message says why and where."""


@dataclass(frozen=True)
class Run:
    name: str
    family: str
    steps: int
    batch_size: int
    smoke_steps: int = 40
    min_free_gpu_gb: float = 18.0
    openpi: dict = field(default_factory=dict)
    lerobot: dict = field(default_factory=dict)
    validate: dict = field(default_factory=dict)


def _int(d: dict, key: str, lo: int, default=None) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise RunFileError(f"{key}: a whole number >= {lo}" + (f", got {v!r}" if key in d else " is required"))
    return v


def _section(d: dict, key: str) -> dict:
    sec = d.get(key) or {}
    if not isinstance(sec, dict):
        raise RunFileError(f"{key}: must be a mapping")
    extra = sorted(set(sec) - _SUB[key])
    if extra:
        raise RunFileError(f"{key}: unknown key(s) {extra} (known: {sorted(_SUB[key])})")
    return sec


def parse(text: str) -> Run:
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RunFileError(f"not valid YAML: {e}") from e
    if not isinstance(d, dict):
        raise RunFileError("the run file must be a mapping (name: ..., family: ...)")
    extra = sorted(set(d) - _TOP)
    if extra:
        raise RunFileError(f"unknown key(s) {extra} (known: {sorted(_TOP)})")
    name = str(d.get("name", ""))
    if not NAME_RE.match(name):
        raise RunFileError("name: letters, digits, _ and - only (1-60 characters, not starting with _ or -)")
    if d.get("family") not in FAMILIES:
        raise RunFileError(f"family: one of {list(FAMILIES)}, got {d.get('family')!r}")
    run = Run(name=name, family=d["family"], steps=_int(d, "steps", 1), batch_size=_int(d, "batch_size", 1),
              smoke_steps=_int(d, "smoke_steps", 0, 40), min_free_gpu_gb=float(d.get("min_free_gpu_gb", 18)),
              openpi=_section(d, "openpi"), lerobot=_section(d, "lerobot"), validate=_section(d, "validate"))
    return _check_family(run)


def _check_family(run: Run) -> Run:
    if run.family == "openpi" and not run.openpi.get("config"):
        raise RunFileError("openpi.config: the TrainConfig name is required")
    if run.family == "lerobot" and not run.lerobot.get("args"):
        raise RunFileError("lerobot.args: the lerobot-train arguments (dataset, policy ...) are required")
    cmd = run.validate.get("command")
    if cmd is not None and not (isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd)):
        raise RunFileError("validate.command: a list of strings")
    return run


def load(path: str | Path) -> Run:
    p = Path(path)
    if not p.is_file():
        raise RunFileError(f"no run file at {p}")
    return parse(p.read_text())
