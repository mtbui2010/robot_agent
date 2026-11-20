"""Read a run's log: progress, loss, speed, and the alerts a person should hear about (nan, out of memory, a traceback, a stall). Pure text in, summary out."""
from __future__ import annotations

import re
import time

_STEP = re.compile(r"(?:^|\s)[Ss]tep\D{0,3}(\d+(?:\.\d+)?[KMB]?)\b[^\n]*?\bloss\D{0,3}(-?[\d.]+(?:e[+-]?\d+)?|nan|inf)", re.I)
_TS = re.compile(r"^(\d{9,11})\s")
_ALERTS = (("OOM", re.compile(r"RESOURCE_EXHAUSTED|out of memory|OutOfMemory", re.I)), ("ERROR", re.compile(r"Traceback \(most recent")))
_UNITS = {"K": 1e3, "M": 1e6, "B": 1e9}


def _steps(tok: str) -> int:
    return int(float(tok[:-1]) * _UNITS[tok[-1].upper()]) if tok[-1].upper() in _UNITS else int(float(tok))


def parse(lines: list[str]) -> list[tuple[float | None, int, float]]:
    """[(epoch seconds or None, step, loss)] from the lines that report a step and a loss."""
    out = []
    for line in lines:
        m = _STEP.search(line)
        if not m:
            continue
        ts = _TS.match(line)
        try:
            out.append((float(ts.group(1)) if ts else None, _steps(m.group(1)), float(m.group(2))))
        except ValueError:
            out.append((float(ts.group(1)) if ts else None, _steps(m.group(1)), float("nan")))
    return out


def summarize(text: str, total_steps: int, alive: bool = True, mtime: float | None = None, now: float | None = None, stall_s: float = 900.0) -> dict:
    """{step, total, loss, steps_per_min, eta_min, alerts: [..], ended: 'ok'|'rc=N'|None}."""
    lines = text.splitlines()
    rows = parse(lines)
    s = {"step": rows[-1][1] if rows else 0, "total": total_steps, "loss": rows[-1][2] if rows else None, "steps_per_min": None, "eta_min": None, "alerts": [], "ended": None}
    timed = [r for r in rows if r[0] is not None]
    if len(timed) >= 2 and timed[-1][0] > timed[0][0] and timed[-1][1] > timed[0][1]:
        s["steps_per_min"] = round((timed[-1][1] - timed[0][1]) / (timed[-1][0] - timed[0][0]) * 60, 2)
        s["eta_min"] = round((total_steps - s["step"]) / s["steps_per_min"], 1) if s["steps_per_min"] else None
    if rows and rows[-1][2] != rows[-1][2] or any(r[2] in (float("inf"), float("-inf")) for r in rows[-3:]):
        s["alerts"].append("NAN: the loss is nan / inf")
    s["alerts"] += [f"{code}: see the log" for code, rx in _ALERTS if rx.search(text)]
    end = re.search(r"EXIT rc=(\d+)\s*$", text.strip())
    s["ended"] = None if not end else ("ok" if end.group(1) == "0" else f"rc={end.group(1)}")
    if alive and s["ended"] is None and mtime is not None and (now or time.time()) - mtime > stall_s:
        s["alerts"].append(f"STALL: no new log line for {int(((now or time.time()) - mtime) / 60)} min")
    return s
