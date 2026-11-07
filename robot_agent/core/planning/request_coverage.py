"""Request coverage (lab round 8, option ``request_coverage``, default OFF; off = the earlier behaviour byte for byte).

Before the run says it is done, ONE cheap LLM call compares the person's request with the goals the run held and with what it
executed, and names the parts of the request that have no goal or that the run never touched. The loop then asks the person
whether to do such a part now (a yes adds it as a goal and the run goes on) instead of claiming the task is done.

Data driven: the comparison is the model's, no word list of any language or task. The only deterministic rule is the guard
against a made-up part: a named part must be a quotation of the person's own words (case and spacing aside), else it is
dropped (and the run says done as before). Fails open: an error, an over-budget call or no JSON means no gap.
"""
from __future__ import annotations

import re

MAX_PARTS = 2
MAX_STEPS = 40
SCHEMA = {"type": "object",
          "properties": {"unserved": {"type": "array", "items": {"type": "object", "properties": {
              "part": {"type": "string"},
              "why": {"type": "string", "enum": ["no_goal", "not_touched"]}}, "required": ["part", "why"]}}},
          "required": ["unserved"]}
PROMPT = """A household robot was asked something and is about to say it is done. Compare the request with the goals the robot
held and with the steps it executed.

Request (the person's words): {request}
Goals the robot held: {goals}
Steps the robot executed: {steps}

List every part of the request that the person named (an object to fetch, a place to go to, something to say or to ask,
a person to serve) and that is NOT covered: "no_goal" when no goal stands for it, "not_touched" when a goal stands for it but
no executed step acted on it. Copy each part from the request in the person's own words. A part the robot refused or asked
about on purpose is covered. When every named part is covered, answer {{"unserved": []}}."""


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip().casefold()


def prompt(request: str, goals_text: str, steps: list) -> str:
    """The comparison prompt; ``steps``: ["Action object", ...] (the last ``MAX_STEPS``)."""
    sl = [str(s) for s in steps][-MAX_STEPS:]
    return PROMPT.format(request=request, goals=goals_text or "(none)", steps="; ".join(sl) if sl else "(none)")


def parts(d, request: str) -> list:
    """The named parts of ``d`` (the model's answer) that are quotations of ``request``: unique, at most ``MAX_PARTS``."""
    hay, out = norm(request), []
    items = d.get("unserved") if isinstance(d, dict) and isinstance(d.get("unserved"), list) else []
    for it in items:
        p = str(it.get("part") if isinstance(it, dict) else it or "").strip()
        if p and norm(p) in hay and p not in out:
            out.append(p)
    return out[:MAX_PARTS]
