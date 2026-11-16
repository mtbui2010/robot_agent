"""Option ``ask_next`` (default off): after a failed Find, Pick or MoveTo used its own retries, ask the person what to do next, and do it.

What: hook ``on_ask_help`` (the engine is about to put its own help question). One menu sentence of the language pack (``q_ask_next_find`` /
``_pick`` / ``_move``): try again here, look somewhere else, or stop. ``parse_answer`` reads the answer with the pack's declared words only
(block ``ask_next``, ``dialogue``, ``answer_unsure``) and the ENV place it names. retry: the step once more in place; elsewhere: the named place
(a room: its best place not searched yet), else Find = the engine's own "Where is it?", Pick = one ``q_ask_next_place``; anywhere (Find): the
engine's search goes on; stop: the run ends ``aborted`` saying ``say_stopped``; unclear: asked once more naming the choices, then the engine's path.
Why: the engine read only a yes or a place, so "no", "give up", "not sure" or silence ended the run or replanned blindly (docs/ask_next.md).
Fail open: no ask skill, no labelled ``ask_next`` words or no phrase in the run's language, silence, a cap reached (``max_menus`` per run,
one per object and kind): the engine's own question, as when off.
Switch off: key ``enabled`` False (the default; run option ``ask_next``). Event: lab flag ``ask_next`` ``{kind, object, answer, place}``.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from robot_agent.core.options import NEXT, STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import langdata, speech
from robot_agent.core.planning.pack_data import labelled

SPEC = OptionSpec(
    name="ask_next",
    keys=(Key("enabled", "bool", False, aliases=("ask_next",)), Key("max_menus", "int", 2)),
    hooks=("on_ask_help",),
    order=40,
    enabled="enabled",
    doc="After a failed find, pick or move used its retries: ask the person (try again here, look somewhere else, stop) and do what they say.",
)
CHOICES = ("retry", "elsewhere", "anywhere", "stop")                  # the word lists of the pack block ``ask_next``
MENU = {"where": "q_ask_next_find", "pick": "q_ask_next_pick", "move": "q_ask_next_move"}
AGAIN, WHERE = {"move": "q_ask_next_again_move"}, "q_ask_next_place"


class Answer(NamedTuple):
    kind: str                                  # retry | elsewhere | stop | unclear
    place: Optional[str] = None                # elsewhere: the ENV place named, or None
    anywhere: bool = False                     # elsewhere: "anywhere" (the robot chooses)


def fold(text) -> str:
    """``text`` in lower case, words only."""
    return " ".join(re.findall(r"\w+(?:['’]\w+)*", str(text or "").casefold()))


def says(text: str, words) -> bool:
    """Does the folded ``text`` hold one of ``words`` as whole words?"""
    return any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text) for w in map(fold, words or ()) if w)


def _strs(v) -> list:
    return [x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def pack_of(lang) -> dict:
    """The words the answers of ``lang`` are read with; {} when its pack has no labelled ``ask_next`` block with words (the option does nothing)."""
    own = langdata.block(lang, "ask_next")
    if not labelled(own) or not any(_strs(own.get(k)) for k in CHOICES):
        return {}
    dlg = langdata.block(lang, "dialogue")
    pack = {k: _strs(own.get(k)) for k in CHOICES}
    pack["stop"] += _strs(dlg.get("stop")) + _strs(dlg.get("stop_prefix"))      # the run's own stop words of the language
    pack.update(yes=_strs(dlg.get("yes")), no=_strs(dlg.get("no")), unsure=langdata.phrases(lang, "answer_unsure"))
    return pack


def parse_answer(text, pack: dict, place: Optional[str] = None) -> Answer:
    """What the answer to the menu asks for (pure). ``place``: the ENV place the answer names (the caller grounds it). A stop word wins; a
    place, then "anywhere", "elsewhere", "again"; an "I don't know" is unclear; a plain no is a stop, a plain yes the first choice (again)."""
    t = fold(text)
    if not t or not pack:
        return Answer("unclear")
    if says(t, pack.get("stop")):
        return Answer("stop")
    if place:
        return Answer("elsewhere", place)
    for kind, ws in (("anywhere", "anywhere"), ("elsewhere", "elsewhere"), ("retry", "retry"), ("unclear", "unsure"), ("stop", "no"), ("retry", "yes")):
        if says(t, pack.get(ws)):
            return Answer("elsewhere", anywhere=True) if kind == "anywhere" else Answer(kind)
    return Answer("unclear")


class AskNext(Option):
    spec = SPEC

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {"menus": 0, "asked": set(), "stopped": False})

    def on_ask_help(self, run, st, kind):
        """Flow: the menu for the failed step ``st`` and what the answer decides: (outcome, replan hint); None = the engine's own question."""
        obj, s, lang = st.get("object"), self._state(run), getattr(run, "lang", None) or "en"
        pack, q = pack_of(lang), self._phrase(run, MENU.get(kind), obj)
        if (not pack or q is None or not self._can_ask() or s["stopped"] or (kind, obj) in s["asked"]
                or s["menus"] >= int(self.cfg.get("max_menus") or 0)):
            return None
        s["menus"] += 1
        s["asked"].add((kind, obj))
        ans = yield from self._ask(run, q)
        a = Answer("unclear") if ans is None else self._read(run, ans, pack, obj, kind)
        again = self._phrase(run, AGAIN.get(kind, "q_ask_next_again"), obj)
        if ans and a.kind == "unclear" and again is not None:                 # once more, naming the choices (silence is not asked again)
            ans = yield from self._ask(run, again)
            a = Answer("unclear") if ans is None else self._read(run, ans, pack, obj, kind)
        self.svc.lab(SPEC.name, kind=kind, object=obj, answer=a.kind, place=a.place)
        if ans is None:
            return STOP, ""                                                   # the run was cancelled while asking
        return (yield from self._act(run, st, kind, a, (q, ans)))

    def _read(self, run, ans, pack, obj, kind) -> Answer:
        """The answer read; a MoveTo offers no other place (an answer that names one is unclear)."""
        a = parse_answer(ans, pack, self._place(run, ans, obj))
        return Answer("unclear") if kind == "move" and a.kind == "elsewhere" else a

    def _act(self, run, st, kind, a: Answer, said):
        """Flow: do what the answer asks for: (outcome, replan hint) or None (the engine's own path)."""
        obj = st.get("object")
        if a.kind == "stop":
            self._state(run)["stopped"] = True
            yield self.svc.finish(run, "aborted", msg=f"{SPEC.name}: the person said stop after {kind} {obj} failed",
                                  say=speech.LocalText("say_stopped"))
            return STOP, ""
        if a.kind == "retry":
            self.svc.retry_here(run, st)
            yield run.ev("recover", at_index=run.i, kind=SPEC.name, msg=f"{kind} {obj} again, as the person said")
            return NEXT, ""
        if a.kind != "elsewhere":
            return None
        if a.place:
            return self._told(run, obj, a.place, said)
        if a.anywhere and kind == "where":
            return (yield from self.svc.search_on(run, obj))
        return (yield from self._where(run, obj)) if kind == "pick" else None   # Find: the engine's own "Where is it?" follows

    def _where(self, run, obj):
        """Flow: a Pick to be tried elsewhere with no place said: one "where should I look?"; a place = told, else the engine's path."""
        q = self._phrase(run, WHERE, obj)
        if q is None:
            return None
        ans = yield from self._ask(run, q)
        if ans is None:
            return STOP, ""
        k = self._place(run, ans, obj)
        return self._told(run, obj, k, (q, ans)) if k else None

    def _told(self, run, obj, place, said):
        """The person named ``place``: remembered and trusted by the search as the engine does; the replan goes there."""
        self.svc.note_answer(run, said[0], said[1], obj, place)
        return None, self.svc.told_at(run, obj, place)

    def _place(self, run, ans, obj) -> Optional[str]:
        """The ENV place the answer names (a room: its best place not searched yet), never the place the robot is at; None."""
        k = self.svc.ground_place(run, ans)
        if not k:
            _room, keys = self.svc.room_places(run, ans)
            k = self.svc.first_unsearched(run, obj, keys) if keys else None
        return k if k and k != getattr(getattr(run, "world", None), "arrived", None) else None

    def _ask(self, run, q):
        """Flow: put ``q`` (a clarify event, the engine's ``ask``): the answer ('' on silence), None when the run was cancelled meanwhile."""
        yield run.ev("clarify", question=q, round=0, kind=SPEC.name)
        ans = self.svc.ask(run, q) or ""
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return None
        return ans

    def _phrase(self, run, key, obj):
        """The pack's phrase ``key`` with the object (the person's own words) as a LocalText; None when the run's language has none."""
        lang = getattr(run, "lang", None) or "en"
        tpl = speech.phrase(self.svc.robot, key, lang) if key else None
        if not tpl:
            return None
        slots = {"object": self.svc.said_of(run, obj)}
        en = speech.phrase(self.svc.robot, key, "en") or tpl
        try:
            return speech.LocalText(key, slots, text=en.format(**slots))
        except (KeyError, IndexError, ValueError):
            return None

    def _can_ask(self) -> bool:
        """Can the person answer at all (the robot has an Ask skill)?"""
        spec = getattr(self.svc.robot, "ask_spec", None)
        return callable(spec) and spec() is not None


register(SPEC, AskNext)
