"""Option ``cond_goal`` (default off): a request with a condition ("if the spoon is on the table, put it away") is read with a ``when`` field
and the condition is looked at by the robot, not guessed by the reader.

The understand schema gets an OPTIONAL object ``when`` {object, at, present} on a goal, after ``content`` (the key order matters for the
backend grammar, docs/home/bench/lora.md "The JSON key order": the goals without a condition are written as before). It means: do this
goal only if ``object`` is (``present`` true) or is not (false) at ``at``. Before the read-back the robot goes to ``at``, looks (up to
``looks`` times), keeps the goals whose ``present`` equals what it saw, drops the others (listed in ``run.filtered``) and removes the
``when`` field. Unsure (the two looks differ, or the object memory put it there but the look did not see it, or the robot cannot go and look): asks
the person once, if the pack has the question. A yes or no decides. Any other answer, no answer, no question, or a place the robot does not know
even after asking is a third state, UNKNOWN: no branch is kept, none is dropped, the run ends as failed with the question said (never a guess).
Service ``refused_goals`` answers with the goals this option dropped, and ``goal_dropped`` says whether a goal equals one of them, so the engine's
read-back counts them as covered and a re-sampled reading does not bring them back.

Generic: no object, place or word of a language. All the texts are pack data (manifest ``planner.options.cond_goal.<key>``, localised by the
phrase key like every robot phrase): ``prompt`` (the lines added to the understand prompt before ``Task:``), ``q_where`` (``cond_where``,
slot ``{object}``), ``q_unsure`` (``cond_unsure``, slots ``{object}``, ``{place}``), ``say_seen`` (``cond_seen``), ``say_not_seen``
(``cond_not_seen``). The texts are used only when the section (or the run options) carries ``source`` (a string) or ``assumed: true``; unlabelled
texts count as none. A text the loop resolved empty is read again from the manifest at run time. No ``prompt`` = the option changes neither the
schema nor the prompt; an empty question = not asked; an empty sentence = nothing said. Needs the pack's action role ``move`` (none = the
condition is read as absent). Use it with a reader trained on ``when``.

Keys of layer cond_answer (docs/options.md): ``answer``, ``open_closed``, ``confirm_none``, ``clauses``. Switch off: every key is off by default.
Hooks: ``on_understand_schema``, ``on_understand_prompt``, ``on_goals``. Keys: ``enabled`` (legacy ``cond_goal``), ``looks`` (2),
``ask_when_unsure`` (true), ``prompt``, ``q_where``, ``q_unsure``, ``say_seen``, ``say_not_seen``, ``source``, ``assumed``. Lab flag ``cond_goal``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import clauses, pack_data

SPEC = OptionSpec(
    name="cond_goal",
    keys=(Key("enabled", "bool", False, aliases=("cond_goal",)),
          Key("looks", "int", 2),
          Key("ask_when_unsure", "bool", True),
          Key("prompt", "str", ""),
          Key("q_where", "str", ""),
          Key("q_unsure", "str", ""),
          Key("say_seen", "str", ""),
          Key("say_not_seen", "str", ""),
          Key("source", "str", ""),
          Key("assumed", "bool", False),
          Key("answer", "bool", False),
          Key("open_closed", "bool", False),
          Key("confirm_none", "bool", False),
          Key("clauses", "dict", {}, sources=("manifest",))),
    hooks=("on_understand_schema", "on_understand_prompt", "on_goals"),
    services=("refused_goals", "goal_dropped"),
    order=20,
    enabled="enabled",
    doc="A goal with a condition (when: object at place, present or not) is decided by the robot looking at the place, not by the reader.",
)

_MARK = "\nTask: {task}"
_WHEN = {"type": "object",
         "properties": {"object": {"type": "string"}, "at": {"type": "string"}, "present": {"type": "boolean"}},
         "required": ["object", "at", "present"]}


_UNKNOWN = "unknown"          # neither present nor absent: the robot could not tell and the person did not say
_GUESS = "guess"              # how a decision was made when no look could make it (a closed place, an answer with no yes or no): key confirm_none


def valid_when(g):
    """The goal's ``when`` as {object, at, present} when it is complete (two non-empty strings and a boolean), else None."""
    w = g.get("when") if isinstance(g, dict) else None
    if not isinstance(w, dict) or not isinstance(w.get("present"), bool):
        return None
    o, at = str(w.get("object") or "").strip(), str(w.get("at") or "").strip()
    return {"object": o, "at": at, "present": w["present"]} if o and at else None


