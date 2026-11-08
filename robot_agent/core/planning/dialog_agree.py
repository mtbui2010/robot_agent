"""Dialogue agreement (solution-lab round 12, option ``dialog_agree``; default OFF, off = the earlier behaviour byte for byte).

Failure class: the robot reads a task back, the person says "no" (or "I don't know") although the reading was right, the
reading comes back after the one which-part question, and the run gives up ("the user did not confirm the task"). A person
who answers the which-part question with "In the fridge, please." names a part of the reading and nothing else: that is
agreement, said in other words. The run then goes on as a confirmed reading, with the reading said as a statement (never
a third yes / no question).

Rule (``decide``), applied once per run, only where the run would stop (the refused reading came back after the which-part
question). The reading R counts as confirmed when all of these hold:

* **T2** the person's latest answer (to the which-part question) agrees with R: it names at least one part of R and
  nothing outside R. A restatement alone is not enough: a verbatim restatement trivially "agrees" with any reading made
  from it.
* **C2** no utterance since R was first read back conflicts with R, unless a later agreeing utterance superseded it: an
  answer on the same declared choice question, or, when the conflict named a single place, a later answer naming one
  place that is R's.
* **C3** every refusal of R was a bare "no", a "not sure", or words that conflict and were superseded. Words that agree
  with R ("no, <a part R has>") block: the fault is in a part the person did not name.
* **C4** every coverage doubt of R is settled: it was asked as its own question, an agreeing answer named the place (or its
  room), or a choice answer settled it out (``settled_by_choices``).

Supporting rule S1 (``settled_by_choices``): a question that declares a choice between places (``LocalText.ask`` with the
intent ``choose_place`` and the place keys as options) and an answer that names exactly one of them settles the other
options out: they are not read back as a doubt ("you mentioned X") any more.

Everything is data the robot already has: ENV places, persons, rooms and aliases (``understand.place_mentions``,
``Places``), the goals' object names (``dialog_converge._score``), the core speaker forms, the options the robot itself
declared in a question, the coverage check and ``dialog_unstick.unsure_answer`` with its data. There is no word list and no
number here. The English of an utterance in another language is used only when the language layer already holds it
(``texts_of``): no translator call. A language with no data names fewer parts, its answers come out neutral and the run
takes today's path (fail open). No benchmark text.

Pure functions, no LLM, no ENV mutation; ``AssistLoop`` records the history (``r.agree``) and acts on the result.
"""

from __future__ import annotations

import re

from . import dialog_converge as DC, persons
from .understand import _norm, place_mentions

INTENT_PLACE = "choose_place"       # a question that declares a choice between places (options: ENV keys)
INTENT_OBJECT = "choose"            # a question that declares a choice between things (options: object names)


def rkeys(goals, places) -> set:
    """The parts of the reading: ENV keys of its places and persons, the object of an open / close goal, a go target and
    the speaker when it hands something over."""
    keys = set()
    for g in goals:
        for s in ("to", "from", "near_person", "user_at"):
            if g.get(s) and not str(g[s]).startswith("@"):
                keys.add(g[s])
        if g.get("type") in ("open", "close") and g.get("object"):
            keys.add(g["object"])
        if g.get("type") == "go" and g.get("to"):
            keys.add(g["to"])
        if g.get("hand") or str(g.get("to") or "").lower() in ("me", "user"):
            sk = persons.speaker_key(places)
            if sk:
                keys.add(sk)
    return keys


def _messages(goals) -> list:
    return [_norm(g.get("object") or g.get("content") or "") for g in goals if g.get("type") == "say"]


def _words(s) -> set:
    return set(re.findall(r"[^\W_]+", _norm(s)))


def names_option(texts, option, options) -> bool:
    """Do ``texts`` name ``option``: its distinguishing words (those not shared by every option) are all in one text."""
    common = set.intersection(*[_words(x) for x in options]) if options else set()
    dist = _words(option) - common
    return bool(dist) and any(dist <= _words(t) for t in texts)


def choices_of(dialogue, goals) -> list:
    """The declared object choices the robot asked (intent ``choose``, at least two options): [(options, the options the
    reading took)]. Place choices are judged through the place mentions and ``settled_by_choices`` instead."""
    from .speech import declared_ask
    objs = [str(g.get("object") or "").lower() for g in goals]
    out = []
    for w_, q in dialogue:
        if w_ != "robot":
            continue
        d = declared_ask(q) or {}
        opts = [str(o) for o in (d.get("options") or [])]
        if d.get("intent") == INTENT_OBJECT and len(opts) >= 2:
            out.append((opts, [o for o in opts if o.lower() in objs]))
    return out


