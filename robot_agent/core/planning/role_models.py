"""Lab round 8 (every option default OFF / empty; off = the earlier behaviour byte for byte): a model per ROLE.

* ``understand_think`` / ``repair_think`` / ``dialog_think``: Ollama's ``think`` switch for the client of that role (a thinking
  model answers EMPTY when its reasoning uses the whole token cap). Absent = not sent.
* ``repair_model`` / ``repair_url`` / ``repair_max_tokens``: the model that answers the PLAN GATE's repair prompts (the
  planner's sub-goal edit and the one refinement with the checker's findings), never the first plan, the execution replans
  or the understand step. A client of that model (role ``repair``); the planner is called ONCE with the same messages when
  the answer is a runaway (empty, a repeating line, longer than the token cap allows, an error). A planner that offers no
  ``chat_override`` (``backends/pyplanner_grace.GraceBackend`` does) is repaired by its own model, as before.
* ``dialog_model`` / ``dialog_model_lang`` ({language code: model}) / ``dialog_url`` / ``dialog_max_tokens``: the model that
  WRITES what the robot says in the user's language (the translator's output: questions, read-backs, statements, and the
  object names it writes in that language). Reading the user's words (the input pivot) stays with the translator. A
  language with no entry and no default is written by the translator, as before. A guard failure (an error, an empty or
  repeating or over-long text, the speech guard's verdict) is answered by ONE call of the translator.

Nothing here names a language or a model: both come from data (option > ``$ROBOT_AGENT_<KEY>`` > the manifest's
``planner.<key>`` > off).
"""
from __future__ import annotations

import json
from typing import Callable, Optional

KEYS = ("understand_think", "repair_model", "repair_url", "repair_max_tokens", "repair_think",
        "dialog_model", "dialog_model_lang", "dialog_url", "dialog_max_tokens", "dialog_think")
CHARS_PER_TOKEN = 4          # an answer this long is taken to have hit the token cap (a cut answer is not a valid one)
REPEAT_LINES = 6             # the same non-empty line this many times in a row: a loop


def think_of(v) -> Optional[bool]:
    """``think`` from a setting: on-words True, off-words False, anything else (absent too) None = do not send."""
    s = str(v).strip().lower() if v is not None else ""
    return True if s in ("1", "true", "yes", "on") else False if s in ("0", "false", "no", "off") else None


def config(pick: Callable) -> dict:
    """The round-8 cfg keys. ``pick(key, default)``: option > env > manifest > default (the loop's resolver)."""
    def count(k):
        try:
            n = int(pick(k, None))
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None
    langs = pick("dialog_model_lang", None)
    if isinstance(langs, str):
        try:
            langs = json.loads(langs)
        except ValueError:
            langs = None
    return {
        "understand_think": think_of(pick("understand_think", None)),
        "repair_model": str(pick("repair_model", "") or "").strip(),
        "repair_url": pick("repair_url", None) or None,
        "repair_max_tokens": count("repair_max_tokens"),
        "repair_think": think_of(pick("repair_think", None)),
        "dialog_model": str(pick("dialog_model", "") or "").strip(),
        "dialog_model_lang": {str(k).strip().lower(): str(v).strip() for k, v in langs.items()
                              if str(k).strip() and str(v or "").strip()} if isinstance(langs, dict) else {},
        "dialog_url": pick("dialog_url", None) or None,
        "dialog_max_tokens": count("dialog_max_tokens"),
        "dialog_think": think_of(pick("dialog_think", None)),
    }


def runaway(out, max_tokens=None) -> str:
    """Why a model's text is a runaway ("empty", "long": as long as the token cap allows, so probably cut, "repeat": one line
    many times in a row), else ""."""
    t = str(out or "")
    if not t.strip():
        return "empty"
    if max_tokens and len(t) >= int(max_tokens) * CHARS_PER_TOKEN:
        return "long"
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    run = 1
    for a, b in zip(lines, lines[1:]):
        run = run + 1 if a == b else 1
        if run >= REPEAT_LINES:
            return "repeat"
    return ""


def flatten(messages) -> str:
    """A chat's messages as one prompt (the role clients take a prompt): the contents in order, blank-line separated."""
    return "\n\n".join(str(m.get("content", "")) for m in (messages or []) if isinstance(m, dict) and m.get("content"))


def role_client(loop, role: str, model: str, url=None, think=None):
    """The client of ``role`` for ``model`` (built once per run object, as the translator's is): ``role`` in its config, on
    ``url``, else ``translate_url``, else the planner's server."""
    clients = loop.__dict__.setdefault("_role_clients", {})
    if (role, model) not in clients:
        from ...connect.llm import init_llm_client
        u = url or loop.cfg.get("translate_url") or (loop._llm_cfg or {}).get("url")
        cfg = dict(loop._llm_cfg or {}, model=model, url=u, role=role)
        if think is not None:
            cfg["think"] = think
        clients[(role, model)] = init_llm_client(cfg=cfg)
    return clients[(role, model)]


def repair_chat(loop, budget=None) -> Optional[Callable]:
    """``fn(planner_chat, messages, temperature) -> (text, in_tokens, out_tokens)`` for a planner's ``chat_override`` when the
    gate's repair has a model of its own, else None. ``planner_chat``: the planner's own chat (the one fallback call, which
    takes a unit of ``budget`` like any other)."""
    c = loop.cfg
    model = str(c.get("repair_model") or "").strip()
    if not model or model == loop._planner_model():
        return None
    cap = c.get("repair_max_tokens")

    def fn(planner_chat, messages, temperature=0.2):
        from ..run_control import is_cancel
        from .understand import LLMBudgetExceeded
        out, why = "", ""
        try:
            kw = {"temperature": temperature, **({"num_predict": int(cap)} if cap else {})}
            out = str(role_client(loop, "repair", model, c.get("repair_url"), c.get("repair_think"))
                      .chat(prompt=flatten(messages), **kw))
            why = runaway(out, cap)
        except LLMBudgetExceeded:
            raise
        except Exception as e:                   # noqa: BLE001 - a model that cannot answer: the planner repairs
            if is_cancel(e):
                raise
            why = "unavailable"
        if not why:
            return out, 0, 0
        loop._lab("repair_model", fallback=why, model=model)
        if budget is not None:
            budget.take(what="plan repair fallback")
        return planner_chat(messages, temperature)
    return fn
