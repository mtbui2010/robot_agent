"""Option ``goal_ledger`` (default off): a ``go`` goal the goal filter would drop is kept when the heard words carry its place, or kept and named in
the read-back; a destination the person took back is asked about.

What: (R1, keys ``restore`` and ``named``) ``goal_fix.drop_invented`` (F4.4) drops a ``go`` goal whose place is not an exact mention of the
words when other goals remain. The engine asks the service ``goal_keep(run, goals)`` for a predicate at that drop site; with ``restore`` a goal
is kept when a heard word STARTS with a name (head or alias) of its place and that name is at least ``min_share`` of the word (a recogniser
slip at the end of the word: "nookx" for "nook"); its read-back issue ``unsaid`` is then settled (hook ``on_issues``), so it is restored with
no question and no re-sample. With ``named`` a goal with no such word is kept too: its ``unsaid`` issue stays, so the engine's read-back names
it with the robot's doubt (no new question: ``issue_question`` asks nothing for a goal with no object). Kept at the drop site, the goal keeps
its place in the list and passes every later step as a said goal would: the safety gate (``on_goals`` of ``hazard``), ``cond_goal`` and
``go_steps``; a re-sample of the reading keeps it again. A goal dropped by another filter is never seen. (R4, key ``retract``) an ``on`` /
``in`` goal whose destination the words name only in a mention a self-correction marker takes back (the pack's markers, or the option's
narrower ``retract_markers`` per language), when words follow the marker and they name no place or object another goal uses, gets the
engine's own ``unsaid`` issue with its object: the read-back asks the existing "where should I put it?".
Why: the failure class "the reader's goal is dropped by the engine's own filter, or a retracted destination is kept" (the go goal of a request
ending "... then go back to the <place>" whose last word the recogniser garbled was dropped silently and the run ended done without it). No
"Did you mean X?" question is asked (user decision); the open items left (``named`` off, no evidence, but a weak cue: a heard word that
starts with the name with a longer tail, or the words ending in the name's last word cut short to at least ``min_share`` of it) are served
by ``ledger_open(run)`` for the end of the run, and this option says nothing at the end. A drop with no sign of the place at all (most of
them: goals the reader invented) is no open item, so a complete success is never reported as partial.
Switch off: key ``enabled`` False (the default; legacy run option ``goal_ledger``), or a part with ``restore`` / ``named`` (run option ``goal_ledger_named``) / ``retract``
False. Off: the service answers nothing and ``drop_invented`` drops as before. ``min_share`` (0.5) is assumed, not measured. Lab flag
``goal_ledger``: ``{rule: restore, to, heard}``, ``{rule: named, to}``, ``{rule: open, to, heard}``, ``{rule: retract, what}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import names, pack_data, understand

SPEC = OptionSpec(
    name="goal_ledger",
    keys=(Key("enabled", "bool", False, aliases=("goal_ledger",)), Key("restore", "bool", True), Key("named", "bool", False, aliases=("goal_ledger_named",)),
          Key("min_share", "float", 0.5), Key("retract", "bool", False), Key("retract_markers", "dict", {}, sources=("manifest",))),
    hooks=("on_issues",),
    order=22,
    enabled="enabled",
    services=("goal_keep", "ledger_open"),
    doc="A go goal the goal filter would drop is kept when a heard word starts with its place's name (restored silently) or kept and named in the "
        "read-back; a destination the person took back gets the engine's where-question.",
)


def place_names(key: str, places) -> list:
    """The normalised head and aliases of the ENV place ``key``."""
    v = places.env.get(key) or {}
    out = [understand._norm(places.head(key))] + [understand._norm(a) for a in v.get("aliases") or [] if a and "@" not in str(a)]
    return [n for n in dict.fromkeys(out) if n]


def evidence(key: str, t: str, places, share: float) -> str:
    """The heard word of ``t`` (normalised words) that starts with a name of ``key`` and of which that name's last word is at least ``share``; ''."""
    for n in place_names(key, places):
        last = n.split()[-1]
        for m in re.finditer(r"(?<!\w)" + re.escape(n) + r"(\w+)", t):
            if len(last) / (len(last) + len(m.group(1))) >= share:
                return m.group(0)
    return ""


def cue(key: str, t: str, places, share: float) -> str:
    """A weak sign that the words named ``key`` (too weak to keep the goal): a heard word that starts with a name of ``key`` with a longer
    tail than ``evidence`` accepts, or the words ending in a name whose last word is cut short (its start, at least ``share`` of it); ''."""
    for n in place_names(key, places):
        last = n.split()[-1]
        m = re.search(r"(?<!\w)" + re.escape(n) + r"\w+", t)
        if m:
            return m.group(0)
        m = re.search(r"(?<!\w)" + re.escape(n[:len(n) - len(last)]) + r"(\w+)\W*$", t)
        if m and m.group(1) != last and last.startswith(m.group(1)) and len(m.group(1)) / len(last) >= share:
            return m.group(0).rstrip(" .,;:!?")
    return ""


