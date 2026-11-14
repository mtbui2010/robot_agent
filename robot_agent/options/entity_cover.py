"""Option ``entity_cover`` (default off): every object the request names ends up as a goal or as a stated reason for dropping it; a dropped one is asked about.

The failure it closes: a request names several objects, the understanding keeps one clause, and the run ends "done" with the others never
mentioned. The names come from the object catalogue (the robot's object memory and the manifest's ``limits.objects``), whole words, with
the request read in the prompt language (service ``request_english``: the task, or the language layer's trusted reading of it).
Names and plurals follow the language packs (``articles``, ``plural_rules``); a name counts only where the request handles it (see ``as_object``), not as a
condition, a negation, a landmark or a tool. Runs before ``ask_focus`` (order 75 < 80): what that option finds redundant is dropped after this one adds.

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
from robot_agent.core.planning import names, understand


_REACH = 40            # a correction takes back the mention within this many characters before its marker (as understand._drop_corrected)

SPEC = OptionSpec(
    name="entity_cover",
    keys=(Key("enabled", "bool", False, aliases=("entity_cover",)), Key("ask_reasoned", "bool", True)),
    hooks=("on_issues",),
    order=75,
    enabled="enabled",
    doc="Entity coverage: every object the request names is a goal or a stated reason for dropping it; a dropped one is asked about.",
)


def _w(s, langs=None) -> str:
    """The words of ``s`` without a leading article (the language packs' ``articles`` of ``langs``; none declared = none stripped)."""
    return names.plain(s, langs)


def world_names(run, robot, langs=None) -> list:
    """The object catalogue: the robot's object memory and the manifest's ``limits.objects`` (normalised, no duplicates)."""
    mem = getattr(getattr(run, "memory", None), "data", None) or {}
    lim = getattr(getattr(robot, "manifest", None), "limits", None) or {}
    return list(dict.fromkeys(n for n in (_w(x, langs) for x in list(mem.get("objects") or {}) + list(lim.get("objects") or {})) if n))


def place_names(places, langs=None) -> set:
    """Every head and alias of an ENV place, as whole-word strings."""
    out = set()
    for k, v in (getattr(places, "env", None) or {}).items():
        out.add(_w(places.head(k), langs))
        out.update(_w(a, langs) for a in (v.get("aliases") or []) if a)
    return {n for n in out if n}


def said(name: str, words: str, langs=None) -> bool:
    """``name`` is, as whole words, in ``words`` (a plural form counts when the language pack declares plural rules)."""
    return names.has(name, words, langs)


def held(name: str, goals, langs=None) -> bool:
    """A goal's object holds ``name`` or is held by it (whole words, either way round)."""
    objs = [g.get("object") for g in goals]
    return any(o and (names.has(name, o, langs) or names.has(o, name, langs)) for o in objs)


def _taken_back(name: str, text: str, corr, catalogue, langs=None) -> list:
    """Spans of ``name`` in ``text`` that a self-correction takes back: the LAST catalogue name before a correction marker (the pack's marker
    regex, language data) and within ``_REACH`` characters of it ("the blue mug, wait, no, the red one"): the person replaced it."""
    marks = [m.start() for m in corr.finditer(text)] if corr is not None else []
    out = []
    for start, end in names.occurrences(name, text, langs):
        for at in marks:
            if end <= at + 1 and at - end < _REACH and not any(
                    e2 <= at + 1 and s2 >= end for n2 in catalogue if n2 != name for s2, e2 in names.occurrences(n2, text, langs)):
                out.append((start, end))
                break
    return out


def as_object(name: str, text: str, places, langs=None, catalogue=()) -> bool:
    """Does the request name ``name`` as something to handle? In its active text (conditions, negations and corrected parts taken out), in
    object position (after a manipulation verb), and not behind a boundary word that makes it a landmark or a tool ("next to the lamp"); the words
    that join two objects (the pack's ``grammar.joiners``) do not count as a boundary."""
    corr, t = understand._corr_of(places), understand._active(text, places=places)
    for a, b in _taken_back(name, t, corr, catalogue, langs):           # a mention the person took back is not asked about
        t = t[:a] + " " * (b - a) + t[b:]
    t = corr.sub(" ", t)
    arts, join = names.articles(langs), names.joiners(langs)
    for start, end in names.occurrences(name, t, langs):
        before = re.findall(r"[\w']+", t[:start])
        while before and before[-1] in arts:
            before.pop()
        if (understand._object_position(t, start, end) or not understand._MANIP.search(t)) and not (before and before[-1] in understand._BOUND and before[-1] not in join):
            return True
    return False


def inside_longer(name: str, words: str, catalogue, langs=None) -> bool:
    """Every mention of ``name`` in ``words`` lies inside a mention of a longer catalogue name ("mug" in "the red mug"): that one is its own entry."""
    spans = [sp for n2 in catalogue if n2 != name and names.has(name, n2, langs) for sp in names.occurrences(n2, words, langs)]
    mine = names.occurrences(name, words, langs)
    return bool(mine) and all(any(a <= s0 and e0 <= b for a, b in spans) for s0, e0 in mine)


def same_object(name: str, goals, words: str, catalogue, langs=None) -> bool:
    """``name`` is another name of a goal's object: they share a word, the request never says the goal's own name, and no third catalogue
    entry carries that word ("TV remote" for the goal's "remote control"). Two objects both named in the request are two objects."""
    for o in {g.get("object") for g in goals if g.get("object")}:
        if names.has(o, words, langs):
            continue
        shared = set(names.plain(name, langs).split()) & set(names.plain(o, langs).split()) - set(names.articles(langs))
        if any(not [c for c in catalogue if c not in (name, o) and names.has(w, c, langs)] for w in shared):
            return True
    return False


def reason(name: str, notes, langs=None) -> str:
    """The note of ``run.filtered`` that names ``name`` (why a goal for it was dropped), else ""."""
    return next((str(x) for x in notes or () if said(name, str(x), langs)), "")


class EntityCover(Option):
    spec = SPEC

    def on_issues(self, issues, run, goals):
        langs = names.langs_of(run)
        words = getattr(self.svc, "request_english", lambda r: getattr(r, "task", ""))(run)
        catalogue = world_names(run, self.svc.robot, langs)
        if not words or not catalogue or not goals:
            return issues
        known = place_names(run.places, langs)
        out = list(issues)
        for n in catalogue:
            if n in known or not said(n, words, langs) or held(n, goals, langs) or any(said(n, str(i.get("what")), langs) for i in out):
                continue
            if inside_longer(n, words, catalogue, langs) or same_object(n, goals, words, catalogue, langs):
                continue
            if not as_object(n, words, run.places, langs, catalogue):
                continue
            why = reason(n, getattr(run, "filtered", None), langs)
            if why and not self.cfg.get("ask_reasoned"):
                continue
            out.append({"kind": "object", "what": n, **({"reason": why} if why else {})})
            self.svc.lab("entity_cover", what=n, reason=why)
        return out


register(SPEC, EntityCover)
