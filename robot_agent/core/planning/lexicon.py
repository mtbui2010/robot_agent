"""Small, general word lists the SAGE-v2 understanding checks use (robot-agnostic, extendable by the robot).

The lists are DATA: the language packs in ``lang/<code>.json`` (``langdata.py``), one per language, never code. A table
here (``PUTIN`` ...) is a view of one pack key over every language that has a pack. A robot pack extends a list with its
i18n phrase of the same key (``RobotView.text(<key>, lang)``: words separated by ``|``), never replaces it. A language
with neither core nor pack words has no patterns: a check that needs them does nothing for it.

* ``PUTIN``: words that put an object INSIDE its destination ("in", "into", "inside"). "away" is not one: "put the mug
  away" names storing it, often on a shelf.
* ``ON_TOP``: words that keep it ON a container's top ("on top of").
* ``HAND``: a direct hand-over asked for ("hand me", "into my hand", "give it to me directly").
* ``FIRST_PERSON``: the speaker's own words, as whole words.
* ``RECIPIENT``: words that make the speaker the one who gets the object ("bring me", "here").
* ``TELL``: verbs that relay a message ("tell", "let ... know", "remind", "ask ... to").
* ``OPEN_WORDS``: words that ask to open something (kind_anchor).

Clean-room: written from general household-robot request phrasing and common grammar, not from any benchmark's
generator, templates, judge rules or simulated user.
"""

from __future__ import annotations

import re

from . import langdata


class Table:
    """A word list read from the language packs: ``table.get(lang, [])`` -> the regexes of ``lang`` (data, read on use)."""

    def __init__(self, key: str) -> None:
        self.key = key

    def get(self, lang, default=None):
        return langdata.words(lang, self.key) or ([] if default is None else default)

    def __getitem__(self, lang):
        return self.get(lang)

    def __contains__(self, lang) -> bool:
        return bool(langdata.words(lang, self.key))

    def langs(self) -> tuple:
        return tuple(lg for lg in langdata.languages() if langdata.words(lg, self.key))


PUTIN = Table("putin")
ON_TOP = Table("on_top")
HAND = Table("hand")
FIRST_PERSON = Table("first_person")
RECIPIENT = Table("recipient")
TELL = Table("tell")
OPEN_WORDS = Table("open_words")


def particles(lang) -> tuple:
    """The case particles a noun of ``lang`` may take (pack data; () when none)."""
    return tuple(langdata.phrases(lang, "particles"))


def lang_of(text: str) -> str:
    """The language whose pack declares the script of ``text`` (``script.letters``), else the pack marked
    ``text_fallback``; '' when no pack says."""
    t = str(text or "")
    fallback = ""
    for lg in langdata.languages():
        pack = langdata.get(lg)
        rx = (pack.get("script") or {}).get("letters")
        if rx and re.search(rx, t):
            return lg
        if pack.get("text_fallback"):
            fallback = lg
    return fallback


def patterns(table, robot=None, key: str = "", langs=None) -> list:
    """The core patterns of ``table`` for ``langs`` plus the robot's i18n ``key`` words (``|``-separated).
    ``langs`` None: every language that has core data, plus the languages the robot's phrase declares."""
    phrases = {}
    if robot is not None and key:
        try:
            phrases = (getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get(key) or {}
        except Exception:
            phrases = {}
        phrases = {str(k)[:2]: v for k, v in phrases.items()} if isinstance(phrases, dict) else {}
    if langs is None:
        core = table.langs() if hasattr(table, "langs") else tuple(table)
        langs = tuple(core) + tuple(k for k in phrases if k not in core)
    out = [p for lg in langs for p in table.get(lg, [])]
    for lg in langs:
        for w in str(phrases.get(lg) or "").split("|"):
            if w.strip():
                out.append(re.escape(w.strip()))
    return out


def has(table, text: str, robot=None, key: str = "") -> bool:
    t = " ".join(str(text or "").lower().split())
    return any(re.search(p, t) for p in patterns(table, robot, key))


__all__ = ["Table", "PUTIN", "ON_TOP", "HAND", "FIRST_PERSON", "RECIPIENT", "TELL", "OPEN_WORDS", "particles", "lang_of",
           "patterns", "has"]
