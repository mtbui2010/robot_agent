"""What the robot says, in the user's language, without raw ENV keys (SAGE-v2 F5.1 / F5.2 / F5.4).

* ``say_name(places, key, lang)``: how the robot names an ENV key to a person: the entry's first alias in the
  user's script (Hangul for ko), else its head in words with the room ("shelf in the kitchen"); a person by
  their name; the speaker as "you". No raw key (``bath_cabinet@bathroom``, ``person_b``) reaches the user.
* ``LocalText``: a question / read-back the loop builds from a fixed phrase. It is the English text (a ``str``:
  logs, the structured-question derive and every old caller see the English template) and carries the phrase
  ``key`` and its ``slots``, so ``render(text, lang, robot, places)`` says the ROBOT's own phrase in the user's
  language (its i18n), with the slots named by ``say_name``. Only free text (the model's own questions, Say
  sentences) needs the translator.
* ``CORE_PHRASES``: the English phrases (the core default; a robot pack gives other languages in its i18n under
  the same keys).
* ``guard(text, lang, keep)``: is a translation in the target script and does it keep the names
  (``translate_model``'s output check)?
"""

from __future__ import annotations

import re
from typing import Optional

CORE_PHRASES = {
    "q_where_put": "Where should I put the {object}?",
    "q_which_place": "Which place do you mean: {options}?",
    "q_which_x": "Which {what} do you mean: {options}?",
    "q_which_drawer": "Which drawer should I {verb}?",
    "q_where_go": "Where should I go?",
    "q_where_take": "Where should I take it?",
    "q_what_say": "What should I say?",
    "q_where_are_you": "Where are you?",
    "q_where_is_person": "Where is {person}?",
    "q_where_is": "I couldn't find the {object}. Where is it?",
    "q_repeat": "Sorry, I didn't catch that. Could you say it again?",
    "q_silence": "Sorry, I didn't hear you.",
    "q_did_you_mean": "Did you mean the {object}?",
    "q_what_bring": "What should I bring?",
    "q_confirm": "{summary} Is that right?",
    "rb_on": "put the {object} on the {place}",
    "rb_on_from": "put the {object} from the {source} on the {place}",
    "sep_then": ", then ",
    "rb_in": "put the {object} in the {place}",
    "rb_to_you": "bring you the {object}",
    "rb_on_near_you": "bring you the {object} and put it on the {place} next to you",
    "rb_on_near_person": "bring the {object} to {person} and put it on the {place} next to them",
    "rb_hand_you": "hand you the {object}",
    "rb_to_person": "bring the {object} to {person}",
    "rb_hand_person": "hand the {object} to {person}",
    "rb_open": "open the {place}",
    "rb_close": "close the {place}",
    "rb_go": "go to the {place}",
    "rb_say": "tell {person}: \"{text}\"",
    "rb_say_here": "say \"{text}\"",
    "rb_say_at": "say \"{text}\" in the {place}",
    "rb_will": "I will {what}.",
    "q_mentioned": "You mentioned the {what}. What should I do there?",
    "q_unclear_task": "Sorry, I did not understand. Could you say the task in other words?",
    "q_what_exactly": "What should I do exactly?",
    "q_what_do": "Sorry. What should I do?",
    "iss_place": "the {what} you mentioned is not in my plan",
    "iss_unsaid": "you did not say the {what}",
    "iss_object": "the {what} is not in my plan",
    "rb_unsure": "I am not sure I understood ({issues}).",
    "sep_semi": "; ",
    "say_stopped": "Okay, I stopped.",
    "say_no_answer": "I did not hear an answer, so I will stop here.",
    "say_not_confirmed": "I could not confirm with you what to do, so I will stop here.",
    "say_gave_up": "I am sorry, I still could not understand what you want me to do.",
    "safety_partial": "I won't {what}: {why}.",
}

_HANGUL = re.compile(r"[가-힣]")
_HAN_KANA = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _script_ok(alias: str, lang: str) -> bool:
    a = str(alias or "")
    if not a or "@" in a:
        return False
    if (lang or "en")[:2] == "ko":
        return bool(_HANGUL.search(a))
    return a.isascii() if (lang or "en")[:2] == "en" else not _HANGUL.search(a)


def _words(k: str) -> str:
    return " ".join(str(k or "").replace("_", " ").split())


def say_name(places, key, lang: str = "en") -> str:
    """See the module doc. ``places``: assist_loop.Places (env, head, room)."""
    key = str(key or "")
    if key.startswith("@"):
        return _words(key[1:])
    e = (getattr(places, "env", {}) or {}).get(key) or {}
    lg = (lang or "en")[:2]
    if e.get("speaker"):
        return "you" if lg == "en" else next((a for a in e.get("aliases") or [] if _script_ok(a, lg)
                                              and not _speaker_alias(a)), "")
    al = [a for a in e.get("aliases") or [] if _script_ok(a, lg) and not _speaker_alias(a)]
    if e.get("person"):
        return al[0] if al else _words(e.get("person"))
    if al and lg != "en":
        return al[0]
    head, room = _words(key.split("@")[0]), _words(key.split("@")[1]) if "@" in key else ""
    if lg == "en":
        named = next((a for a in al if room and room.lower() in a.lower()), None)
        return named or (f"{head} in the {room}" if room else head)
    return al[0] if al else (f"{head} ({room})" if room else head)


