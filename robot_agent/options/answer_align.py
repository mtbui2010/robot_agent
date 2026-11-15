"""Option ``answer_align`` (default off): a yes or a no to an either-or picks nothing, and a correction changes only the goal its words name.

What: two parts, both matched by the words that tell readings apart (core ``names.distinguishing`` / ``names.says``: the object's name,
the person's own words for it, a word with one declared particle or ending of the language pack):

* ``reask`` (hook ``on_heard``, before the reader): the robot's last question offered options (its declared ``options``, else the clause
  on each side of the pack's ``grammar.sep_or``, said again in the question's own words) and the answer holds a yes or a no word of the pack (``dialogue.yes`` / ``no``) and names
  none of them. The reader would pick one from nothing, so the robot asks ONCE more, naming the options (phrase ``q_pick_one`` of the
  language pack, labelled; no phrase in the run's language: not asked). An "I don't know" is not one (service ``unsure_leftover``),
  nor an answer with another word in another declared script than the options (it may name one in the person's own words).
* ``correction`` (hook ``on_goals``, before the safety gate): the reader read the task again after a correction to the read-back. When
  the correction's words name exactly ONE object of the readings (its distinguishing words against every other object), a goal of
  another object that the new reading sent elsewhere gets its earlier destination back. Two objects named, none, or a result equal to
  the refused reading: nothing is decided.

Why: failure classes ask_loop / wrong_answer_accepted / correction_misapplied: a bare "yes" to "the A or the B?" was read as a choice
(false success), and "no, the <X> on the <Y>" moved the other object too. The read-back's own either-or (option ``ask_agree``) gets the
same matching with its key ``distinct`` (same hook and data there, so a key of that option and not a part of this one).
Switch off: key ``enabled`` False (the default; run option ``answer_align``), or ``reask`` / ``correction`` False. A language with no
yes / no words, no ``sep_or`` and no phrase: nothing is asked; a correction that names no object: nothing changes (fail open).
Lab flag ``answer_align``: ``{rule: reask | reask_no_phrase | correction, ...}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import dialog_converge, langdata, names, speech

SPEC = OptionSpec(
    name="answer_align",
    keys=(Key("enabled", "bool", False, aliases=("answer_align",)), Key("reask", "bool", True), Key("correction", "bool", True)),
    hooks=("on_heard", "on_goals"),
    order=8,
    enabled="enabled",
    doc="A yes or no to an either-or is asked once more naming the options; a correction that names one object leaves the other goals' destinations as they were.",
)
Q_PICK = "q_pick_one"                       # the language pack's phrase (block ``speech``): the re-ask with its ``options`` slot
MOVE = ("on", "in")
DEST = ("type", "to", "hand", "near_person", "user_at")
_CUT = re.compile(r"[:;,.!?]")


def options_of(q, langs) -> tuple:
    """(the options a question offers, the "or" that joins them): its declared ``options`` (two or more; the prompt language's ``sep_or``),
    else the clause on each side of a pack's ``sep_or`` in its words (that ``sep_or``); ([], "") for none."""
    opts = [str(o) for o in (speech.declared_ask(q) or {}).get("options") or [] if str(o).strip()]
    if len(opts) >= 2:
        return opts, langdata.grammar(langdata.prompt_lang(), "sep_or") or speech.CORE_PHRASES["sep_or"]
    if getattr(q, "key", "") in speech.READBACK_KEYS or any(getattr(p, "key", "") in speech.READBACK_KEYS for p in getattr(q, "parts", ())):
        return [], ""
    text = str(q or "")
    for sep in dict.fromkeys(langdata.grammar_all("sep_or", langs)):
        if sep.strip() and sep in text:
            left, right = text.split(sep, 1)
            a, b = _CUT.split(left.rstrip(" ,;"))[-1].strip(), _CUT.split(right)[0].strip()
            if a and b:
                return [a, b], sep
    return [], ""


def yes_or_no(text, langs) -> bool:
    """The answer holds a yes or a no word of the packs (one word, a declared ending allowed, or a phrase of several words)."""
    entries = langdata.dialogue_all("yes", langs) + langdata.dialogue_all("no", langs)
    one = names.words([w for w in entries if " " not in w.strip()], langs)
    norm = " " + " ".join(re.findall(r"\w+", str(text or "").lower())) + " "
    return names.says([text], one, langs) or any(" " + w.strip().lower() + " " in norm for w in entries if " " in w.strip())


def other_script(text, opts, langs) -> bool:
    """A word of the answer that is no yes / no word, filler or polite word of the packs is in another declared script than
    the options (the person named one in their own words, the options are the robot's): no match is possible, nothing is asked."""
    own, skip = langdata.any_script(" ".join(opts)), {w.lower() for k in ("fillers", "polite") for w in langdata.dialogue_all(k, langs)}
    return any(langdata.any_script(w) != own for w in re.findall(r"\w+", str(text or "").lower()) if w not in skip and not yes_or_no(w, langs))


def named_any(text, opts, langs) -> bool:
    """The answer holds a distinguishing word of at least one option (against the other options)."""
    sets = [names.words(o, langs) for o in opts]
    return any(names.says([text], names.distinguishing(s, set().union(*[x for j, x in enumerate(sets) if j != i]), langs), langs)
               for i, s in enumerate(sets))


def _obj(g) -> str:
    return str(g.get("object") or "").strip().lower() if g.get("type") in MOVE else ""


def _dest(g) -> tuple:
    return tuple(g.get(k) for k in DEST)


def realign(prev, now, texts, known, langs):
    """``now`` with the earlier destination back on every goal whose object the correction does not name, when it names exactly one
    object of the readings (``known``: the person's own words per object); None = nothing decided."""
    objs = list(dict.fromkeys(o for o in map(_obj, list(prev) + list(now)) if o))
    i = names.which_named(texts, [names.words(o, langs) | known.get(o, set()) for o in objs], langs) if len(objs) >= 2 else None
    if i is None:
        return None
    before = {}
    for g in prev:
        before.setdefault(_obj(g), []).append(g)
    out = []
    for g in now:
        p = before.get(_obj(g)) or []
        keep = _obj(g) and _obj(g) != objs[i] and len(p) == 1 and _dest(p[0]) != _dest(g)
        out.append({**{k: v for k, v in g.items() if k not in DEST}, **{k: v for k, v in p[0].items() if k in DEST}} if keep else g)
    if out == list(now) or dialog_converge.goal_sig(out) == dialog_converge.goal_sig(prev):
        return None
    return out


class AnswerAlign(Option):
    spec = SPEC

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {})

    def on_heard(self, run):
        """A yes or a no to an either-or: ask once more naming the options (the loop reads the next round); else None."""
        q = self._reask(run) if self.cfg["reask"] else None
        if q is None:
            return None
        yield run.ev("understand_check", issues=["a yes or no to an either-or"], reask=True)
        return (yield from self.svc.ask_clarify(run, q))

    def _reask(self, run):
        st, dlg, langs = self._state(run), list(getattr(run, "dialogue", None) or []), names.langs_of(run)
        if st.get("reasked") or len(dlg) < 2 or dlg[-1][0] != "user" or dlg[-2][0] != "robot":
            return None
        q, ans = dlg[-2][1], str(dlg[-1][1] or "")
        (opts, sep), unsure = options_of(q, langs), getattr(self.svc, "unsure_leftover", None)
        if (len(opts) < 2 or not yes_or_no(ans, langs) or named_any(ans, opts, langs) or other_script(ans, opts, langs)
                or (unsure and unsure(run, ans) is not None)):
            return None
        robot, lang = getattr(self.svc, "robot", None), getattr(run, "lang", None) or "en"
        if speech.phrase(robot, Q_PICK, lang) is None:
            self.svc.lab(SPEC.name, rule="reask_no_phrase")
            return None
        st["reasked"] = True
        self.svc.lab(SPEC.name, rule="reask", options=len(opts))
        slots = {"text": sep.join(opts)}                       # in the question's own words (a slot not in the run's script is translated)
        en = speech.render(speech.LocalText(Q_PICK, slots, text=""), "en", robot) or ""
        return speech.LocalText(Q_PICK, slots, text=en, ask={"intent": "choose", "options": opts, "about": ""})

    def on_goals(self, run):
        """After a correction to the read-back, the goals of the objects the correction does not name keep their earlier destination."""
        st, goals, langs = self._state(run), list(getattr(run, "goals", None) or []), names.langs_of(run)
        known = st.setdefault("known", {})
        for g in goals:
            if _obj(g):
                known.setdefault(_obj(g), set()).update(names.words([str(g.get("said") or ""), str(g.get("name_local") or "")], langs))
        n, dlg = int(getattr(run, "corrected", 0) or 0), list(getattr(run, "dialogue", None) or [])
        if self.cfg["correction"] and st.get("prev") and n > st.get("seen", 0) and dlg and dlg[-1][0] == "user":
            new = realign(st["prev"], goals, [str(dlg[-1][1])], known, langs)
            if new is not None:
                self.svc.lab(SPEC.name, rule="correction", kept=sum(a is not b for a, b in zip(new, goals)))
                run.goals = goals = new
        if goals:
            st["prev"] = [dict(g) for g in goals]
        st["seen"] = n
        yield from ()
        return None


register(SPEC, AnswerAlign)
