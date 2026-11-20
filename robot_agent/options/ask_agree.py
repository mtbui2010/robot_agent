"""Option ``ask_agree`` (default off): the robot does not loop on a read-back, and does not accept an answer that contradicts what the person said.

Three classes of the read-back dialogue (``AssistLoop._read_back`` asks "Is that right?" and the person answers):

* **A bare "no" followed by the same request.** The reading was right, the first answer was noise or a slip, and the person says the request again; it reads
  the same goals as the refused reading. The engine then asks "which part is wrong?" and stops on the second time. With the option the reading is read back
  ONE more time as a yes / no question (hook ``on_goals`` sets the run's ``allow_repeat``); only a yes acts. Silence or another no stops as before. Once per
  run, only after a refusal that was a bare no (no word of correction), only while the loop would read back once more (service ``readback_left``: the
  loop's own cap with ``confirm_limit``; a host without it: never). A restatement alone is no agreement (``dialog_agree``), so the person is asked again.
* **An "I don't know" that keeps a reading it should not** (service ``unrefused``, before ``ask_focus``). It is no refusal only in a clean case: the answer
  says nothing but "not sure" (its words: fillers, first-person words, whole words of the pack's phrases, or such a word with one declared ending), the
  read-back did not carry the robot's own doubt, and no later answer changed the reading since it was first read back. Otherwise it is a refusal (the
  engine's correction path). A leftover word that is none of these stays a refusal (fail closed). A language with no data does nothing.
* **The either-or.** When the reading changed exactly one value (an object or a place of one goal) since it was first read back, and the person had said the
  earlier value, an "I don't know" asks once more, naming both readings (a declared choice). The answer that names one reading decides: the current one
  stands, the earlier one is read back again (``run.patched``, ``allow_repeat``). A plain yes or no names neither: nothing is picked and the answer is a refusal.
  The question is the phrase ``q_which_reading`` (the core English; another language: the robot's i18n or the language pack's ``speech`` block); with
  no text in the run's language it is not asked. The question is asked from the read-back's query service: its ``clarify`` event goes through the service ``emit``, the turn is counted in ``run.asked``
  (service ``ask``) and a run stopped meanwhile decides nothing.

Pack data: the language pack's ``dialogue.fillers`` / ``no`` / ``polite``, ``words.first_person``, ``answer_unsure`` and ``dialogue_ext.answer_unsure`` (both
places), ``dialogue_ext.polite_endings`` and ``patterns.answer_tail`` (the endings). No data: nothing is claimed, the engine's path stands.
Key ``distinct`` (off; run option ``ask_agree_distinct``, D4 layer ``answer_align``): the earlier value counts as said, and the answer to the
either-or as naming a reading, by the words that tell the two readings apart (``names.distinguishing``: the object's name, the person's own words
for it, its places, the speaker's own word when it goes to them), a word with one declared particle or ending of the pack; one word of the name
is enough ("the tin" for "tin box"), a word both readings share names neither (with the key, "jar" said is no earlier "jar" against a current
"teal jar"); an object that changed with its own ``from`` and words
(``FOLLOW``) is one change, and the earlier object comes back with them. Off: the whole value as a phrase, one value changed, as before.
Hooks ``on_goals``, ``on_issues``; service ``unrefused`` (order 78, before ``ask_focus``). Keys: ``enabled``, ``restate_confirm`` (true), ``either_or`` (true), ``distinct`` (false).
Lab flag ``ask_agree``: ``{rule, ...}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import dialog_agree, dialog_converge, langdata, names, persons, speech
from robot_agent.core.planning.understand import place_mentions

SPEC = OptionSpec(
    name="ask_agree",
    keys=(Key("enabled", "bool", False, aliases=("ask_agree",)), Key("restate_confirm", "bool", True), Key("either_or", "bool", True),
          Key("distinct", "bool", False, aliases=("ask_agree_distinct",))),
    hooks=("on_goals", "on_issues"),
    order=78,
    enabled="enabled",
    services=("unrefused",),
    doc="Read-back dialogue: one more yes / no after a bare no and the same request; an \"I don't know\" keeps only a clean reading; an either-or when it changed a value.",
)
VALUE_KEYS = ("object", "to", "from")
FOLLOW = ("from", "said", "name_local")   # values that go with the object (where it is, the person's words for it): key ``distinct``
Q_WHICH = "q_which_reading"          # the phrase of the either-or (speech.CORE_PHRASES; another language: the robot's i18n or the language pack's ``speech``)


def fold(text) -> str:
    return " ".join(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", str(text or "").casefold()))


def _list(lang, block, key) -> list:
    v = langdata.block(lang, block).get(key)
    return [str(x) for x in v if isinstance(x, str) and x] if isinstance(v, (list, tuple)) else []


def _unsure_words(langs) -> set:
    """The whole words of every "I don't know" phrase of the packs: the top-level key and ``dialogue_ext`` (the language puts them in either)."""
    out = set()
    for lg in langs:
        for p in langdata.phrases(lg, "answer_unsure") + _list(lg, "dialogue_ext", "answer_unsure"):
            out.update(fold(p).split())
    return out


class Words:
    """What one word of the leftover of an "I don't know" may be, from the language packs of ``langs``."""

    def __init__(self, langs):
        self.ok = {fold(w) for w in langdata.dialogue_all("fillers", langs)} | _unsure_words(langs)
        self.no = {fold(w) for w in langdata.dialogue_all("no", langs)}
        self.me = [re.compile(p) for lg in langs for p in langdata.words(lg, "first_person")]
        ends = [e for lg in langs for e in _list(lg, "dialogue_ext", "polite_endings")]
        self.ends = sorted({fold(e) for e in ends if fold(e)}, key=len, reverse=True)
        self.tail = langdata.rx("answer_tail", langs=langs)

    def plain(self, tok) -> bool:
        return tok in self.ok or any(m.search(tok) for m in self.me)

    def accepts(self, tok) -> bool:
        """A filler, a first-person word, a word of an unsure phrase, or one of them with ONE declared ending (or the ending alone); never a no."""
        if tok in self.no:
            return False
        if self.plain(tok):
            return True
        stems = [tok[:-len(e)] for e in self.ends if tok.endswith(e)]
        if self.tail is not None and self.tail.search(tok):
            stems.append(self.tail.sub("", tok, count=1))
        return any(not s or self.plain(s) for s in stems)


def clean_leftover(left, langs) -> bool:
    """The words left of an "I don't know" answer once its phrases are taken out say nothing else (fail closed on any other word)."""
    w = Words(langs)
    return all(w.accepts(t) for t in fold(left).split())


def bare_no(text, langs) -> bool:
    """The answer is a no and nothing else: its words are no-words, politeness or fillers (and at least one no-word)."""
    toks = fold(text).split()
    no = {fold(w) for w in langdata.dialogue_all("no", langs)}
    rest = no | {fold(w) for w in langdata.dialogue_all("polite", langs) + langdata.dialogue_all("fillers", langs)}
    return bool(toks) and any(t in no for t in toks) and all(t in rest for t in toks)


def one_change(first, now):
    """(index, key) when ``now`` differs from ``first`` in exactly one value (object, to, from) of one goal; else None."""
    if len(first) != len(now) or any(a.get("type") != b.get("type") for a, b in zip(first, now)):
        return None
    diffs = [(i, k) for i, (a, b) in enumerate(zip(first, now)) for k in set(a) | set(b) if a.get(k) != b.get(k)]
    return diffs[0] if len(diffs) == 1 and diffs[0][1] in VALUE_KEYS else None


def object_change(first, now):
    """(index, "object") when ``now`` differs from ``first`` in the object of one goal and in nothing else but the values that go
    with an object (``FOLLOW``: a new object has its own place and words); else ``one_change``."""
    bare = [[{k: v for k, v in g.items() if k not in FOLLOW} if a.get("object") != b.get("object") else g for g in (a, b)]
            for a, b in zip(first, now)] if len(first) == len(now) else []
    ch = one_change([x for x, _ in bare], [y for _, y in bare]) if bare else None
    return ch if ch and ch[1] == "object" else one_change(first, now)


def said(texts, value, places) -> bool:
    """The person's words name ``value``: as whole words, or as a place of the ENV (``place_mentions``)."""
    v = str(value or "").strip().lower()
    if not v:
        return False
    if any(re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", " ".join(t.lower().split())) for t in texts):
        return True
    return any(value in keys for t in texts for _s, _e, _p, keys, _src in place_mentions(t, places))


def _st(run) -> dict:
    return run.__dict__.setdefault("opt", {}).setdefault("ask_agree", {"first": None, "bare": set(), "content": set(), "used": False, "asked": False})


class AskAgree(Option):
    spec = SPEC

    def on_goals(self, run):
        """Record the first reading; after a bare no, the same reading from the person's restatement is read back once more (``allow_repeat``)."""
        goals = getattr(run, "goals", None) or []
        st = _st(run)
        if goals and st["first"] is None:
            st["first"] = [dict(g) for g in goals]
        sig, left = dialog_converge.goal_sig(goals) if goals else None, getattr(self.svc, "readback_left", None)
        if (self.cfg["restate_confirm"] and sig in st["bare"] and sig not in st["content"] and not st["used"]
                and sig in getattr(run, "rejected", ()) and left is not None and left(run)):
            st["used"], run.allow_repeat = True, True
            self.svc.lab(SPEC.name, rule="restated_after_bare_no")
        yield from ()
        return None

    def on_issues(self, issues, run, goals):
        """The place the person gave up in the either-or is no "you mentioned X" question (its name is still in the earlier answer)."""
        gone = _st(run).get("gave_up")
        if not gone:
            return None
        keep = [i for i in issues if not (i.get("kind") == "place" and set(i.get("keys") or ()) & gone)]
        return keep if len(keep) != len(issues) else None

    def unrefused(self, run, ans):
        """False: the answer refuses the reading (the engine's path); True: an "I don't know" that leaves a clean reading standing; None: not mine."""
        st, langs = _st(run), names.langs_of(run)
        readings = list(getattr(run, "readings", None) or [])
        sig = readings[-1] if readings else None
        left_of = getattr(self.svc, "unsure_leftover", None)
        left = left_of(run, ans) if left_of and ans else None
        if left is None:                                          # not an "I don't know": a bare no or words
            (st["bare"] if bare_no(ans, langs) else st["content"]).add(sig)
            return None
        if not clean_leftover(left, langs):
            st["content"].add(sig)
            self.svc.lab(SPEC.name, rule="unsure_with_more")
            return False
        if getattr(run, "unsure", None):                          # the read-back said the robot's own doubt
            self.svc.lab(SPEC.name, rule="veto_own_doubt")
            return False
        if len(set(readings)) > 1:                                # an answer changed the reading since it was first read back
            return self._changed(run, st)
        self.svc.lab(SPEC.name, rule="unsure_is_no_refusal")
        return True

    def _changed(self, run, st):
        """The reading changed since its first read-back: False (a refusal), or the either-or when one value the person said earlier was changed."""
        self.svc.lab(SPEC.name, rule="veto_changed")
        change = object_change if self.cfg["distinct"] else one_change
        ch = change(st["first"] or [], run.goals) if self.cfg["either_or"] and not st["asked"] else None
        ask, phrase = getattr(self.svc, "ask", None), getattr(self.svc, "goal_phrase", None)
        if ch is None or not ask or not phrase:
            return False
        i, k = ch
        old, new = st["first"][i], run.goals[i]
        if not self._said(run, old, new, k):
            return False
        if speech.phrase(getattr(self.svc, "robot", None), Q_WHICH, getattr(run, "lang", None) or "en") is None:
            self.svc.lab(SPEC.name, rule="either_or_no_phrase")          # no text in the run's language: not asked (no translation call)
            return False
        st["asked"] = True
        a, b = phrase(old, run.places), phrase(new, run.places)
        q = speech.LocalText(Q_WHICH, {"first": a, "second": b}, ask={"intent": "choose", "options": [str(a), str(b)], "about": ""})
        ans = self._ask_either(run, q)
        if ans is None:                                       # the run was stopped while asking: nothing is decided
            return False
        run.dialogue += [("robot", q), ("user", ans)]
        named = self._picked(run, ans, (old, new), (str(a), str(b)))
        self.svc.lab(SPEC.name, rule="either_or", picked=len(named) == 1)
        if named == [1]:
            return True
        if named == [0]:
            run.patched = [dict(g) if j != i else self._back(g, old, k) for j, g in enumerate(run.goals)]
            run.allow_repeat = True
            if k != "object" and new.get(k):                  # the place the person gave up is no coverage doubt any more (``on_issues``)
                st.setdefault("gave_up", set()).add(new[k])
        return False

    def _back(self, g, old, k) -> dict:
        """Goal ``g`` with the earlier value of ``k``; with ``distinct`` an earlier object brings its own ``FOLLOW`` values back."""
        if k != "object" or not self.cfg["distinct"]:
            return {**g, k: old.get(k)}
        return {**{f: v for f, v in g.items() if f not in FOLLOW}, k: old.get(k), **{f: old[f] for f in FOLLOW if f in old}}

    def _said(self, run, old, new, k) -> bool:
        """The person said the earlier value: as a whole phrase, or (key ``distinct``) a word that tells the earlier reading from the
        current one (``names.distinguishing``: the object's name, the person's own words for it, its places, the speaker); with the key
        a phrase made only of words the current reading shares ("jar" against "teal jar") says nothing."""
        texts = [run.task] + [t for w, t in run.dialogue if w == "user"]
        if not self.cfg["distinct"]:
            return said(texts, old.get(k), run.places)
        langs, sk = names.langs_of(run), persons.speaker_key(run.places)
        a, b = (names.reading_words(g, run.places, langs, sk) for g in (old, new))
        return names.says(texts, names.distinguishing(a, b, langs), langs)

    def _picked(self, run, ans, goals, phrases) -> list:
        """[i]: the answer names reading i of (earlier, current) alone (the phrases as said; with ``distinct`` the readings' own
        distinguishing words when the phrases name none); [] / two: nothing is picked (a plain yes or no names neither)."""
        if not ans:
            return []
        named = [i for i, o in enumerate(phrases) if dialog_agree.names_option([ans], o, list(phrases))]
        if named or not self.cfg["distinct"]:
            return named
        langs, sk = names.langs_of(run), persons.speaker_key(run.places)
        i = names.which_named([ans], [names.reading_words(g, run.places, langs, sk) for g in goals], langs)
        return [] if i is None else [i]

    def _ask_either(self, run, q):
        """Ask the either-or from the read-back's query service (it cannot yield): its ``clarify`` event through the service ``emit`` (logged
        with the run's events, pushed live when the host has a channel), the turn counted by ``ask`` (``run.asked``); None when the run was
        stopped meanwhile."""
        emit = getattr(self.svc, "emit", None)
        if emit is not None:
            emit(run, "clarify", question=q, round=getattr(run, "asked", 0) + 1, kind=SPEC.name)
        ans = self.svc.ask(run, q)
        cancelled = getattr(self.svc, "cancelled", None)
        if cancelled is not None and cancelled(run):
            self.svc.lab(SPEC.name, rule="either_or_stopped")
            return None
        return ans


register(SPEC, AskAgree)
