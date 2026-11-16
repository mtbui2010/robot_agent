"""Option ``trace`` (default off): one JSONL file per run, ``<dir>/<run_id>/trace.jsonl``, to read why a run failed in one place.

Why: the causes of failed runs were spread over several logs joined by hand on timestamps, and some were in none (the time of each
LLM call by purpose, the recovery rule that fired). How: a listener on what the engine already makes (events, lab entries, LLM calls
through ``observe.llm``, the failure before the recovery policy); no extra model or robot call, nothing it returns changes the run.
Schema 1 (docs/trace.md): ``header``, ``event``, ``lab``, ``llm``, ``recovery``; arrays become shapes, long strings lengths.
Fail open: a write error gives one warning, then the trace is off for that run.
Switch off: key ``enabled`` (run option ``trace``, ``$ROBOT_AGENT_TRACE``; default off). ``dir``: the folder (default: the run log's).
Hooks on_start, on_event, on_lab, on_step_failed. Lab flag ``trace``: ``{path}`` once per run (it adds no event).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from enum import Enum
from pathlib import Path

from robot_agent.core.options import Key, Option, OptionSpec, register

SCHEMA = 1
MAX_STR, MAX_LIST, MAX_DEPTH, SMALL_ARRAY = 4096, 64, 8, 16
CHOICE = {"recover": "recover", "replan": "replan", "clarify": "ask", "search": "search", "done": "stop", "step_start": "next_step"}
log = logging.getLogger(__name__)


def summarise(v, depth: int = 0):
    """JSON-safe copy of ``v``: numbers, bools, None and short strings kept; a long string (an encoded image) becomes ``{_len}``,
    bytes ``{_bytes}``, an array ``{_shape, _dtype}`` (its values when it has at most SMALL_ARRAY), a long list ``{_list, head}``."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, str):
        return v if len(v) <= MAX_STR else {"_len": len(v)}
    if isinstance(v, (bytes, bytearray)):
        return {"_bytes": len(v)}
    if hasattr(v, "shape") and hasattr(v, "dtype"):
        n = getattr(v, "size", SMALL_ARRAY + 1)
        return v.tolist() if n <= SMALL_ARRAY else {"_shape": list(v.shape), "_dtype": str(v.dtype)}
    if depth >= MAX_DEPTH:
        return {"_depth": type(v).__name__}
    if isinstance(v, dict):
        return {str(k): summarise(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        items = list(v)
        out = [summarise(x, depth + 1) for x in items[:MAX_LIST]]
        return out if len(items) <= MAX_LIST else {"_list": len(items), "head": out[:8]}
    return summarise(getattr(v, "__dict__", None), depth + 1) if hasattr(v, "__dict__") else repr(v)[:200]


def text_chars(v) -> int:
    """The characters of text in ``v`` (a prompt, messages, a reply): the strings at any depth, images (key ``images``) left out."""
    if isinstance(v, str):
        return len(v)
    if isinstance(v, dict):
        return sum(text_chars(x) for k, x in v.items() if k != "images")
    if isinstance(v, (list, tuple)):
        return sum(text_chars(x) for x in v)
    return 0


def event_record(ev: dict, step) -> dict:
    """An ``event`` record: the event's name and its fields summarised (the belief ``world`` only on ``done``, no audio)."""
    name = ev.get("event")
    data = {k: v for k, v in ev.items() if k not in ("event", "say_audio") and (k != "world" or name == "done")}
    return {"k": "event", "step": step, "ev": name, "data": summarise(data)}


def llm_record(call: dict, t0: float, step, phase) -> dict:
    """An ``llm`` record from an ``observe.llm`` call: purpose (the budget label, "other" when none), start, duration, sizes."""
    start = float(call.get("t0") or time.time())
    return {"k": "llm", "t": round(start - t0, 3), "ts": round(start, 3), "step": step, "purpose": call.get("purpose") or "other",
            "source": call.get("source"), "model": summarise(call.get("model")), "ms": call.get("latency_ms"),
            "prompt_chars": text_chars(call.get("request")), "reply_chars": text_chars(call.get("response")),
            "error": call.get("error"), "phase": phase}


def failure_record(run, st: dict, code, reason, step) -> dict:
    """The failure the recovery policy is about to answer: FailureCode, the robot policy's rule for it, the reason."""
    try:
        rule = summarise(run.robot.policy.rule_for(code))
    except Exception:                                   # noqa: BLE001 - no policy on a stub robot: no rule
        rule = None
    return {"k": "recovery", "step": step, "action": st.get("action"), "object": st.get("object"),
            "code": summarise(code), "rule": rule, "reason": summarise(reason)}


def choice_of(ev: dict, failed_step):
    """What the loop chose after a failed step, read from the next deciding event (None: not a deciding event)."""
    name = ev.get("event")
    if name not in CHOICE:
        return None
    if name == "recover":
        return f"recover:{ev.get('kind')}"
    if name == "done":
        return f"stop:{ev.get('status')}"
    if name == "step_start":
        return "retry" if ev.get("index") == failed_step else "next_step"
    return CHOICE[name]


class Writer:
    """Append-only JSONL, one flush per record, thread-safe; the first error turns it off with one warning."""

    def __init__(self, path: Path, t0: float) -> None:
        self.path, self.t0, self.f, self.dead, self.lock = path, t0, None, False, threading.Lock()

    def write(self, rec: dict) -> None:
        now = time.time()
        rec = {"v": SCHEMA, "t": round(now - self.t0, 3), "ts": round(now, 3), **rec}
        with self.lock:
            if self.dead:
                return
            try:
                if self.f is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.f = open(self.path, "a", encoding="utf-8")
                self.f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                self.f.flush()
            except Exception as e:                      # noqa: BLE001 - fail open: the run goes on without its trace
                self.dead = True
                log.warning("trace: cannot write %s (%s); no trace for the rest of this run", self.path, e)

    def close(self) -> None:
        with self.lock:
            if self.f is not None:
                try:
                    self.f.close()
                except Exception:                       # noqa: BLE001
                    pass
            self.f, self.dead = None, True


def trace_path(folder: str, run) -> Path:
    """``<folder>/<run_id>/trace.jsonl``; no folder: the folder of the run's log (``task_runs``)."""
    base = Path(folder) if folder else Path(getattr(getattr(run, "logger", None), "path", "trace")).parent
    return base / str(getattr(run, "run_id", "run")) / "trace.jsonl"


SPEC = OptionSpec(
    name="trace",
    keys=(Key("enabled", "bool", False, aliases=("trace",)), Key("dir", "str", "", aliases=("trace_dir",), sources=("run", "env"))),
    hooks=("on_start", "on_event", "on_lab", "on_step_failed"),
    order=0,                                             # first: it sees every failure before an option answers it
    enabled="enabled",
    doc="One JSONL file per run (events, lab entries, LLM calls with purpose and time, recovery decisions): docs/trace.md.",
)


class Trace(Option):
    w, stop, pending, step, phase = None, None, None, None, None

    def on_start(self, run):
        self._end()
        self.w, self.pending, self.step, self.phase = Writer(trace_path(self.cfg["dir"], run), time.time()), None, None, None
        self.w.write({"k": "header", "schema": SCHEMA, "run_id": run.run_id, "epoch": round(self.w.t0, 3),
                      "task": summarise(run.task), "lang": run.lang})
        llm_calls = getattr(self.svc, "llm_calls", None)
        self.stop = llm_calls(self._on_llm) if llm_calls else None
        self.svc.lab(SPEC.name, path=str(self.w.path))

    def on_event(self, ev):
        if self.w is not None and isinstance(ev, dict):
            try:
                self._event(ev)
            except Exception as e:                       # noqa: BLE001 - fail open
                log.warning("trace: event not recorded (%s)", e)
        if ev is None or isinstance(ev, dict) and ev.get("event") in ("done", "error"):
            self._end()                                  # None: the run's event stream ended (also when it was closed early)

    def on_lab(self, flag, info):
        if self.w is not None and flag != SPEC.name:
            self.w.write({"k": "lab", "step": self.step, "flag": flag, "data": summarise(info)})

    def on_step_failed(self, run, st, code, reason, a, o):
        if self.w is not None:
            self.pending = failure_record(run, st, code, reason, getattr(run, "i", self.step))
        return None
        yield                                            # a flow hook: a generator that yields nothing

    def _event(self, ev):
        if ev.get("event") in ("step_start", "step_done") and isinstance(ev.get("index"), int):
            self.step = ev["index"]
        self.phase = ev.get("phase") if ev.get("event") == "plan_step" else None
        chose = choice_of(ev, self.pending.get("step")) if self.pending else None
        if chose:
            self.w.write({**self.pending, "chose": chose, "msg": summarise(ev.get("msg"))})
            self.pending = None
        self.w.write(event_record(ev, self.step))

    def _on_llm(self, call):
        if self.w is not None:
            self.w.write(llm_record(call, self.w.t0, self.step, self.phase))

    def _end(self):
        if self.stop is not None:
            self.stop()
        if self.w is not None:
            self.w.close()
        self.w, self.stop = None, None


register(SPEC, Trace)
