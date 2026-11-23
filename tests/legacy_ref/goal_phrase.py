"""Reference copy for Step S08: ``summary`` and ``_goal_phrase`` as they were in
robot_agent/core/planning/assist_loop.py lines 1025-1068 at commit ef42ad3 (the tip of generalize-core before S08), copied
verbatim as the static methods of a class ``Old``, with ``_fmt_en`` (assist_loop.py:106-108 at ef42ad3) beside it. What
differs from the file: the class is named ``Old`` (the bodies say ``AssistLoop``, bound to ``Old`` below) and the
relative imports are absolute. Never edited: it is the old behaviour that tests/test_goal_phrase_diff.py compares the
pack-chosen phrases with."""

from __future__ import annotations

from robot_agent.core.planning import persons, speech
from robot_agent.core.planning import goals as goal_kinds


def _fmt_en(key: str, **slots) -> str:
    from robot_agent.core.planning.speech import CORE_PHRASES
    return CORE_PHRASES[key].format(**slots)


class Old:
    @staticmethod
    def summary(goals, places=None):
        """The read-back "I will ..., then ...": a ``speech.LocalText`` (English text; the robot's phrases in the
        user's language, ``speech.render``). With ``places`` the keys are named as a person says them (F5.1) and a
        delivery to a person reads "bring you the cup" / "hand the cup to grandpa"."""
        nice = (lambda k: speech.say_name(places, k, "en")) if places is not None else \
            (lambda k: (k or "").replace("@", " in the "))
        return speech.LocalText("rb_will", {"what": speech.LocalText.seq(
            [Old._goal_phrase(g, places, nice) for g in goals])},
            text="I will " + ", then ".join(str(Old._goal_phrase(g, places, nice)) for g in goals) + ".")

    @staticmethod
    def _goal_phrase(g, places, nice):
        L, t, obj = speech.LocalText, g["type"], g.get("object") or ""
        o = g.get("said") or g.get("name_local") or obj          # name_local: the object's name in the user's
        if t == "on" and places is not None and persons.is_person(places, g.get("to")):   # language (F4.8)
            you = bool((places.env.get(g["to"]) or {}).get("speaker"))
            key = ("rb_hand_you" if you else "rb_hand_person") if g.get("hand") else ("rb_to_you" if you else "rb_to_person")
            return L(key, {"object": o, "person": g["to"]}, text=_fmt_en(key, object=obj, person=nice(g["to"])))
        if t == "on" and g.get("near_person") and places is not None:
            you = bool((places.env.get(g["near_person"]) or {}).get("speaker"))
            key = "rb_on_near_you" if you else "rb_on_near_person"
            return L(key, {"object": o, "place": g["to"], "person": g["near_person"]},
                     text=_fmt_en(key, object=obj, place=nice(g["to"]), person=nice(g["near_person"])))
        if t == "on":
            if g.get("from") and not (places is not None and persons.is_person(places, g["from"])):
                return L("rb_on_from", {"object": o, "source": g["from"], "place": g["to"]},
                         text=f"put the {obj} from the {nice(g['from'])} on the {nice(g['to'])}")
            return L("rb_on", {"object": o, "place": g["to"]}, text=f"put the {obj} on the {nice(g['to'])}")
        if t == "in":
            return L("rb_in", {"object": o, "place": g["to"]}, text=f"put the {obj} in the {nice(g['to'])}")
        if t == "go" and places is not None:
            return L("rb_go", {"place": g.get("to")}, text=f"go to the {nice(g.get('to'))}")
        if t == "say" and places is not None:
            who = g.get("to") or ""
            if who and persons.is_person(places, who):
                return L("rb_say", {"person": who, "text": obj}, text=f"tell {nice(who)}: \"{obj}\"")
            if who:
                return L("rb_say_at", {"place": who, "text": obj}, text=f"say \"{obj}\" in the {nice(who)}")
            if g.get("person"):
                return L("rb_say", {"person": g["person"], "text": obj}, text=f"tell {g['person']}: \"{obj}\"")
            return L("rb_say_here", {"text": obj}, text=f"say \"{obj}\"")
        if t in goal_kinds.TEMPLATE_KINDS:
            return goal_kinds.summary(g)
        return L(f"rb_{t}", {"place": obj}, text=f"{t} the {nice(obj)}") if t in ("open", "close") else f"{t} the {nice(obj)}"
