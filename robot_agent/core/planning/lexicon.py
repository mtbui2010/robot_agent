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
* ``TELL``: verbs that relay a message ("tell", "let ... know", "remind", "ask ... to"; KO 말해 / 전해 / 알려 /
  얘기해 / 물어봐; VI nói / bảo / nhắn).
* ``KO_PARTICLES``: the closed list of Korean particles a noun may take (word boundaries for Hangul matching).
"""

from __future__ import annotations

import re

PUTIN = {
    "en": [r"\binto\b", r"\binside\b", r"\bin\b"],
    "ko": [r"안에", r"안쪽", r"속에", r"넣"],
    "vi": [r"\bvào\b", r"\btrong\b"],
}
ON_TOP = {"en": [r"\bon top of\b", r"\bonto\b", r"\bon\b"], "ko": [r"위에", r"위로"], "vi": [r"\btrên\b"]}
HAND = {
    "en": [r"\bhand (?:it |them |that |this )?(?:over )?(?:to )?(?:me|him|her|them)\b", r"\bhand me\b",
           r"\binto (?:my|his|her|their) hands?\b", r"\bin(?:to)? (?:my|his|her|their) hands?\b",
           r"\bpass (?:it |them )?(?:straight |directly )?(?:to )?me\b", r"\bdirectly to me\b",
           r"\bstraight to me\b", r"\bgive (?:it|them) to me directly\b", r"\bhand-?over\b", r"\bhand (?:it|them) over\b"],
    "ko": [r"손에", r"손으로", r"건네", r"쥐여", r"쥐어"],
    "vi": [r"tận tay", r"vào tay", r"đưa tay"],
}
FIRST_PERSON = {
    "en": [r"\bi\b", r"\bme\b", r"\bmy\b", r"\bmyself\b", r"\bmine\b"],
    "ko": [r"(?:^|(?<=\s))(?:나|저|제|내)(?:한테|에게|게|가|는|를|도|의)?(?=\s|$|[.,!?])", r"(?:^|(?<=\s))(?:내게|제게)"],
    "vi": [r"\btôi\b", r"\bmình\b", r"\bem\b"],
}
TELL = {
    "en": [r"\btell\b", r"\blet \w+(?: \w+)? know\b", r"\bremind\b", r"\bask \w+(?: \w+)? to\b", r"\bsay to\b",
           r"\bpass on\b", r"\binform\b", r"\bwarn\b"],
    "ko": [r"말해", r"말씀", r"전해", r"알려", r"얘기해", r"이야기해", r"물어봐", r"여쭤"],
    "vi": [r"\bnói\b", r"\bbảo\b", r"\bnhắn\b", r"\bbáo\b"],
}
KO_PARTICLES = ("이랑", "에서", "한테", "에게", "으로", "까지", "부터", "하고", "에", "을", "를", "이", "가", "은", "는",
                "께", "로", "도", "만", "의", "과", "와", "랑", "위", "안", "옆", "쪽")


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


__all__ = ["PUTIN", "ON_TOP", "HAND", "FIRST_PERSON", "TELL", "KO_PARTICLES", "lang_of", "patterns", "has"]