def own_issues(goal, places) -> list:
    """The engine's read-back issues of ``goal`` alone when no word names its place (its ``unsaid`` issue)."""
    return understand.find_issues([goal], "", places)


def _tail(t: str, end: int, corr):
    """The words after the self-correction marker that follows the mention ending at ``end`` (to the end of the sentence), or None."""
    m = next((m for m in (corr.finditer(t) if corr is not None else ()) if m.start() + 1 >= end), None)
    return re.split(r"[.;!?]", t[m.end():], maxsplit=1)[0] if m is not None else None


def _other_named(tail: str, goal, goals, places, langs) -> bool:
    """Does ``tail`` name a place or an object of a goal other than ``goal`` (the marker began another goal, it took nothing back)?"""
    others = [g for g in goals if g is not goal]
    used = {g.get(s) for g in others for s in ("to", "from", "object") if g.get(s)}
    if any(keys & used for _s, _e, _p, keys, _src in understand.place_mentions(tail, places)):
        return True
    return any(g.get("object") and names.has(str(g["object"]), tail, langs) for g in others)


def retracted(goals, text: str, places, corr, langs=None) -> list:
    """``unsaid`` issues (with the object) of the ``on`` / ``in`` goals whose destination the words name only in mentions a marker takes back."""
    t = understand._norm(text)
    ms = understand.place_mentions(t, places)
    kept = understand._drop_corrected(t, ms, corr)
    alive = set().union(*(m[3] for m in kept)) if kept else set()
    out = []
    for g in goals:
        to = g.get("to")
        if g.get("type") not in ("on", "in") or not to or to in alive:
            continue
        for _s, e, _p, keys, _src in (m for m in ms if m not in kept):
            tail = _tail(t, e, corr) if to in keys else None
            if tail and tail.strip() and not _other_named(tail, g, goals, places, langs):
                out += [i for i in own_issues(g, places) if i not in out]
                break
    return out


def settled(issues, goals, evident: dict, places) -> list:
    """``issues`` without those a go goal kept with evidence answers: its ``unsaid`` issue and the object issue of the heard word itself."""
    kept = [g for g in goals if g.get("type") == "go" and g.get("to") in evident]
    own = [i for g in kept for i in own_issues(g, places)]
    heard = {evident[g["to"]] for g in kept}
    return [i for i in issues if i not in own and not (i.get("kind") == "object" and set(str(i.get("what")).split()) & heard)]


class GoalLedger(Option):
    spec = SPEC

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {})

    def goal_keep(self, run, goals):
        """Service (asked at ``drop_invented``): ``keep(goal, words)`` for a ``go`` goal the filter would drop, None when R1 is off."""
        if not (self.cfg["restore"] or self.cfg["named"]):
            return None
        st = self._state(run)
        st["evident"], st["open"] = {}, []
        return lambda g, t: self._keep(run, g, t)

    def _keep(self, run, g, t) -> bool:
        st, to = self._state(run), g.get("to")
        hit = evidence(to, t, run.places, float(self.cfg["min_share"])) if self.cfg["restore"] else ""
        if hit:
            st["evident"][to] = hit
            self.svc.lab(SPEC.name, rule="restore", to=to, heard=hit)
            return True
        if self.cfg["named"]:
            self.svc.lab(SPEC.name, rule="named", to=to)
            return True
        weak = cue(to, t, run.places, float(self.cfg["min_share"]))
        if weak:                                            # the person may have said it: an open item; no sign at all: the reader's own goal
            st["open"].append(dict(g))
            self.svc.lab(SPEC.name, rule="open", to=to, heard=weak)
        return False

    def ledger_open(self, run):
        """Service: the ``go`` goals the last reading dropped with only a weak cue of their place in the words (``cue``; ``named`` off), for
        the end of the run; None when none. A goal dropped with no sign at all is the reader's own, not an item of the request."""
        return list(self._state(run).get("open") or []) or None

    def _corr(self, run):
        """The self-correction markers: the option's own list for the run's language (pack data with a ``source`` or ``assumed`` label,
        re-read at run time; unlabelled = none), else the pack's."""
        data = pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "retract_markers")
        words = data.get(str(getattr(run, "lang", None) or "en")[:2]) if pack_data.labelled(data) else None
        if words:
            return re.compile("|".join(f"(?:{w})" for w in words), re.I)
        return understand._corr_of(run.places)

    def on_issues(self, issues, run, goals):
        """Settle the ``unsaid`` issue of a go goal kept with evidence; add the where-issue of a destination taken back (``retract``)."""
        out = settled(issues, goals, self._state(run).get("evident") or {}, run.places)
        if self.cfg["retract"]:
            words = " ".join([run.task] + [t for w, t in getattr(run, "dialogue", []) if w == "user"])
            add = [i for i in retracted(goals, words, run.places, self._corr(run), names.langs_of(run)) if i not in out]
            for i in add:
                self.svc.lab(SPEC.name, rule="retract", what=i.get("what"))
            out += add
        return out if out != list(issues) else None


register(SPEC, GoalLedger)
