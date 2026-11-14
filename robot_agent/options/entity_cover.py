"""Option ``entity_cover`` (default off): every object the request names ends up as a goal or as a stated reason for dropping it; a dropped one is asked about.

The failure it closes: a request names several objects, the understanding keeps one clause, and the run ends "done" with the others never
mentioned. The names come from the object catalogue (the robot's object memory and the manifest's ``limits.objects``), whole words, with
the request read in the prompt language (service ``request_english``: the task, or the language layer's trusted reading of it).

* a catalogue name the request says that a goal's object does not hold (whole words, either way round) and that is not a place the robot knows
  is an open ``object`` issue; the read-back asks about it once (the engine's ``issue_asked``), so a silent drop becomes a question;
* when the dropped goal left a note (``run.filtered``: the reason it was dropped) the issue carries it (``reason``); key ``ask_reasoned``
  (default on) decides whether such a drop is asked about too (the person may have meant it: asked once, no more);
* a name the catalogue does not know is ignored; no catalogue, no request words: nothing is added.

No pack data: the catalogue is what the robot already holds. Hook ``on_issues`` (transform). Keys: ``enabled``, ``ask_reasoned``.
Lab flag ``entity_cover``: ``{what, reason}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import Key, Option, OptionSpec, register

_ARTICLES = ("the ", "a ", "an ")

SPEC = OptionSpec(
    name="entity_cover",
    keys=(Key("enabled", "bool", False, aliases=("entity_cover",)), Key("ask_reasoned", "bool", True)),
    hooks=("on_issues",),
    order=85,
    enabled="enabled",
    doc="Entity coverage: every object the request names is a goal or a stated reason for dropping it; a dropped one is asked about.",
)


def _w(s) -> str:
    t = " ".join(re.findall(r"[\w']+", str(s or "").lower()))
    return next((t[len(a):] for a in _ARTICLES if t.startswith(a)), t)


def world_names(run, robot) -> list:
    """The object catalogue: the robot's object memory and the manifest's ``limits.objects`` (normalised, no duplicates)."""
    mem = getattr(getattr(run, "memory", None), "data", None) or {}
    lim = getattr(getattr(robot, "manifest", None), "limits", None) or {}
    return list(dict.fromkeys(n for n in map(_w, list(mem.get("objects") or {}) + list(lim.get("objects") or {})) if n))


def place_names(places) -> set:
    """Every head and alias of an ENV place, as whole-word strings."""
    out = set()
    for k, v in (getattr(places, "env", None) or {}).items():
        out.add(_w(places.head(k)))
        out.update(_w(a) for a in (v.get("aliases") or []) if a)
    return {n for n in out if n}


def _has(small: str, big: str) -> bool:
    """The words of ``small`` are, in order, whole words of ``big`` (the last one may be a plural there)."""
    return bool(small) and re.search(r"(?<!\w)" + re.escape(small) + r"(?:e?s)?(?!\w)", big) is not None


def said(name: str, words: str) -> bool:
    """``name`` is, as whole words, in ``words``."""
    return _has(_w(name), _w(words))


def held(name: str, goals) -> bool:
    """A goal's object holds ``name`` or is held by it (whole words, either way round)."""
    n = _w(name)
    return any(o and (_has(n, o) or _has(o, n)) for o in (_w(g.get("object")) for g in goals))


def reason(name: str, notes) -> str:
    """The note of ``run.filtered`` that names ``name`` (why a goal for it was dropped), else ""."""
    return next((str(x) for x in notes or () if said(name, str(x))), "")


class EntityCover(Option):
    spec = SPEC

    def on_issues(self, issues, run, goals):
        words = getattr(self.svc, "request_english", lambda r: getattr(r, "task", ""))(run)
        names = world_names(run, self.svc.robot)
        if not words or not names or not goals:
            return issues
        known = place_names(run.places)
        out = list(issues)
        for n in names:
            if n in known or not said(n, words) or held(n, goals) or any(said(n, str(i.get("what"))) for i in out):
                continue
            why = reason(n, getattr(run, "filtered", None))
            if why and not self.cfg.get("ask_reasoned"):
                continue
            out.append({"kind": "object", "what": n, **({"reason": why} if why else {})})
            self.svc.lab("entity_cover", what=n, reason=why)
        return out


register(SPEC, EntityCover)
