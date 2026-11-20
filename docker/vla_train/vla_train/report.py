"""The validation report of a run: the table of validation losses per step and the step with the lowest one."""
from __future__ import annotations

import re


def rows(val_log: str) -> list[dict]:
    return [dict(re.findall(r"(\w+)=([\d.eE+-]+|nan)", line)) for line in val_log.splitlines() if line.startswith("step=")]


def validation_md(title: str, val_log: str) -> str:
    rs = [r for r in rows(val_log) if "val_loss" in r]
    if not rs:
        return f"# {title}\n\nNo validation numbers were produced.\n"
    best = min(rs, key=lambda r: float(r["val_loss"]))
    out = [f"# {title}", "", "| step | val loss | train loss (same frames) |", "|---|---|---|"]
    out += [f"| {r['step']} | {float(r['val_loss']):.5f} | {float(r.get('train_eval', 'nan')):.5f} |" for r in rs]
    out += ["", f"Lowest validation loss: step {best['step']} ({float(best['val_loss']):.5f}). A lower loss is not a better policy: serve the candidate steps and run the offline "
            "check on the held-out episodes, then the robot, before choosing one.", ""]
    return "\n".join(out)