def _speaker_alias(a: str) -> bool:
    from .persons import is_speaker_word
    return is_speaker_word(a)


class LocalText(str):
    """English text built from the phrase ``key`` with ``slots`` (see the module doc)."""

    key: str = ""
    slots: dict = {}
    parts: tuple = ()

    def __new__(cls, key: str, slots: Optional[dict] = None, text: Optional[str] = None, parts=()):
        slots = dict(slots or {})
        en = text if text is not None else _fmt(CORE_PHRASES.get(key, key), slots)
        out = super().__new__(cls, en)
        out.key, out.slots, out.parts = key, slots, tuple(parts)
        return out

    @classmethod
    def join(cls, *items) -> "LocalText":
        """Several phrases said one after the other ("Which shelf ...? Where should I put the cup?")."""
        items = [x for x in items if x]
        if len(items) == 1 and isinstance(items[0], LocalText):
            return items[0]
        return cls("_join", {}, text=" ".join(str(x) for x in items), parts=tuple(items))

    def again(self) -> "LocalText":
        """The question asked again after silence ("Sorry, I didn't hear you. <question>")."""
        return LocalText.join(LocalText("q_silence"), self)

    @classmethod
    def seq(cls, items, sep_key: str = "sep_then") -> "LocalText":
        """Phrases in a row with the robot's separator ("A, then B, then C")."""
        items = [x for x in items if x]
        sep = CORE_PHRASES.get(sep_key, " ")
        out = cls("_seq", {"sep": sep_key}, text=sep.join(str(x) for x in items), parts=tuple(items))
        return out


def _fmt(s: str, slots: dict) -> str:
    try:
        return s.format(**slots)
    except (KeyError, IndexError, ValueError):
        return s


def phrase(robot, key: str, lang: str) -> Optional[str]:
    """The robot's phrase for ``key`` in ``lang`` (its i18n), the core English one for en, else None."""
    lg = (lang or "en")[:2]
    by = ((getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get(key) or {}) if robot is not None else {}
    if by.get(lg):
        return by[lg]
    return CORE_PHRASES.get(key) if lg == "en" else None


def render(text, lang: str, robot=None, places=None, name_slots=("place", "person", "options")) -> Optional[str]:
    """``text`` said in ``lang`` from the robot's phrases (None: a slot / phrase is missing in that language,
    the caller translates instead). A plain str is returned as is for en, else None."""
    lg = (lang or "en")[:2]
    if not isinstance(text, LocalText):
        return str(text) if lg == "en" else None
    if text.key in ("_join", "_seq"):
        out = [render(p, lang, robot, places, name_slots) for p in text.parts]
        if any(o is None for o in out):
            return None
        sep = " " if text.key == "_join" else phrase(robot, text.slots.get("sep", "sep_then"), lg)
        return None if sep is None else sep.join(out)
    tpl = phrase(robot, text.key, lg)
    if tpl is None:
        return str(text) if lg == "en" else None
    slots = {}
    for k, v in text.slots.items():
        if "{" + k + "}" not in tpl:
            continue                                  # a slot this language's phrase does not say
        if isinstance(v, LocalText):
            v = render(v, lang, robot, places, name_slots)
            if v is None:
                return None
        elif k in ("place", "person", "source") and places is not None and v in (getattr(places, "env", {}) or {}):
            v = say_name(places, v, lg)
            if not v:
                return None
        elif k == "options" and isinstance(v, (list, tuple)):
            names = [say_name(places, x, lg) if places is not None else str(x) for x in v]
            if not all(names):
                return None
            v = (" 아니면 " if lg == "ko" else " or ").join(names)
        elif k in ("object", "text", "what", "why", "summary", "first", "rest") and lg != "en" and \
                v and not _in_script(str(v), lg):
            return None                               # an English slot in a Korean phrase: translate the whole
        slots[k] = v
    return _fmt(tpl, slots)


def _in_script(s: str, lg: str) -> bool:
    return bool(_HANGUL.search(s)) if lg == "ko" else True


def guard(text: str, lang: str, keep=()) -> Optional[str]:
    """Why ``text`` is not an acceptable translation into ``lang`` (None = acceptable): for ko, Hangul makes at
    least 60 % of the letters, no Han / kana / Cyrillic, no Latin word except the names to keep and digits; every
    name of ``keep`` that must appear does."""
    t = str(text or "").strip()
    if not t:
        return "empty"
    lg = (lang or "en")[:2]
    if lg == "ko":
        if _HAN_KANA.search(t) or _CYRILLIC.search(t):
            return "Chinese / Japanese / Cyrillic characters"
        rest = t
        for k in keep:
            rest = rest.replace(str(k), " ")
        letters = [c for c in rest if c.isalpha()]
        hangul = [c for c in letters if _HANGUL.match(c)]
        if letters and len(hangul) / len(letters) < 0.6:
            return "not Korean"
        if re.search(r"[A-Za-z]{2,}", rest):
            return "English words left"
    for k in keep:
        if k and str(k) not in t:
            return f"the name {k!r} is missing"
    return None


__all__ = ["CORE_PHRASES", "LocalText", "say_name", "phrase", "render", "guard"]
