"""Option ``cond_goal`` (default off): a request with a condition ("if the spoon is on the table, put it away") is read with a ``when`` field
and the condition is looked at by the robot, not guessed by the reader.

The understand schema gets an OPTIONAL object ``when`` {object, at, present} on a goal, after ``content`` (the key order matters for the
backend grammar, docs/home/bench/lora.md "The JSON key order": the goals without a condition are written as before). It means: do this
goal only if ``object`` is (``present`` true) or is not (false) at ``at``. Before the read-back the robot goes to ``at``, looks (up to
``looks`` times), keeps the goals whose ``present`` equals what it saw, drops the others (listed in ``run.filtered``) and removes the
``when`` field. Unsure (the two looks differ, or the object memory put it there but the look did not see it): asks the person once, if the
pack has the question; an answer that is not a yes, no answer, or no question = absent (the safe side: the conditional goal is not done).

Generic: no object, place or word of a language. All the texts are pack data (manifest ``planner.options.cond_goal.<key>``, localised by the
phrase key like every robot phrase): ``prompt`` (the lines added to the understand prompt before ``Task:``), ``q_where`` (``cond_where``,
slot ``{object}``), ``q_unsure`` (``cond_unsure``, slots ``{object}``, ``{place}``), ``say_seen`` (``cond_seen``), ``say_not_seen``
(``cond_not_seen``). No ``prompt`` = the option changes neither the schema nor the prompt; an empty question = not asked; an empty sentence =
nothing said. Needs the pack's action role ``move`` (none = the condition is read as absent). Use it with a reader trained on ``when``.

Hooks: ``on_understand_schema``, ``on_understand_prompt``, ``on_goals``. Keys: ``enabled`` (legacy ``cond_goal``), ``looks`` (2),
``ask_when_unsure`` (true), ``prompt``, ``q_where``, ``q_unsure``, ``say_seen``, ``say_not_seen``. Lab flag ``cond_goal``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="cond_goal",
    keys=(Key("enabled", "bool", False, aliases=("cond_goal",)),
          Key("looks", "int", 2),
          Key("ask_when_unsure", "bool", True),
          Key("prompt", "str", ""),
          Key("q_where", "str", ""),
          Key("q_unsure", "str", ""),
          Key("say_seen", "str", ""),
          Key("say_not_seen", "str", "")),
    hooks=("on_understand_schema", "on_understand_prompt", "on_goals"),
    order=20,
    enabled="enabled",
    doc="A goal with a condition (when: object at place, present or not) is decided by the robot looking at the place, not by the reader.",
)

_MARK = "\nTask: {task}"
_WHEN = {"type": "object",
         "properties": {"object": {"type": "string"}, "at": {"type": "string"}, "present": {"type": "boolean"}},
         "required": ["object", "at", "present"]}


def valid_when(g):
    """The goal's ``when`` as {object, at, present} when it is complete (two non-empty strings and a boolean), else None."""
    w = g.get("when") if isinstance(g, dict) else None
    if not isinstance(w, dict) or not isinstance(w.get("present"), bool):
        return None
    o, at = str(w.get("object") or "").strip(), str(w.get("at") or "").strip()
    return {"object": o, "at": at, "present": w["present"]} if o and at else None


def without_when(g) -> dict:
    return {k: v for k, v in g.items() if k != "when"}


class CondGoal(Option):
    # ── what the reader is asked for ─────────────────────────────────────
    def on_understand_schema(self, schema):
        """``schema`` with the optional ``when`` after the goal's last property; None (unchanged) without pack prompt text."""
        items = ((schema.get("properties") or {}).get("goals") or {}).get("items")
        if not self.cfg["prompt"].strip() or not isinstance(items, dict) or "properties" not in items:
            return None
        items["properties"]["when"] = {**_WHEN, "properties": dict(_WHEN["properties"])}
        return schema

    def on_understand_prompt(self, text):
        """The prompt template with the pack's lines before ``Task:`` (braces escaped: the template is formatted next)."""
        extra = self.cfg["prompt"].strip()
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
        seen = []
        for _ in range(max(1, int(self.cfg["looks"]))):
            seen.append(bool(self.svc.look_for(run, obj)[0]))
            if seen[-1]:
                break
        return seen

    def _unsure(self, run, obj, place, seen) -> bool:
        """The looks do not settle it: a miss then a sighting, or a trusted memory of ``obj`` at ``place`` that no look saw."""
        return seen == [False, True] or (not any(seen) and self.svc.remembered(run, obj) == place)

    def _decide(self, run, w):
        """Flow: does ``w.object`` hold at ``w.at`` (True / False), or STOP. Unsure and unanswered = False."""
        from robot_agent.core.run_control import yes_no
        obj = w["object"]
        place = self.svc.ground_place(run, w["at"])
        if not place:
            place = self.svc.ground_place(run, self._ask(run, "cond_where", self.cfg["q_where"], object=obj))
        seen = self._go_and_look(run, obj, place) if place else None
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        holds = bool(seen) and seen[-1]
        if seen is not None and self._unsure(run, obj, place, seen):
            holds = yes_no(self._ask(run, "cond_unsure", self.cfg["q_unsure"], object=obj, place=place))[0] is True
        self.svc.lab("cond_goal", object=obj, at=w["at"], place=place or "", seen=seen, holds=holds)
        return holds

    def _say(self, run, holds, obj, place):
        key, text = ("cond_seen", self.cfg["say_seen"]) if holds else ("cond_not_seen", self.cfg["say_not_seen"])
        ph = self._phrase(key, text, object=obj, place=place)
        return [run.ev("status", msg=str(ph), say=self.svc.in_lang(ph, run.lang))] if ph else []

    def on_goals(self, run):
        """Flow: the goals with a ``when`` are kept or dropped by what the robot sees; the ``when`` field is removed from all."""
        if not any(isinstance(g, dict) and "when" in g for g in run.goals):
            return None
        done = run.__dict__.setdefault("opt", {}).setdefault("cond_goal", {})
        kept, dropped = [], []
        for g in run.goals:
            w = valid_when(g)
            if w is None:                       # no condition, or an incomplete one (read as none)
                kept.append(without_when(g))
                continue
            key = (w["object"].lower(), w["at"].lower())
            if key not in done:
                done[key] = yield from self._decide(run, w)
                if done[key] is STOP:
                    return STOP
                for ev in self._say(run, done[key], w["object"], w["at"]):
                    yield ev
            (kept if done[key] == w["present"] else dropped).append(without_when(g))
        run.goals = kept
        run.filtered = list(getattr(run, "filtered", None) or []) + dropped      # goal dicts, as the engine's own filters record
        if kept:
            return None
        yield self.svc.finish(run, "success", msg="no goal's condition holds: nothing to do")
        return STOP


register(SPEC, CondGoal)
