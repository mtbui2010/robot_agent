"""Option ``else_ask`` (default off): a request with an "otherwise" clause that no goal carries asks the person ONCE what to do otherwise.

What: hook ``on_goals`` at order 18, after ``cond_extract`` (15) wrote the ``when`` field of ``cond_goal`` on the branch goals (or the reader
wrote it) and before ``cond_goal`` (20) reads it. It asks when (1) the goals' ``when`` fields name exactly ONE condition (one object / place)
and all say the same ``present`` (the other branch has no goal), (2) the request (its words, or its reading in the prompt language) holds an
else word of the language pack (``patterns.otherwise``), and (3) the else clause (from the else word to the end of its sentence) names an
object of the robot's catalogue (object memory, manifest ``limits.objects``) or a place of its map that no goal and not the condition names:
"otherwise just leave it" names nothing, so nothing is asked. The question is the language pack's phrase ``q_else`` (block ``speech``,
labelled; slots ``{object}``, ``{place}`` optional). The answer is read as a change of the request (engine service ``reread_change``); each
goal it adds that is not a goal of the run nor one the run filtered gets the ``when`` of the other branch (``present`` flipped) and joins
the goals, which pass the safety gate again (service ``safety_gate``); ``cond_goal`` then looks and keeps the branch that holds. A later
reading that reads exactly as the first one (``goal_sig``, the same condition) gets the same added goals again, without a question.
Why: the failure class "conditional": the reader read only the if branch of "if X, do A, otherwise B"; ``cond_extract`` cannot make a goal the
reader did not read, and the user decided: no forced split to an else branch, ask once. Never: a goal the person did not say, a branch
forced, a second question in a run.
Switch off: key ``enabled`` False (the default; run option ``else_ask``). No else word, no phrase in the run's language nor in English, no
known name in the else clause: nothing is asked (fail open); no answer, or one that adds no goal: the goals stay. Lab flag ``else_ask``:
``{object, at, names, added}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import dialog_converge, langdata, names, speech

SPEC = OptionSpec(
    name="else_ask",
    keys=(Key("enabled", "bool", False, aliases=("else_ask",)),),
    hooks=("on_goals",),
    order=18,
    enabled="enabled",
    doc="A request whose else clause names what no goal carries asks once what to do otherwise; the answer's goals get the other branch's condition.",
)
PATTERN = "otherwise"                   # the language packs' else words (block ``patterns``)
PHRASE = "q_else"                       # the language packs' question (block ``speech``)
_END = re.compile(r"[.!?\n]")           # the end of the else clause's sentence


def one_condition(goals):
    """(object, place, present) when the goals' ``when`` fields name exactly one condition and all say the same ``present``; else None."""
    seen = set()
    for g in goals or []:
        w = g.get("when") if isinstance(g, dict) else None
        if isinstance(w, dict) and isinstance(w.get("present"), bool) and str(w.get("object") or "").strip() and str(w.get("at") or "").strip():
            seen.add((str(w["object"]).strip(), str(w["at"]).strip(), w["present"]))
    return next(iter(seen)) if len(seen) == 1 else None


def else_clauses(texts, langs) -> list:
    """The words after each else word of the packs of ``langs``, up to the end of its sentence ([] when there is none)."""
    rx = langdata.rx(PATTERN, re.I, langs)
    return [_END.split(t[m.end():], 1)[0] for t in texts if rx and t for m in rx.finditer(t)]


def uncarried(clauses, objects, places, goals, cond, langs) -> list:
    """The catalogue ``objects`` and the map ``places`` (Places) the ``clauses`` name that no goal and not the condition names."""
    carried = [str(g.get(k) or "") for g in goals if isinstance(g, dict) for k in ("object", "to", "from")] + list(cond[:2])
    said = [o for o in objects if any(names.has(o, c, langs) for c in clauses)]
    out = [o for o in said if not any(names.has(o, c, langs) or names.has(c, o, langs) for c in carried if c)]
    keys = [k for k in getattr(places, "keys", None) or [] if any(places.said(k, c) for c in clauses)]
    return out + [k for k in keys if not any(c == k or places.said(k, c) for c in carried if c)]