def judge(texts, goals, places, settled_out=(), choices=(), guard=None) -> tuple:
    """(``"agree"`` | ``"conflict"`` | ``"neutral"``, detail) of one utterance (``texts``: as said, and its English when
    held) against the reading ``goals``. Conflict: it names an ENV place, room or the speaker the reading does not hold and
    that is not settled out, or the option the reading did not take. Agree: it names a part of the reading and no conflict.
    A mention inside a message the reading will say is the message, not a slot, and is ignored unless the reading holds it.
    ``guard`` (option ``dialog_agree2``, ``dialog_ext.Guard``): an object counts only when its name is said as it is."""
    rk = rkeys(goals, places)
    msgs = _messages(goals)
    agree, conflict, n_mentions = [], [], 0
    for t in texts:
        nt = _norm(t)
        for _s, _e, p, keys, _src in place_mentions(nt, places):
            keys = {k for k in keys if not str(k).startswith("@")}
            if not keys or keys <= set(settled_out):
                continue
            if not (keys & rk) and any(p in m for m in msgs if m):
                continue
            n_mentions += 1
            (agree if keys & rk else conflict).append(("place", p, sorted(keys)))
        try:
            rooms = [k[1:] for k in places.candidates(t, t) if str(k).startswith("@")]
        except Exception:                                   # noqa: BLE001 - a lookup that fails names nothing
            rooms = []
        for rm in rooms:
            if any(places.room(k) == rm for k in rk):
                agree.append(("room", rm))
            elif not any(rm in m for m in msgs if m):
                conflict.append(("room", rm))
    if DC.speaker_said(texts):
        sk = persons.speaker_key(places)
        if sk and sk in rk:
            agree.append(("speaker", sk))
        elif sk and not any(m and sk in m for m in msgs):
            conflict.append(("speaker", sk))
    for g in goals:
        if (guard.exact_score(g, texts) if guard is not None else DC._score(g, texts)) >= 2:
            agree.append(("object", g.get("object")))
    for qi, (opts, taken) in enumerate(choices):
        named = [o for o in opts if names_option(texts, o, opts)]
        if len(named) == 1:
            (agree if named[0] in taken else conflict).append(("option", qi, named[0]))
    verdict = "conflict" if conflict else ("agree" if agree else "neutral")
    return verdict, {"agree": agree, "conflict": conflict, "n_place_mentions": n_mentions}


def settled_by_choices(dialogue, places, texts_of) -> set:
    """S1: the place options a choice answer did not take. A robot question that declares a choice between places
    (intent ``choose_place``, the keys as options, at least two) answered by naming exactly one of them: the others."""
    from .speech import declared_ask
    gone = set()
    for i, (w_, q) in enumerate(dialogue):
        if w_ != "robot" or i + 1 >= len(dialogue) or dialogue[i + 1][0] != "user":
            continue
        d = declared_ask(q) or {}
        if d.get("intent") != INTENT_PLACE:
            continue
        keys = [k for k in dict.fromkeys(str(o) for o in (d.get("options") or [])) if k in getattr(places, "env", {})]
        if len(keys) < 2:
            continue
        named = set()
        for t in texts_of(dialogue[i + 1][1]):
            for m in place_mentions(_norm(t), places):
                named |= {k for k in m[3] if k in keys}
        if len(named) == 1:
            gone |= set(keys) - named
    return gone


def settle_pair(question, answer, places, texts_of) -> set:
    """``settled_by_choices`` for the one question / answer just asked (the loop adds it to ``r.replaced``)."""
    return settled_by_choices([("robot", question), ("user", answer)], places, texts_of)


