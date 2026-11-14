"""The clause words of a language: which words open a condition ("if the drawer is open") or a negation ("do not touch the mug").

The words are DATA of the language packs (``lang/<code>.json``, ``patterns.condition`` and ``patterns.negation``, one regex each; a clause
runs from its word to the next comma, semicolon or full stop, or the clause shape the pack gives). ``build(lang)`` compiles those of the run's
language and of the prompt language (the held English reading of a translated answer is read too). No pack declares either key: None, and the
engine's own reading (``understand._active``) stands. A key that only one of the two packs declares leaves the other kind unread (nothing is
taken out for it). Option ``lang_words`` hands the result to the run's ``Places`` (``places.clauses``).
"""

from __future__ import annotations

import re

from . import langdata


class Clauses:
    """The compiled condition and negation regexes of a run (None for a kind no pack declares)."""

    def __init__(self, cond=None, neg=None) -> None:
        self.cond = cond
        self.neg = neg


def _one(key: str, langs):
    return langdata.rx(key, re.I, langs=langs)


def build(lang):
    """The run's ``Clauses`` for ``lang`` (first two letters), or None when no pack of it or of the prompt language declares a kind."""
    lg = str(lang or "").strip().lower()[:2]
    langs = [x for x in dict.fromkeys([lg, langdata.prompt_lang()]) if x]
    cond, neg = _one("condition", langs), _one("negation", langs)
    return Clauses(cond, neg) if (cond or neg) else None


def of(places):
    """The ``Clauses`` the run's places carry (set by option ``lang_words``), or None."""
    return getattr(places, "clauses", None)