def without_when(g) -> dict:
    return {k: v for k, v in g.items() if k != "when"}


def ask_when(g):
    """The goal's ``when`` as {object, at: "", present, ask} when it holds a yes / no question ``ask`` (a condition no look settles), else None."""
    w = g.get("when") if isinstance(g, dict) else None
    q = str(w.get("ask") or "").strip() if isinstance(w, dict) else ""
    return {"object": str(w.get("object") or q), "at": "", "present": w["present"], "ask": q} if q and isinstance(w.get("present"), bool) else None


def answer_holds(yes: bool, no: bool, negated: bool, named, place, unsure: bool):
    """Pure: the condition by the person's own answer to "is the X at P?": True, False or _UNKNOWN (never a default). An "I don't know":
    unknown. A negation ("it is not there"): False. A yes: True, a no: False, unless it names a place that says the opposite (unknown).
    No yes / no word: the place it names decides (P: True, another place: False); none named: unknown."""
    if unsure:
        return _UNKNOWN
    if negated:
        return False
    if yes or no:
        return _UNKNOWN if named is not None and (named == place) != yes else yes
    return _UNKNOWN if named is None else named == place


def closed_openable(entry, rule_says: bool, opened, key) -> bool:
    """Pure: ``key`` is a place the robot declares openable (its ENV entry has an ``openable`` field and the robot's rule says yes) and the
    belief holds it closed (not in ``opened``)."""
    return isinstance(entry, dict) and "openable" in entry and bool(rule_says) and key not in set(opened or ())


def split(goals, done, when_of) -> tuple:
    """Pure: (kept, dropped) goals, ``when`` removed: a goal with a condition (``when_of``: its {key, present} or None) is kept when the
    decision ``done[key]`` equals its ``present``."""
    kept, dropped = [], []
    for g in goals:
        w = when_of(g)
        (kept if w is None or done.get(w["key"]) == w["present"] else dropped).append(without_when(g))
    return kept, dropped