def decide(goals, places, dialogue, first, refusals, asked_doubts, issues, describe, texts_of, part_asked, replaced=(),
           guard=None) -> dict:
    """The rule, then (option ``dialog_agree2``, ``guard`` given) the guard: when the rule would fire, the agreement must also
    hold with names said as they are, name a slot of the reading (not only an object) and come from words with no garbled name
    and in the language's script; else ``fire`` is False and ``reask`` True (the loop asks the person once more).
    Without a guard this is the rule alone, as in round 12."""
    res = _rule(goals, places, dialogue, first, refusals, asked_doubts, issues, describe, texts_of, part_asked, replaced)
    if guard is None or not res["fire"]:
        return res
    strict = _rule(goals, places, dialogue, first, refusals, asked_doubts, issues, describe, texts_of, part_asked, replaced,
                   guard)
    utts = res["utts"]
    why = []
    if not strict["fire"]:
        why.append("inexact")
    elif rkeys(goals, places) and not strict["slot"]:
        why.append("object_only")
    garb = guard.garbled(utts)
    if garb:
        why.append("garbled")
    if utts and not guard.plausible(utts[-1]):
        why.append("script")
    return {**res, "fire": not why, "reask": bool(why), "guard": why, "garbled": garb}


def _rule(goals, places, dialogue, first, refusals, asked_doubts, issues, describe, texts_of, part_asked, replaced=(),
          guard=None) -> dict:
    """The round-12 rule. ``first``: the index in ``dialogue`` where the reading was first read back; ``refusals``: [{"ans", "rest",
    "unsure"}] of this reading (``rest`` empty = a bare "no"); ``asked_doubts``: the doubts already asked as questions;
    ``issues``: the coverage issues of the reading (``AssistLoop._issues``), ``describe``: an issue as text; ``texts_of(u)``:
    the utterance and its held English. Returns {"fire": bool, ...evidence for the lab log}."""
    R = [dict(g) for g in goals]
    settled_out = settled_by_choices(dialogue, places, texts_of) | set(replaced or ())
    choices = choices_of(dialogue, R)
    utts = [t for w_, t in dialogue[first:] if w_ == "user"] if first is not None else []
    cls = [judge(texts_of(u), R, places, settled_out, choices, guard) for u in utts]
    # C2: a conflicting utterance stands unless a later agreeing utterance superseded it
    unsup = []
    for i, (v, d) in enumerate(cls):
        if v != "conflict":
            continue
        sup = False
        for j in range(i + 1, len(cls)):
            vj, dj = cls[j]
            if vj != "agree":
                continue
            qi = {c[1] for c in d["conflict"] if c[0] == "option"}
            if qi and qi <= {a[1] for a in dj["agree"] if a[0] == "option"} and len(d["conflict"]) == len(qi):
                sup = True
            if (len(d["conflict"]) == 1 and d["conflict"][0][0] == "place" and d["n_place_mentions"] == 1
                    and dj["n_place_mentions"] == 1 and any(a[0] == "place" for a in dj["agree"])):
                sup = True
        if not sup:
            unsup.append(utts[i])
    # C3: every refusal was a bare no, a not-sure, or words that conflicted and were superseded
    bad_ref = []
    for x in refusals:
        if not x.get("rest") or x.get("unsure"):
            continue
        v, _d = judge(texts_of(x["rest"]), R, places, settled_out, choices, guard)
        if v == "conflict" and x.get("ans") not in unsup:
            continue
        bad_ref.append(x.get("ans"))
    # C4: every coverage doubt is settled
    agreed_words = " ".join(_norm(t) for (v, _d), u in zip(cls, utts) if v == "agree" for t in texts_of(u))
    agreed_rooms = {a[1] for (v, d) in cls if v == "agree" for a in d["agree"] if a[0] == "room"}
    asked = {_norm(str(a)) for a in (asked_doubts or ())}
    open_doubts = []
    for it in issues:
        what = _norm(it.get("what"))
        if _norm(str(describe(it))) in asked:
            continue
        if what and what in agreed_words:
            continue
        keys = set(it.get("keys") or [])
        if keys and keys <= settled_out:
            continue
        if it.get("kind") == "unsaid":
            ks = [k for k in rkeys(R, places) if _norm(places.head(k)) == what]
            if ks and any(places.room(k) in agreed_rooms for k in ks):
                continue
        open_doubts.append(str(describe(it)))
    last = cls[-1][0] if cls else None
    ok = not unsup and not bad_ref and not open_doubts
    return {"fire": bool(ok and last == "agree" and part_asked), "last": last, "unsuperseded": unsup,
            "bad_refusals": bad_ref, "open_doubts": open_doubts, "settled_out": sorted(settled_out),
            "verdicts": [v for v, _d in cls], "utts": utts,
            "slot": bool(cls) and any(a[0] != "object" for a in cls[-1][1]["agree"])}


__all__ = ["decide", "judge", "settled_by_choices", "settle_pair", "choices_of", "names_option", "rkeys", "INTENT_PLACE"]
