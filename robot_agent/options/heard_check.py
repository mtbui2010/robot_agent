"""Option ``heard_check`` (default off): a goal whose own quote of the person is not in the heard words is an open ``object`` issue.

What: the quote of an ``on`` / ``in`` goal is its ``said``, of a ``say`` goal its message (``content``, else ``object``). A quote none of
whose tokens a heard token matches (the same first ``min_prefix`` characters, or a similarity of ``min_ratio`` with the heard token cut to
its length; the ENV's places and people cut from both) adds one issue ``{kind: object, what: <quote>}`` to the read-back. A quote whose
main script is not the heard words' main script, or no quote: not checked (fail open). Design: docs/heard_check.md.
Why: the reader filled a recogniser gap with an object or a message the person never said; the engine checks heard words -> goals only.
How: hook ``on_issues`` (order 82, after ``ask_focus``); the engine's own re-sample, question and read-back follow; nothing else is added.
Data ``planner.options.heard_check.match`` ``{min_prefix, min_ratio, source | assumed: true}``; none or unlabelled: nothing happens.
Switch off: key ``enabled`` (run option ``heard_check``), off by default. Event: lab flag ``heard_check`` ``{goals, unheard}``.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import pack_data, understand

FLAG = "heard_check"

SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)), Key("match", "dict", {}, sources=("manifest",))),
    hooks=("on_issues",),
    order=82,
    enabled="enabled",
    doc="A goal whose quote of the person (said, or a message) no heard word matches is an object issue of the read-back (needs planner.options.heard_check.match).",
)


def tokens(text) -> list:
    """The lower-cased word tokens of ``text`` (Unicode word characters)."""
    return re.findall(r"\w+", str(text or "").lower())


def main_script(text) -> str:
    """The Unicode script of most letters of ``text`` (the first word of each letter's Unicode name); '' when it has no letter."""
    c = Counter(unicodedata.name(ch, " ").split(" ")[0] for ch in str(text or "") if ch.isalpha())
    return c.most_common(1)[0][0] if c else ""


def quote(goal) -> str:
    """The goal's own quote of the person: ``said`` of an on / in goal, the message of a say goal; '' for any other goal."""
    if not isinstance(goal, dict):
        return ""
    if goal.get("type") in ("on", "in"):
        return str(goal.get("said") or "")
    return str(goal.get("content") or goal.get("object") or "") if goal.get("type") == "say" else ""


def limits(data):
    """``(min_prefix, min_ratio)`` of labelled match data, or None (missing, unlabelled or not numbers: the option does nothing)."""
    if not pack_data.labelled(data):
        return None
    try:
        return int(data["min_prefix"]), float(data["min_ratio"])
    except (KeyError, TypeError, ValueError):
        return None


def token_found(q: str, heard, min_prefix: int, min_ratio: float) -> bool:
    """A heard token starts like ``q`` (its first ``min_prefix`` characters, all of a shorter ``q``), or is ``min_ratio`` similar cut to its length."""
    need = min(min_prefix, len(q))
    for h in heard:
        if need > 0 and h[:need] == q[:need]:
            return True
        if difflib.SequenceMatcher(None, q, h[:len(q)]).ratio() >= min_ratio:
            return True
    return False


def unheard_goals(goals, heard_tokens, data, strip=None) -> list:
    """The goals whose quote (``strip``: a text -> text cut, e.g. of place names) no heard token matches; [] with no data or no heard token."""
    lim = limits(data)
    if lim is None or not heard_tokens:
        return []
    cut = strip or (lambda s: s)
    script, out = main_script(" ".join(heard_tokens)), []
    for g in goals or ():
        q = tokens(cut(quote(g)))
        if q and main_script(" ".join(q)) == script and not any(token_found(t, heard_tokens, *lim) for t in q):
            out.append(g)
    return out


def heard_text(run) -> str:
    """What the person said in this run: the request and their answers."""
    answers = [str(t) for w, t in getattr(run, "dialogue", None) or [] if w == "user"]
    return " ".join([str(getattr(run, "task", "") or "")] + answers)


class HeardCheck(Option):
    def on_issues(self, issues, run, goals):
        """The read-back's issues plus one object issue per goal whose quote was not heard; None (unchanged) when there is none."""
        data = pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "match")
        places = getattr(run, "places", None)
        strip = (lambda s: understand.without_places(s, places, places.keys)) if places is not None else None
        heard = tokens(strip(heard_text(run)) if strip else heard_text(run))
        new = []
        for g in unheard_goals(goals, heard, data, strip):
            x = {"kind": "object", "what": quote(g)}
            if x not in issues and x not in new:
                new.append(x)
        if not new:
            return None
        self.svc.lab(FLAG, goals=len(goals), unheard=[x["what"] for x in new])
        return list(issues) + new


register(SPEC, HeardCheck)
__all__ = ["FLAG", "SPEC", "HeardCheck", "tokens", "main_script", "quote", "limits", "token_found", "unheard_goals", "heard_text"]
