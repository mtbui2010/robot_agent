"""Reference copy for step S09: the grounding methods of AssistLoop EXACTLY as they were in assist_loop.py:712-906 at fc0ee0a
(before they moved to phases/ground.py): _ground_container, _ground_template, _ground_goals, _ground_speaker,
_person_destination and _say_name. Only the imports changed (absolute names; the local import in _say_name). Used by
tests/test_ground_phase.py as the differential reference; never edit the method bodies."""
import re

from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import persons, speech
from robot_agent.core.planning.places import words

_USER_WORDS = {"me", "user", "here", "you", "myself", "person"}


class RefGround:
    @staticmethod
    def _ground_container(g, places, task):
        """Ground an open / close goal's container on ENV: (goal, question|None)."""
        k = places.ground(g["object"], task) or places.ground(g.get("to") or "", task)
        if not k:
            drawers = [p for p in places.keys if places.openable(p)]
            k = drawers[0] if len(drawers) == 1 else None
        g["object"] = k or g["object"]; g["to"] = k or ""
        return g, (None if k else speech.LocalText("q_which_drawer", {"verb": g["type"]},
                                                   text=f"Which drawer should I {g['type']}?"))

    @staticmethod
    def _ground_template(g, places, task, latest=""):
        """Ground a go / say / deliver goal on ENV: (goal, question|None). A say goal's `to` may be a
        person instead of a place (``person``)."""
        g["from"] = ""
        v, t = g.get("to") or "", g["type"]
        if t == "say":
            if not g.get("object"):
                return g, speech.LocalText("q_what_say")
            k = places.ground(v, task) if v else None
            if v and not k:
                g["person"], g["to"] = v, ""
            elif k:
                g["to"] = k
            return g, None
        if not v:
            return g, speech.LocalText("q_where_go" if t == "go" else "q_where_take")
        c = places.candidates(v, task)
        if len(c) > 1 and latest and len(places.candidates(v, latest)) == 1:
            c = places.candidates(v, latest)
        if len(c) == 1 and c[0].startswith("@") and len(places.in_room(c[0][1:])) == 1:
            c = places.in_room(c[0][1:])
        if t == "go" and len(c) == 1 and c[0].startswith("@"):
            # a room is a fine place to wait: its first place that does not open, is not a person or the floor
            # (SAGE-v2 F3.5; no "which place" question for "wait in the bedroom")
            ok = [k for k in places.in_room(c[0][1:]) if not places.openable(k) and not persons.is_person(places, k)
                  and "floor" not in places.head(k).lower()]
            if ok:
                c = ok[:1]
        if len(c) == 1 and c[0] in places.env:
            g["to"] = c[0]
            return g, None
        opts = places.in_room(c[0][1:]) if len(c) == 1 else c
        nice = [k.replace("@", " in the ") for k in (opts or places.keys[:4])]
        return g, speech.LocalText("q_which_place", {"options": list(opts or places.keys[:4])},
                                   text=f"Which place do you mean: {' or '.join(nice)}?")

    def _ground_goals(self, goals, places, memory, task, latest=""):
        """Ground every goal's places on ENV. Returns (goals, question|None). `latest`: the user's last answer,
        which wins over earlier words ("the kitchen one" ... "no, the restroom")."""
        out, questions = [], []
        for g in goals:
            g = dict(g)
            t = g.get("type")
            if t in goal_kinds.TEMPLATE_KINDS or t in ("open", "close"):
                g, q_ = (self._ground_template(g, places, task, latest) if t in goal_kinds.TEMPLATE_KINDS
                         else self._ground_container(g, places, task))
                out.append(g); questions += [q_] if q_ else []
                continue
            for slot in ("to", "from"):
                v = g.get(slot) or ""
                if not v:
                    if slot == "to":
                        questions.append(speech.LocalText("q_where_put", {"object": g.get("said") or g["object"]},
                                                          text=f"Where should I put the {g['object']}?"))
                    continue
                if words(v) in _USER_WORDS or (slot == "to" and persons.is_speaker_word(v)):
                    q_ = self._ground_speaker(g, slot, places, task, latest)
                    questions += [q_] if q_ else []
                    continue
                c = places.candidates(v, task)
                if len(c) > 1 and latest:
                    c2 = places.candidates(v, latest)
                    if len(c2) == 1:
                        c = c2
                if len(c) == 1 and c[0] in places.env:
                    # the LLM may pick a key ('shelf@kitchen') the user never said: is its name unique in the words?
                    alt = places.candidates(places.head(c[0]), task)
                    if len(alt) > 1 and latest and places.candidates(places.head(c[0]), latest) == [c[0]]:
                        alt = [c[0]]                     # the last answer settles it
                    if len(alt) > 1:
                        c, v = alt, places.head(c[0])
                    elif not places.said(c[0], task):
                        # 'test table' for 식탁 translated to 'table': the user's words (an alias) may name another
                        # place of that kind -> take it; several -> ask instead of guessing
                        alt = places.candidates(places.head(c[0]).split()[-1], task)
                        if len(alt) == 1 and alt[0] != c[0]:
                            c = alt
                        elif len(alt) > 1:
                            c, v = alt, places.head(c[0]).split()[-1]
                if (slot == "from" and len(c) == 1 and c[0] in places.env and not places.said(c[0], task)
                        and memory.where(g["object"]) != c[0]):
                    c = []                                # the model made up where it is: search instead
                if len(c) == 1 and not c[0].startswith("@"):
                    g[slot] = c[0]
                elif slot == "from" and not c:
                    g[slot] = ""                      # unknown source: search for it
                else:
                    opts = places.in_room(c[0][1:]) if len(c) == 1 else c
                    if slot == "from" and opts:
                        g["from_any"] = opts; g[slot] = ""   # 'from the kitchen' / 'the shelf': search them
                    else:
                        nice = [k.replace("@", " in the ") for k in (opts or places.keys[:4])]
                        what = places.head(v) if '@' in v else words(v)
                        questions.append(speech.LocalText("q_which_x", {"what": what, "options": list(opts or places.keys[:4])},
                                                          text=f"Which {what} do you mean: {' or '.join(nice)}?"))
            if g.get("from") and g.get("from") == g.get("to"):
                g["from"] = ""                       # 'from the desk to the desk': the model's slip, search instead
            if t == "in" and g.get("to") and not places.openable(g["to"]):
                drawers = [p for p in places.keys if places.openable(p)]
                if len(drawers) == 1:
                    g["to"] = drawers[0]
            if slot_q := self._person_destination(g, places, task, latest):
                questions.append(slot_q)
            if not g.get("from") and not g.get("from_any"):
                g["from"] = self._remembered(memory, g["object"]) or ""
            if g.get("from") and g.get("from") == g.get("to"):
                g["from"] = ""                       # already where it should go? search rather than trust it
            out.append(g)
        return out, (speech.LocalText.join(*dict.fromkeys(questions)) if questions else None)

    def _ground_speaker(self, g, slot, places, task, latest):
        """A destination in the speaker's own words ("me", "here", 나한테): the speaker's ENV entry (F2.1, no
        question when the robot knows where they are); an untracked speaker's place from their answer, else
        "Where are you?"; without a speaker entry, the place the words name ("to me ... at the sofa"), else the
        question (as before). Returns the question or None."""
        sk = persons.speaker_key(places)
        if sk is not None and slot == "to":
            g[slot] = sk
            return None                                   # untracked: _person_destination asks / maps
        m = re.search(r"\b(?:at|on|by|near) (?:the )?([a-z ]+?)(?:[.,!?]|$)", task.lower())
        k = places.ground(m.group(1), task) if m else None
        if k:
            g[slot] = k
            return None
        return "Where are you? Which place should I bring it to?"

    def _person_destination(self, g, places, task, latest):
        """An ``on`` goal to a person entry (F2.1 / F2.3): a tracked person -> a hand-over (``g['hand']``) or a
        put-down at the support surface beside them (``persons.delivery``); an untracked person (no ENV position,
        ``people_tracking`` off) -> the support surface nearest to the place their answer names, never the seat
        itself, else a hand-over; no answer yet -> "Where are you?" / "Where is X?". Returns the question or None."""
        to = g.get("to") or ""
        radius = float(self.cfg.get("person_seat_m", persons.PERSON_SEAT_M)) if hasattr(self, "cfg") else persons.PERSON_SEAT_M
        robot = getattr(self, "robot", None)
        asked = getattr(self, "_where_asked", None)
        if (g.get("type") == "on" and asked and latest and to in places.env and not persons.is_person(places, to)
                and not persons.is_support(places, to) and persons.place_in_answer(places, latest) == to):
            # the answer to "Where are you?" named where they sit / lie: the delivery is to them, never onto the
            # seat itself (the model put the answer's place as the destination)
            g["user_at"] = to
            to = g["to"] = persons.seat_person(places, to, radius) or asked
        if g.get("type") != "on" or not persons.is_person(places, to):
            return None
        mode = self.options.query("person_where", places, g, to, task, asked, latest, default="")   # "": the engine's own order
        if persons.tracked(places, to) and mode in ("", "keep"):
            mode, where = persons.delivery(places, to, task, robot, radius)
            mode = self.options.transform("on_ground", mode, places, to)         # e.g. reach_delivery: no declared surface, a hand-over
            if mode == "hand":
                g["hand"] = True
            else:
                g["to"] = where
            return None
        if persons.wants_hand(task, robot):
            g["hand"] = True
            return None
        # only an answer to "Where are you?" tells where the person is (not an answer to another question, nor the request again)
        at = persons.place_in_answer(places, latest) if latest and asked else None
        known = self.__dict__.setdefault("_person_at", {})
        if at is None:
            at = known.get(to)             # said earlier (as the answer): a later answer or question does not forget it
        else:
            known[to] = at                 # only an answer to the asked question gets here
        if at is None:
            if mode == "hand": g["hand"] = True; return None      # asked once, no place in the answer: a hand-over, not again
            self._where_asked = to
            if (places.env.get(to) or {}).get("speaker") or to == persons.speaker_key(places):
                return speech.LocalText(mode or "q_where_are_you")
            return speech.LocalText("q_where_is_person", {"person": to},
                                    text=f"Where is {self._say_name(places, to)}?")
        spot = persons.surface_near(places, at, float(self.cfg.get("person_surface_m", 1.2)) if hasattr(self, "cfg") else 1.2)
        g["user_at"] = at                 # where they said they are: covered by this goal (no "You mentioned ...")
        if spot:
            g["to"], g["near_person"] = spot, to
        else:
            g["hand"] = True
        return None

    @staticmethod
    def _say_name(places, key, lang="en") -> str:
        """How the robot names ``key`` to a person (F5.1): an alias in the user's script, else the key's head
        in words with its room; a person by their name."""
        from robot_agent.core.planning import language, speech
        return speech.say_name(places, key, lang)
