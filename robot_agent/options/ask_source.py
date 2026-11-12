"""Option ``ask_source`` (default off): when the request does not say where an object is and the robot does not remember it, ask first.

The default of SAGE is to guess, look, and ask only after misses (``search_max``) or when nowhere is left. This option asks at once,
before planning: "Where is the banana?" The answer, when it names a place, becomes the goal's source (``from``) and a sighting the
search may trust; "I don't know" (or no answer, or no place) leaves the goal as it is, so the object memory and the ranked search
take over, and the same question is not asked again after the first misses.

Generic: no object, place or word of a language (the question is a phrase of the pack's i18n, key ``q_where_is_obj``). It asks for
``on`` / ``in`` goals only, once per object, never when the request names the source, and never when the object memory still
holds a trusted sighting. More questions mean more chances of a wrong answer: it stays off in the benchmark.

Key ``reask_on_miss`` (default off): when the person did name a place, the first look that does not see the object asks them once more
("I couldn't find the X. Where is it?") instead of searching on alone; a person who said "I don't know" is not asked again.

Answers that name no place (all default off, each its own key; they act on the answer after the question above):
* ``use_room``: the answer names a room only ("in the study room"): the robot says so ("OK, I will look in the study room.") and the
  search belief looks at the places of that room first (its usual order, route included); the other places stay a fallback.
* ``confirm_near``: the answer nearly names one place (a cut or misspelled name; ``understand.fuzzy_place``): "Did you mean the X?"
  and a yes makes it the source. When no place is near, the same question is asked for a ROOM (``understand.fuzzy_room``: the ENV's room
  names, their words without "room", and the language layer's room names; ASR noise such as "story room" for "study room"); a yes
  is used as ``use_room`` does. Key ``near_threshold`` (default 0.25, the fuzzy_place value): the room match's normalised edit distance.
* ``clarify_unusable``: the answer names nothing usable and is not an "I don't know" (``dialog_unstick.unsure_answer``: the language
  pack's and the robot pack's ``answer_unsure`` phrases; a language without them never triggers it): ONE clarifying question ("Which room,
  or which piece of furniture, is it in?"), whose answer goes through the same steps; still nothing usable = "I don't know".

* ``hedged``: a hedged answer that still names a place ("I am not sure, maybe near the washr") is not read as "I don't know": when the
  answer is an "I don't know" phrase AND names no place exactly, the nearly-named place (then room) is offered as a guess ("Did you
  mean the X?", as ``confirm_near`` does, whether or not that key is on); a yes uses it. A bare "I am not sure" or a hedge that
  names nothing near stays an "I don't know" (no extra question).

Hook: ``on_goals``. Keys: ``enabled`` (legacy name ``ask_source``), ``reask_on_miss`` (legacy ``ask_source_reask``), ``use_room``,
``confirm_near``, ``near_threshold``, ``hedged``, ``clarify_unusable``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="ask_source",
    keys=(Key("enabled", "bool", False, aliases=("ask_source",)),
          Key("reask_on_miss", "bool", False, aliases=("ask_source_reask",)),
          Key("use_room", "bool", False, aliases=("ask_source_room",)),
          Key("confirm_near", "bool", False, aliases=("ask_source_near",)),
          Key("near_threshold", "float", 0.25),
          Key("hedged", "bool", False, aliases=("ask_source_hedged",)),
          Key("clarify_unusable", "bool", False, aliases=("ask_source_clarify",))),
    hooks=("on_goals", "on_where_answer"),
    order=30,
    enabled="enabled",
    doc="Ask where an object is before looking for it, when the request does not say and the memory does not know.",
)


class AskSource(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault("ask_source", {})

    def _resolve(self, run, obj, ans):
        """Flow: what the answer gives, as (place, room places, room); the steps that need a question ask it."""
        place = self.svc.ground_place(run, ans) if ans else None
        if place or not ans:
            return place, [], ""
        if self.cfg.get("use_room"):
            room, keys = self.svc.room_places(run, ans)
            if keys:
                return None, keys, room
        if self.cfg.get("confirm_near") or (self.cfg.get("hedged") and self.svc.is_unsure(run, ans)):
            place = yield from self.svc.confirm_near(run, ans)
            if not place:                                  # not a place: a room? ("Story Room" = the study room), same yes / no
                room, keys = yield from self.svc.confirm_near_room(run, ans, self.cfg.get("near_threshold"))
                if keys:
                    return None, keys, room
        return place, [], ""

    def _ask_where(self, run, q, kind):
        """Flow: one question to the person: (answer, the stop event or None)."""
        yield run.ev("clarify", question=q, round=run.asked + 1, kind=kind)
        ans = self.svc.ask(run, q)
        return ans, self.svc.cancelled(run)

    def _answer(self, run, obj, ans):
        """Flow: the place / room the answer names; a second question when it names nothing usable and the person did not say they
        do not know (``clarify_unusable``). (place, room places, room, answer, stop event)."""
        from robot_agent.core.planning import speech
        place, keys, room = yield from self._resolve(run, obj, ans)
        if not (place or keys) and ans and self.cfg.get("clarify_unusable") and not self.svc.is_unsure(run, ans):
            q = speech.LocalText("q_where_in_home", text="Which room, or which piece of furniture, is it in?")
            ans2, stop = yield from self._ask_where(run, q, "where_source_clarify")
            self.svc.lab("ask_source", clarified=True, obj=obj, answered=bool(ans2))
            if stop:
                return None, [], "", ans, stop
            if ans2:
                ans = ans2
                place, keys, room = yield from self._resolve(run, obj, ans)
        return place, keys, room, ans, None

    def on_where_answer(self, run, obj, ans):
        """The answer to the engine's own re-asked "I couldn't find the X. Where is it?" (after the first look missed): a room (``use_room``)
        or an unusable answer (``clarify_unusable``) is handled as for the first question. The place to go to, or None (the engine then
        goes on as it always did)."""
        if not (self.cfg.get("use_room") or self.cfg.get("clarify_unusable") or self.cfg.get("hedged")):
            return None
        from robot_agent.core.planning import speech
        place, keys, room, _ans, stop = yield from self._answer(run, obj, ans)
        if stop:
            yield stop
            return None
        if keys:
            self.svc.prefer_now(run, obj, keys)
            yield run.ev("status", msg=f"looking in the {room} first",
                         say=self.svc.in_lang(speech.LocalText("a_look_in_room", {"room": room}, text=f"OK, I will look in the {room}."), run.lang))
            return self.svc.first_unsearched(run, obj, keys)
        return place or None

    def on_goals(self, run):
        asked = self._state(run).setdefault("asked", set())
        for g in run.goals:
            obj = g.get("object")
            if g.get("type") not in ("on", "in") or not obj or g.get("from") or g.get("from_any") or obj in asked:
                continue
            if self.svc.remembered(run, obj):
                continue                                   # the memory still knows: no need to ask
            asked.add(obj)
            from robot_agent.core.planning import speech
            q = speech.LocalText("q_where_is_obj", {"object": self.svc.said_of(run, obj)}, text=f"Where is the {obj}?")
            ans, stop = yield from self._ask_where(run, q, "where_source")
            if stop:
                yield stop
                return STOP
            place, keys, room, ans, stop = yield from self._answer(run, obj, ans)
            if stop:
                yield stop
                return STOP
            self.svc.lab("ask_source", obj=obj, answered=bool(ans), place=place or "", room=room)
            if keys:
                self.svc.prefer_places(run, obj, keys)
                yield run.ev("status", msg=f"looking in the {room} first",
                             say=self.svc.in_lang(speech.LocalText("a_look_in_room", {"room": room}, text=f"OK, I will look in the {room}."), run.lang))
            if place and place != g.get("to"):
                g["from"] = place
                self.svc.note_answer(run, str(q), ans, obj, place)
            if place and self.cfg.get("reask_on_miss"):
                self.svc.ask_again_on_miss(run, obj)               # the place they named did not show it: ask once more
            else:
                self.svc.mark_asked_where(run, obj)                # "I don't know" / no place: no second question
            # no place in the answer ("I don't know"): the exchange stays out of the dialogue the understand step re-reads, which would
            # take "I don't know where it is" for an answer about the destination
        return None


register(SPEC, AskSource)
