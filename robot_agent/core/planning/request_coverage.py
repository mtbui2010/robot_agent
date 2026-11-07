"""Request coverage (lab round 8, option ``request_coverage``, default OFF; off = the earlier behaviour byte for byte).

Before the run says it is done, ONE cheap LLM call compares the person's request with the goals the run held and with what it
executed, and names the parts of the request that have no goal or that the run never touched. The loop then asks the person
whether to do such a part now (a yes adds it as a goal and the run goes on) instead of claiming the task is done.

Data driven: the comparison is the model's, no word list of any language or task. Two deterministic rules guard it: a named
part must be a quotation of the person's own words (case and spacing aside), and a part that names something the run already
covered (an object, place or person of a held goal, the person's own word for it, or an object or place of an executed step;
the names come from the ENV aliases and the goals, ``covered_names``) is dropped: a small model names such parts by mistake.
The question is asked about the first part that survives. Fails open: an error, an over-budget call, no JSON, no answer or an
answer the loop cannot read means no gap (the run says done as before, the goals were observed to hold).
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


def covered_names(goals, completed, places) -> list:
    """The words that name what the run already covered: for each held goal its object, its ``said`` / ``name_local`` words
    and the places and people it names (``from`` / ``to``), for each executed step its object, each with the ENV entry's
    head and aliases when the name is an ENV key. The speaker's own words ("me") are left out, and names under two
    letters. Lower case, unique."""
    env = getattr(places, "env", {}) or {}
    out: list = []

    def add(x):
        x = norm(x)
        if len(x) >= 2 and x not in out:
            out.append(x)

    def add_key(k):
        k = str(k or "")
        if not k:
            return
        e = env.get(k)
        if e is None:
            add(k.replace("_", " "))
            return
        if e.get("speaker"):
            return
        add(k.split("@")[0].replace("_", " "))
        for a in e.get("aliases") or []:
            if "@" not in str(a) and not _speaker_word(a):
                add(a)
        if e.get("person"):
            add(e.get("person") if isinstance(e.get("person"), str) else "")

    for g in goals or []:
        for f in ("said", "name_local"):
            add(g.get(f))
        for f in ("object", "from", "to"):
            add_key(g.get(f))
    for st in completed or []:
        add_key(st.get("object"))
    return out


def _speaker_word(a) -> bool:
    from .persons import is_speaker_word
    return is_speaker_word(a)


def names_any(part: str, names) -> bool:
    """Does ``part`` contain one of ``names``? A name in Latin letters must stand as a word; a name with other letters
    (Hangul, kana, ...) is found inside a word, its particles being attached."""
    t = norm(part)
    for n in names:
        if re.search(r"[^\W\d_]", n) and n.isascii():
            if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", t):
                return True
        elif n in t:
            return True
    return False


def parts(d, request: str, covered=()) -> list:
    """The named parts of ``d`` (the model's answer) that are quotations of ``request`` and name nothing of ``covered``
    (:func:`covered_names`): unique, at most ``MAX_PARTS``, in the model's order (the first is the one asked about)."""
    hay, out = norm(request), []
    items = d.get("unserved") if isinstance(d, dict) and isinstance(d.get("unserved"), list) else []
    for it in items:
        p = str(it.get("part") if isinstance(it, dict) else it or "").strip()
        if p and norm(p) in hay and p not in out and not names_any(p, covered):
            out.append(p)
    return out[:MAX_PARTS]
