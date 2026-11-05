"""Small, general word lists the SAGE-v2 understanding checks use (robot-agnostic, extendable by the robot).

Clean-room: written from general household-robot request phrasing and common grammar (EN, KO, VI), not from
any benchmark's generator, templates, judge rules or simulated user. Each list is a core default; a robot pack
extends it with its i18n phrase of the same key (``RobotView.text(<key>, lang)``: words separated by ``|``),
never replaces it.

* ``PUTIN``: words that put an object INSIDE its destination ("in", "into", "inside"; KO 안에 / 안쪽 / 넣다;
  VI vào / trong). "away" is not one: "put the mug away" names storing it, often on a shelf.
* ``ON_TOP``: words that keep it ON a container's top ("on top of", 위에, trên).
* ``HAND``: a direct hand-over asked for ("hand me", "into my hand", "give it to me directly"; KO 손에, 건네;
  VI đưa tận tay).
* ``FIRST_PERSON``: the speaker's own words (EN I / me / my, KO 나 / 저 / 제 / 내, VI tôi / mình), as whole
  words.
* ``RECIPIENT``: words that make the speaker the one who gets the object ("bring me", "here", 나한테, 가져다,
  "컵 좀 줘"; VI cho tôi / mang / đưa).
* ``TELL``: verbs that relay a message ("tell", "let ... know", "remind", "ask ... to"; KO 말해 / 전해 / 알려 /
  얘기해 / 물어봐; VI nói / bảo / nhắn).
* ``KO_PARTICLES``: the closed list of Korean particles a noun may take (word boundaries for Hangul matching).
"""

from __future__ import annotations

import re

from .. import domain

# the default (household) profile's lists (domains/household/lexicon.yaml); a robot that selects another profile is
# served by ``patterns`` / ``has`` below, which swap a list for the profile's list of the same name
_H = domain.load()
PUTIN, ON_TOP, HAND, FIRST_PERSON, RECIPIENT, TELL = (_H.words(n) for n in (
    "PUTIN", "ON_TOP", "HAND", "FIRST_PERSON", "RECIPIENT", "TELL"))
KO_PARTICLES = tuple(_H.words("KO_PARTICLES"))
_NAMES = {id(t): n for n, t in (("PUTIN", PUTIN), ("ON_TOP", ON_TOP), ("HAND", HAND), ("FIRST_PERSON", FIRST_PERSON),
                               ("RECIPIENT", RECIPIENT), ("TELL", TELL))}


def lang_of(text: str) -> str:
    """'ko' for Hangul, 'vi' for Vietnamese letters, else 'en'."""
    t = str(text or "")
    if re.search(r"[가-힣]", t):
        return "ko"
    if re.search(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", t, re.I):
        return "vi"
    return "en"


def patterns(table: dict, robot=None, key: str = "", langs=("en", "ko", "vi")) -> list:
    """The core patterns of ``table`` for ``langs`` plus the robot's i18n ``key`` words (``|``-separated)."""
    name = _NAMES.get(id(table))
    if name and robot is not None and domain.name_of(robot) != domain.DEFAULT:
        table = domain.of(robot).words(name) or {}          # the robot's profile has its own list of that name
    out = [p for lg in langs for p in table.get(lg, [])]
    if robot is not None and key:
        try:
            phrases = (getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get(key) or {}
        except Exception:
            phrases = {}
        for lg in langs:
            for w in str(phrases.get(lg) or "").split("|"):
                if w.strip():
                    out.append(re.escape(w.strip()))
    return out


def has(table: dict, text: str, robot=None, key: str = "") -> bool:
    t = " ".join(str(text or "").lower().split())
    return any(re.search(p, t) for p in patterns(table, robot, key))


__all__ = ["PUTIN", "ON_TOP", "HAND", "FIRST_PERSON", "RECIPIENT", "TELL", "KO_PARTICLES", "lang_of", "patterns", "has"]