class CondGoal(Option):
    def _text(self, key) -> str:
        """The pack text ``key``: only when labelled (``source`` / ``assumed``), read again from the manifest when the resolved key is empty."""
        return str(pack_data.labelled_keys(SPEC, self.cfg, pack_data.planner_of(self.svc), (key,))[key] or "")

    # ── what the reader is asked for ─────────────────────────────────────
    def on_understand_schema(self, schema):
        """``schema`` with the optional ``when`` after the goal's last property; None (unchanged) without pack prompt text."""
        items = ((schema.get("properties") or {}).get("goals") or {}).get("items")
        if not self._text("prompt").strip() or not isinstance(items, dict) or "properties" not in items:
            return None
        items["properties"]["when"] = {**_WHEN, "properties": dict(_WHEN["properties"])}
        return schema

    def on_understand_prompt(self, text):
        """The prompt template with the pack's lines before ``Task:`` (braces escaped: the template is formatted next)."""
        extra = self._text("prompt").strip()
        if not extra or _MARK not in text:
            return None
        return text.replace(_MARK, "\n" + extra.replace("{", "{{").replace("}", "}}") + "\n" + _MARK, 1)

    # ── what the robot does with it ──────────────────────────────────────
    def _phrase(self, key, text, **slots):
        """The pack's sentence as a localisable phrase; None when the pack has none."""
        from robot_agent.core.planning import speech
        try:
            en = text.format(**slots) if text else ""
        except (KeyError, IndexError, ValueError):
            en = ""
        return speech.LocalText(key, slots, text=en) if en else None

    def _ask(self, run, key, text, **slots):
        """One question to the person (a LocalText the pack declares): their answer, '' when there is no question or no answer."""
        q = self._phrase(key, text, **slots)
        if q is None or not self.cfg["ask_when_unsure"]:
            return ""
        return self.svc.ask(run, q) or ""

    def _go_and_look(self, run, obj, place):
        """The sightings of ``obj`` at ``place`` (a list of bool, at most ``looks``); None when the robot cannot go there."""
        move = self.svc.role("move")
        if not move:
            return None
        if getattr(run.world, "arrived", None) != place:
            if not getattr(self.svc.run_step(run, {"action": move, "object": place}), "ok", False):
                return None
        if self.cfg["open_closed"]:
            self._open_first(run, place)
        seen = []
        for _ in range(max(1, int(self.cfg["looks"]))):
            seen.append(bool(self.svc.look_for(run, obj)[0]))
            if seen[-1]:
                break
        return seen

    def _unsure(self, run, obj, place, seen) -> bool:
        """The looks do not settle it: a miss then a sighting, or a trusted memory of ``obj`` at ``place`` that no look saw."""
        return seen == [False, True] or (not any(seen) and self.svc.remembered(run, obj) == place)

    def _blind(self, run, place) -> bool:
        """The look cannot see inside ``place``: the ENV declares it openable and the belief holds it closed."""
        places = getattr(run, "places", None)
        entry = (getattr(places, "env", None) or {}).get(place) if places is not None else None
        return closed_openable(entry, entry is not None and places.openable(place), getattr(run.world, "opened", None), place)

    def _open_first(self, run, place) -> None:
        """Key ``open_closed``: a declared openable place the belief holds closed is opened (role ``open``) before the look; once open it
        counts as opened just now for the run (service ``opened_now``), so a later miss inside is not doubted as a stale open belief."""
        open_ = self.svc.role("open")
        if open_ and self._blind(run, place):
            ok = getattr(self.svc.run_step(run, {"action": open_, "object": place}), "ok", False)
            if ok:
                getattr(self.svc, "opened_now", lambda r, k: None)(run, place)
            self.svc.lab("cond_goal", part="open", place=place, ok=bool(ok))

    def _read(self, run, ans, place):
        """Key ``answer``: the answer read by itself (``answer_holds``): True, False or _UNKNOWN; ``place`` None: a yes / no question."""
        from robot_agent.core.run_control import yes_no
        t = str(ans or "").strip()
        if not t:
            return _UNKNOWN
        ok, rest = yes_no(t)
        cl = clauses.build(getattr(run, "lang", None), pack_data.fresh(SPEC, self.cfg, pack_data.planner_of(self.svc), "clauses"))
        neg = cl.neg if cl is not None else None
        named = self.svc.ground_place(run, t) if place else None
        unsure = bool(getattr(self.svc, "is_unsure", lambda r, x: False)(run, t))
        return answer_holds(ok is True, ok is False and rest != t, bool(neg and neg.search(t)), named, place, unsure)

    def _asked(self, run, obj, place) -> tuple:
        """(holds, how): the ``q_unsure`` answer; with ``answer`` read by itself, else as before (a yes or a no decides, silence: unknown;
        an answer with no yes / no word reads as no, a default: how = _GUESS)."""
        from robot_agent.core.run_control import yes_no
        ans = self._ask(run, "cond_unsure", self._text("q_unsure"), object=obj, place=place)
        if self.cfg["answer"]:
            return self._read(run, ans, place), "told"
        said, rest = yes_no(ans)
        return (_UNKNOWN if said is None else said), (_GUESS if said is False and rest == ans.strip() else "told")

    def _decide(self, run, w):
        """Flow: (holds, how, place): does ``w.object`` hold at ``w.at`` (True / False), _UNKNOWN when it cannot be told; how: "seen", "told"
        or _GUESS (no look could see it, key ``confirm_none``); or STOP."""
        obj = w["object"]
        place = self.svc.ground_place(run, w["at"])
        if not place:
            place = self.svc.ground_place(run, self._ask(run, "cond_where", self._text("q_where"), object=obj))
        seen = self._go_and_look(run, obj, place) if place else None
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        holds = _UNKNOWN if not place else bool(seen) and seen[-1]
        how = _GUESS if self.cfg["confirm_none"] and holds is False and self._blind(run, place) else "seen"
        if place and (seen is None or self._unsure(run, obj, place, seen)):
            holds, how = self._asked(run, obj, place)
        self.svc.lab("cond_goal", object=obj, at=w["at"], place=place or "", seen=seen, holds=holds)
        return holds, how, place

    def _decide_ask(self, run, w):
        """Flow: (holds, "told", None) of a ``when`` with a yes / no question (key ``answer``): asked once, read by itself; or STOP."""
        ans = (self.svc.ask(run, w["ask"]) or "") if self.cfg["ask_when_unsure"] else ""
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        holds = self._read(run, ans, None)
        self.svc.lab("cond_goal", object=w["object"], at="", place="", seen=None, holds=holds, part="ask")
        return holds, "told", None

    def _say(self, run, holds, obj, place):
        key, text = ("cond_seen", self._text("say_seen")) if holds else ("cond_not_seen", self._text("say_not_seen"))
        ph = self._phrase(key, text, object=obj, place=place)
        return [run.ev("status", msg=str(ph), say=self.svc.in_lang(ph, run.lang))] if ph else []

    def _dropped(self, run) -> list:
        return run.__dict__.setdefault("opt", {}).setdefault("cond_goal_dropped", [])

    def refused_goals(self, run):
        """The goals this option dropped because their condition did not hold (counted as covered by the read-back's issue check)."""
        return list(self._dropped(run))

    def goal_dropped(self, run, goal):
        """True when ``goal`` reads like one this option dropped (the same type, object, places and words); None otherwise."""
        from robot_agent.core.planning.dialog_converge import goal_sig
        return True if goal_sig([goal])[0] in goal_sig(self._dropped(run)) else None

    def _cannot_tell(self, run, w):
        """Flow: the run ends as failed, saying the question: the robot does not know the condition and does not guess."""
        ph = w.get("ask") or self._phrase("cond_unsure", self._text("q_unsure"), object=w["object"], place=w["at"])
        yield self.svc.finish(run, "failed", msg=f"cannot tell whether the {w['object']} is at {w['at']}",
                              **({"say": self.svc.in_lang(ph, run.lang)} if ph else {}))
        return STOP

    def _when(self, g):
        """The goal's condition with its decision key, or None (no condition, an incomplete one; a question ``when`` only with ``answer``)."""
        w = valid_when(g)
        if w is not None:
            return {**w, "key": (w["object"].lower(), w["at"].lower())}
        w = ask_when(g) if self.cfg["answer"] else None
        return {**w, "key": ("?", w["ask"].lower())} if w else None

    def _confirm(self, run, guesses):
        """Flow, key ``confirm_none``: every goal was dropped on a decision no look made: ``q_unsure`` once more, read by itself.
        (key, holds) of the first such decision; None when there is none, no question, or it was asked already."""
        st = run.__dict__.setdefault("opt", {}).setdefault("cond_goal_confirm", {})
        w = next(iter(guesses.values()), None)
        if w is None or st.get("asked") or not self._text("q_unsure"):
            return None
        st["asked"] = True
        place = w["place"]
        holds = self._read(run, self._ask(run, "cond_unsure", self._text("q_unsure"), object=w["object"], place=place), place)
        self.svc.lab("cond_goal", part="confirm", object=w["object"], place=place or "", holds=holds)
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        return w, holds

    def on_goals(self, run):
        """Flow: the goals with a ``when`` are kept or dropped by what the robot sees; the ``when`` field is removed from all."""
        if not any(isinstance(g, dict) and "when" in g for g in run.goals):
            return None
        done, guesses = run.__dict__.setdefault("opt", {}).setdefault("cond_goal", {}), {}
        for g in run.goals:
            w = self._when(g)
            if w is None or w["key"] in done:
                continue
            res = yield from (self._decide_ask(run, w) if w.get("ask") else self._decide(run, w))
            done[w["key"]] = res if res is STOP else res[0]
            if res is STOP:
                return STOP
            if done[w["key"]] == _UNKNOWN:
                return (yield from self._cannot_tell(run, w))
            if res[1] == _GUESS:
                guesses[w["key"]] = {**w, "place": res[2]}
            for ev in ([] if w.get("ask") else self._say(run, done[w["key"]], w["object"], w["at"])):
                yield ev
        kept, dropped = split(run.goals, done, self._when)
        if not kept and self.cfg["confirm_none"] and guesses:
            c = yield from self._confirm(run, guesses)
            if c is STOP:
                return STOP
            if c is not None and c[1] == _UNKNOWN:
                return (yield from self._cannot_tell(run, c[0]))
            if c is not None:
                done[c[0]["key"]] = c[1]
                kept, dropped = split(run.goals, done, self._when)
        run.goals = kept
        self._dropped(run).extend(dropped)
        run.filtered = list(getattr(run, "filtered", None) or []) + dropped      # goal dicts, as the engine's own filters record
        if kept:
            return None
        yield self.svc.finish(run, "success", msg="no goal's condition holds: nothing to do")
        return STOP


register(SPEC, CondGoal)
