"""The clause words of a language: which words open a condition ("if the drawer is open") or a negation ("do not touch the mug").

The words are DATA of the language packs (``lang/<code>.json``, ``patterns.condition`` and ``patterns.negation``, one regex each; a clause
runs from its word to the next comma, semicolon or full stop, or the clause shape the pack gives). ``build(lang)`` compiles those of the run's
language and of the prompt language (the held English reading of a translated answer is read too), plus the words a robot pack declares for the language
(manifest ``planner.options.lang_words.clauses``: ``{lang: {"condition": regex | [regex], "negation": ..., "source": label | "assumed": true}}``).
Nothing declared: None, and the engine's own reading (``understand._active``) stands. A key that only one of the two packs declares leaves the other kind unread (nothing is
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


def _strs(v) -> list:
    return [v] if isinstance(v, str) and v else [x for x in v if isinstance(x, str) and x] if isinstance(v, (list, tuple)) else []


def _one(key: str, langs, declared):
    """One compiled regex of the packs' ``patterns[key]`` and the robot's declared words for ``langs``; None when there are none (or a bad one)."""
    pats = langdata.pattern_list(key, langs)
    for lg in langs:
        entry = declared.get(lg) if isinstance(declared, dict) else None
        pats += _strs(entry.get(key)) if isinstance(entry, dict) else []
    try:
        return re.compile("|".join(f"(?:{p})" for p in pats), re.I) if pats else None
    except re.error:
        return None


def build(lang, declared=None):
    """The run's ``Clauses`` for ``lang`` (first two letters) from the language packs and ``declared`` ({lang: {kind: regex(es)}}, the robot pack's),
    or None when nothing is declared for it or for the prompt language."""
    lg = str(lang or "").strip().lower()[:2]
    langs = [x for x in dict.fromkeys([lg, langdata.prompt_lang()]) if x]
    cond, neg = _one("condition", langs, declared), _one("negation", langs, declared)
    return Clauses(cond, neg) if (cond or neg) else None


def of(places):
    """The ``Clauses`` the run's places carry (set by option ``lang_words``), or None."""
    return getattr(places, "clauses", None)
