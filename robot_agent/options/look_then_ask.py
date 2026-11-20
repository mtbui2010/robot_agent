"""Option ``look_then_ask`` (default ON): when the request does not say where an object is and the memory does not know, the robot
looks around where it stands first; only if it does not see the object there does it ask the person once; "I don't know" (or no
answer, or no place) leaves the rest to the ranked search, and the same question is not asked again after the first misses.

Order of one object without a source: (1) one ``Find`` here; (2) seen: that place is the source, no question; (3) not seen: "I don't see the
fork here. Where is it?" (pack i18n key ``q_where_is_here``); (4) a place (or a room) in the answer is the source (or looked at first);
(5) anything else: the search by belief takes over (memory, habit, ranking), silently.

Unlike ``ask_source`` (which asks BEFORE any look, default off) this asks after a look. When both are on, this one acts first and the other
does not ask about the same object. It acts on ``on`` / ``in`` goals only, once per object and run, never when the request names the source,
never when the object memory still holds a trusted sighting, and never when the robot cannot make the look (no ``Find`` bound).

Generic: no object, place or word of a language (the question is a phrase of the pack's i18n). Hook ``on_goals``. Key ``enabled``
(``$ROBOT_AGENT_LOOK_THEN_ASK``). Lab flag ``look_then_ask``: ``{object, seen, answered, place, room}``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="look_then_ask",
    keys=(Key("enabled", "bool", True, aliases=("look_then_ask",)),),
    hooks=("on_goals",),
    order=29,                                   # before ask_source (30): it must see this option's work
    enabled="enabled",
    doc="Look around where the robot stands before asking where an object is; ask once if it is not seen; 'I don't know': the ranked search.",
)


class LookThenAsk(Option):
    def _done(self, run) -> set:
        return run.__dict__.setdefault("opt", {}).setdefault("look_then_ask", {}).setdefault("done", set())

    def _here(self, run) -> str:
        w = getattr(run, "world", None)
        return str(getattr(w, "arrived", "") or "")

    def on_goals(self, run):
        done = self._done(run)
        for g in run.goals:
            obj = g.get("object")
            if g.get("type") not in ("on", "in") or not obj or g.get("from") or g.get("from_any") or obj in done:
                continue
            if self.svc.remembered(run, obj):
                continue                                   # the memory still knows: neither look nor ask
            seen, skill = self.svc.look_for(run, obj)
            if skill is None:
                continue                                   # no look can be made here: the engine's own order
            done.add(obj)
            ask_src = run.__dict__.setdefault("opt", {}).setdefault("ask_source", {}).setdefault("asked", set())
            ask_src.add(obj)                               # ask_source must not ask about it again
            if seen:
                here = self._here(run)
                if here and here != g.get("to"):
                    g["from"] = here
                self.svc.lab("look_then_ask", obj=obj, seen=True, here=here)
                continue
            from robot_agent.core.planning import speech
            q = speech.LocalText("q_where_is_here", {"object": self.svc.said_of(run, obj)},
                                 text=f"I don't see the {obj} here. Where is it?")
            yield run.ev("clarify", question=q, round=run.asked + 1, kind="where_source")
            ans = self.svc.ask(run, q)
            stop = self.svc.cancelled(run)
            if stop:
                yield stop
                return STOP
            place = self.svc.ground_place(run, ans) if ans else None
            room = ""
            keys = []
            if not place and ans:
                room, keys = self.svc.room_places(run, ans)
            self.svc.lab("look_then_ask", obj=obj, seen=False, answered=bool(ans), place=place or "", room=room or "")
            if keys:
                self.svc.prefer_places(run, obj, keys)
                yield run.ev("status", msg=f"looking in the {room} first",
                             say=self.svc.in_lang(speech.LocalText("a_look_in_room", {"room": room},
                                                                   text=f"OK, I will look in the {room}."), run.lang))
            if place and place != g.get("to"):
                g["from"] = place
                self.svc.note_answer(run, str(q), ans, obj, place)
            else:
                self.svc.mark_asked_where(run, obj)        # no place: the search goes on alone, no second question
        return None


register(SPEC, LookThenAsk)