def question(robot, lang, obj, place):
    """The ``q_else`` phrase as a LocalText (run language, else English), or None when no pack has it."""
    tpl = speech.phrase(robot, PHRASE, lang) or speech.phrase(robot, PHRASE, "en")
    en = speech.phrase(robot, PHRASE, "en") or tpl
    if not tpl:
        return None
    slots = {"object": obj, "place": place}
    try:
        return speech.LocalText(PHRASE, slots, text=en.format(**slots))
    except (KeyError, IndexError, ValueError):
        return None


class ElseAsk(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {})

    def _named(self, run, goals, cond) -> list:
        """What the request's else clauses name and no goal carries (objects of the catalogue, places of the map)."""
        langs = names.langs_of(run)
        texts = [str(getattr(run, "task", "") or ""), str(getattr(self.svc, "request_english", lambda r: "")(run) or "")]
        clauses = else_clauses(texts, langs)
        if not clauses:
            return []
        mem = getattr(getattr(run, "memory", None), "data", None) or {}
        lim = getattr(getattr(self.svc.robot, "manifest", None), "limits", None) or {}
        objects = list(dict.fromkeys(str(n) for n in list(mem.get("objects") or {}) + list(lim.get("objects") or {}) if str(n).strip()))
        return uncarried(clauses, objects, getattr(run, "places", None), goals, cond, langs)

    def on_goals(self, run):
        """Flow: ask once what to do otherwise; the answer's new goals get the other branch's ``when`` (or, read again, the same ones)."""
        goals, st = list(getattr(run, "goals", None) or []), self._state(run)
        cond = one_condition(goals)
        if cond is None:
            return None
        if "asked" in st:
            return (yield from self._again(run, st, goals, cond))
        named = self._named(run, goals, cond)
        q = question(self.svc.robot, getattr(run, "lang", None) or "en", cond[0], cond[1]) if named else None
        if q is None:
            return None
        st.update(asked=True, sig=dialog_converge.goal_sig(goals), cond=cond, added=[])
        ans = self.svc.ask(run, q, vetoed=None)
        if ans is None:                         # an option vetoed it (the question cap): not asked, nothing logged, not tried again
            st["asked"] = False
            return None
        ans = str(ans or "").strip()
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        st["added"] = self._read(run, ans, cond) if ans else []
        self.svc.lab(SPEC.name, object=cond[0], at=cond[1], names=named, added=len(st["added"]))
        return (yield from self._join(run, st["added"]))

    def _read(self, run, ans, cond) -> list:
        """The goals the answer adds (reader, as a change of the request), each with the other branch's ``when``."""
        ch = self.svc.reread_change(run, ans)
        old = set(dialog_converge.goal_sig(list(run.goals) + [g for g in getattr(run, "filtered", None) or [] if isinstance(g, dict)]))
        when = {"object": cond[0], "at": cond[1], "present": not cond[2]}
        return [{**{k: v for k, v in g.items() if k != "when"}, "when": dict(when)}
                for g in (getattr(ch, "added", None) or []) if isinstance(g, dict) and dialog_converge.goal_sig([g])[0] not in old]

    def _join(self, run, added):
        """Flow: the added goals join the run's goals and pass the safety gate again; None when there are none."""
        if not added:
            return None
        run.goals = list(run.goals) + [dict(g, when=dict(g["when"])) for g in added]
        return (yield from self.svc.safety_gate(run))

    def _again(self, run, st, goals, cond):
        """Flow: a later reading that reads as the first one (same goals, same condition) gets the same added goals; nothing is asked."""
        if not st.get("added") or cond != st.get("cond") or dialog_converge.goal_sig(goals) != st.get("sig"):
            return None
        return (yield from self._join(run, st["added"]))


register(SPEC, ElseAsk)
