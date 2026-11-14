"""Names of objects and places read from words, with the language data of the run: articles to strip and plural rules to fold.

All of it is the language packs' own data (``articles``, ``plural_rules``, ``grammar.joiners``), read for the languages given (the run's
and the prompt language). A language with no such data: no article is stripped and a word is matched as it is (fail open).
"""

from __future__ import annotations

import re

from . import langdata

_WORD = re.compile(r"[\w']+")


def langs_of(run=None) -> tuple:
    """The run's language and the prompt language (first two letters, no duplicates)."""
    got = [str(getattr(run, "lang", "") or "").strip().lower()[:2], langdata.prompt_lang()]
    return tuple(dict.fromkeys(x for x in got if x))


def articles(langs=None) -> tuple:
    """The leading words an object label may carry, in ``langs`` (default: the prompt language)."""
    return tuple(dict.fromkeys(a.lower() for lg in (langs or (langdata.prompt_lang(),)) for a in langdata.phrases(lg, "articles") if a))


def plain(s, langs=None) -> str:
    """The words of ``s`` (lower case), without a leading article of ``langs``."""
    toks = _WORD.findall(str(s or "").lower())
    arts = articles(langs)
    return " ".join(toks[1:] if toks and toks[0] in arts else toks)


def rules(langs=None) -> list:
    """[(suffix, replacement)] singular rules of ``langs`` (the packs' ``plural_rules``), first match wins; [] = no folding."""
    out = []
    for lg in langs or (langdata.prompt_lang(),):
        out += [(str(r[0]), str(r[1])) for r in (langdata.value(lg, "plural_rules", []) or []) if isinstance(r, list) and len(r) == 2 and r[0]]
    return out


def fold(word: str, langs=None) -> str:
    """``word`` as a singular, by the packs' rules (at least three letters stay); unchanged when no rule applies."""
    for suf, rep in rules(langs):
        if word.endswith(suf) and len(word) > len(suf) + 2:
            return word[: -len(suf)] + rep
    return word


def joiners(langs=None) -> set:
    """Words that join two objects of one request ("kettle AND lamp"), from the packs' ``grammar.joiners``."""
    return {w.lower() for w in langdata.grammar_all("joiners", langs or (langdata.prompt_lang(),))}


def occurrences(name: str, text: str, langs=None) -> list:
    """[(start, end)] of ``name`` as whole words in ``text``, plural forms folded on both sides (exact words when no rules)."""
    want = [fold(w, langs) for w in _WORD.findall(plain(name, langs))]
    toks = [(m.start(), m.end(), fold(m.group(), langs)) for m in _WORD.finditer(str(text or "").lower())]
    n = len(want)
    return [(toks[i][0], toks[i + n - 1][1]) for i in range(len(toks) - n + 1) if n and [t[2] for t in toks[i:i + n]] == want]


def has(small: str, big: str, langs=None) -> bool:
    """The words of ``small`` are, in order, whole words of ``big`` (plurals folded by the packs' rules)."""
    return bool(occurrences(small, plain(big, langs), langs)) if plain(small, langs) else False
