"""Option ``look_then_ask`` (default ON): when the request does not say where an object is and the memory does not know, the robot
looks around where it stands first; only if it does not see the object there does it ask the person once; "I don't know" (or no
answer, or no place) leaves the rest to the ranked search, and the same question is not asked again after the first misses.

Order of one object without a source: (1) one ``Find`` here; (2) seen: that place is the source, no question; (3) not seen: "I don't see the
fork here. Where is it?" (pack i18n key ``q_where_is_here``); (4) a place (or a room) in the answer is the source (or looked at first);
(5) an answer that names something the map does not know (a typo, an unknown name; not an "I don't know": ``svc.is_unsure``): key
``offer_near`` asks ONE follow-up offering the nearest places (``svc.near_places``, i18n ``q_place_unknown``), its answer read as in (4);
(6) anything else: the search by belief takes over (memory, habit, ranking), silently. ``ask_source`` (asks BEFORE any look) does not ask
about an object this option handled. Acts on ``on`` / ``in`` goals only, once per object and run, never when the request names the source
or the memory trusts a sighting, never without a bound ``Find``. Hook ``on_goals``. Switch off: ``enabled`` 0 (``$ROBOT_AGENT_LOOK_THEN_ASK``);
``offer_near`` 0 drops an unmatched answer as before. Event: lab flag ``look_then_ask`` ``{object, seen, answered, place, room,
answered_unmatched, second_answer}``. Generic: no object, place or word of a language.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="look_then_ask",
    keys=(Key("enabled", "bool", True, aliases=("look_then_ask",)),
          Key("offer_near", "bool", True)),
    hooks=("on_goals",),
    order=29,                                   # before ask_source (30): it must see this option's work
    enabled="enabled",
    doc="Look around where the robot stands before asking where an object is; ask once if it is not seen; 'I don't know': the ranked search.",
)

MAX_OFFERED = 3        # places named in the follow-up: a short spoken question (a choice of the design, not tuned on any data)
_STOPPED = object()    # the run was cancelled while a question waited (an answer can be any text, even "stop")


def offered(near, dest, n: int = MAX_OFFERED) -> list:
    """The places the follow-up offers: the near ones, closest first, without the goal's destination (not where the object is), at most n."""
    return [k for k in near if k != dest][:n]


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
            if (yield from self._ask_where(run, g, obj)) == STOP:
                return STOP
        return None

    def _question(self, run, q, kind):
        """Flow: one question to the person: the answer, or ``_STOPPED`` (the stop event yielded) when the run was cancelled meanwhile."""
        yield run.ev("clarify", question=q, round=run.asked + 1, kind=kind)
        ans = self.svc.ask(run, q)
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return _STOPPED
        return ans

    def _ground(self, run, ans) -> tuple:
        """(place, room, the room's places) an answer names; nothing: (None, "", [])."""
        place = self.svc.ground_place(run, ans) if ans else None
        room, keys = self.svc.room_places(run, ans) if ans and not place else ("", [])
        return place, room or "", keys

    def _ask_where(self, run, g, obj):
        """Flow: "I don't see the X here. Where is it?", then the follow-up for an answer the map does not know; the answer used."""
        from robot_agent.core.planning import speech
        q = speech.LocalText("q_where_is_here", {"object": self.svc.said_of(run, obj)}, text=f"I don't see the {obj} here. Where is it?")
        ans = yield from self._question(run, q, "where_source")
        if ans is _STOPPED:
            return STOP
        place, room, keys = self._ground(run, ans)
        unmatched, more = bool(ans) and not (place or keys), None
        if unmatched:
            more = yield from self._follow_up(run, g, obj, ans)
            if more is _STOPPED:
                return STOP
        if more:
            q, ans, (place, room, keys) = more[0], more[1], more[2:]
        self.svc.lab("look_then_ask", obj=obj, seen=False, answered=bool(ans), place=place or "", room=room,
                     answered_unmatched=unmatched, second_answer=bool(more and more[1]))
        yield from self._use(run, g, obj, q, ans, place, room, keys)
        return None

    def _follow_up(self, run, g, obj, ans):
        """Flow: ONE question offering the places the answer nearly names: (question, answer, place, room, room places) of its answer;
        None when no follow-up is made (key off, an "I don't know", no place near enough); ``_STOPPED``."""
        if not self.cfg.get("offer_near") or self.svc.is_unsure(run, ans):
            return None
        opts = offered(self.svc.near_places(run, ans), g.get("to"))
        if not opts:
            return None
        from robot_agent.core import run_control
        from robot_agent.core.planning import speech
        said = " ".join(str(ans).split()).strip(" .!?")
        names = " or ".join(self.svc.say_name(run.places, k) for k in opts)
        q = speech.LocalText("q_place_unknown", {"said": said, "options": list(opts)}, ask={"intent": "choose", "options": list(opts), "about": obj},
                             text=f"I could not find '{said}' on my map. Do you mean: {names}? Or tell me another place.")
        ans2 = yield from self._question(run, q, "where_source_near")
        if ans2 is _STOPPED:
            return _STOPPED
        place, room, keys = self._ground(run, ans2)
        if not (place or keys) and len(opts) == 1 and ans2 and run_control.yes_no(ans2)[0] is True:
            place = opts[0]                                # "yes" to the one place offered
        return q, ans2, place, room, keys

    def _use(self, run, g, obj, q, ans, place, room, keys):
        """Flow: a place becomes the source (not the destination), a room's places are looked at first; else no further question."""
        from robot_agent.core.planning import speech
        if keys:
            self.svc.prefer_places(run, obj, keys)
            yield run.ev("status", msg=f"looking in the {room} first",
                         say=self.svc.in_lang(speech.LocalText("a_look_in_room", {"room": room},
                                                               text=f"OK, I will look in the {room}."), run.lang))
        if place and place != g.get("to"):
            g["from"] = place
            self.svc.note_answer(run, str(q), ans, obj, place)
        else:
            self.svc.mark_asked_where(run, obj)            # no place: the search goes on alone, no further question


register(SPEC, LookThenAsk)
