"""Names of objects and places read from words, with the language data of the run: articles to strip and plural rules to fold.

All of it is the language packs' own data (``articles``, ``plural_rules``, ``grammar.joiners``; for the readings an answer names
``particles``, ``grammar.noun_particles``, ``dialogue_ext.polite_endings`` and ``grammar.speaker_forms``), read for the languages given (the
run's and the prompt language). A language with no such data: no article is stripped and a word is matched as it is (fail open).
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


# ── which of two readings an answer names: their distinguishing words (options ask_agree.distinct, answer_align) ─────
SPEAKER = "@speaker"        # the word of a reading that goes to the person talking: said by the packs' speaker words


def words(s, langs=None) -> set:
    """The words of ``s`` (a name, or a list of names), lower case, without a leading article, plurals folded."""
    items = [s] if isinstance(s, str) else list(s or ())
    return {fold(w, langs) for x in items for w in _WORD.findall(plain(x, langs))}


def endings(langs=None) -> tuple:
    """What may follow a name word inside one word in ``langs``: the packs' ``particles``, ``grammar.noun_particles`` and
    ``dialogue_ext.polite_endings`` (a language that attaches them), longest first; () for a language without them."""
    out = set()
    for lg in langs or (langdata.prompt_lang(),):
        for v in (langdata.value(lg, "particles", []), langdata.grammar(lg, "noun_particles", []),
                  langdata.block(lg, "dialogue_ext").get("polite_endings")):
            out.update(str(e).lower() for e in (v if isinstance(v, list) else []) if e)
    return tuple(sorted(out, key=lambda e: (-len(e), e)))


def speaker_words(langs=None) -> set:
    """The words the person talking uses for themself as a destination: ``grammar.speaker_forms`` of ``langs`` without the
    ambiguous ones (``speaker_forms_weak``); the word of the speaker, never the robot's word for its addressee."""
    weak = {w.lower() for w in langdata.grammar_all("speaker_forms_weak", langs or (langdata.prompt_lang(),))}
    return {w.lower() for w in langdata.grammar_all("speaker_forms", langs or (langdata.prompt_lang(),))} - weak


def reading_words(goal: dict, places=None, langs=None, speaker=None) -> set:
    """The words a person may use for the reading ``goal``: its object name and the person's own words for it (``said``,
    ``name_local``), the head and aliases of its places, and ``SPEAKER`` when it goes to the person talking (``speaker``: the
    ENV key of that person, or a speaker word as the destination)."""
    out = words([str(goal.get(k) or "") for k in ("object", "said", "name_local")], langs)
    for slot in ("to", "from"):
        key = str(goal.get(slot) or "")
        if key and (key == speaker or key.lower() in speaker_words(langs)):
            out.add(SPEAKER)
        elif key:
            env = (getattr(places, "env", None) or {}).get(key) or {}
            out |= words([key.split("@")[0].replace("_", " ")] + [str(a) for a in env.get("aliases") or [] if "@" not in str(a)], langs)
    return out


def distinguishing(a, b, langs=None) -> set:
    """The words of reading ``a`` that reading ``b`` does not have (each a name, a list of names or a word set)."""
    wa = set(a) if isinstance(a, (set, frozenset)) else words(a, langs)
    wb = set(b) if isinstance(b, (set, frozenset)) else words(b, langs)
    return wa - wb


def says(texts, wanted, langs=None) -> bool:
    """Do ``texts`` hold a word of ``wanted``: as a whole word, or followed by ONE declared ending of ``langs`` inside the
    word (``endings``); ``SPEAKER`` by a speaker word of the packs, with one ending too. A speaker word of one letter counts
    only with an ending (alone it may be another word, a demonstrative). False when ``wanted`` is empty."""
    want = {w for w in wanted if w != SPEAKER}
    me = speaker_words(langs) if SPEAKER in wanted else set()
    ends = endings(langs)
    for t in texts:
        for w in _WORD.findall(str(t or "").lower()):
            stems = {w[: -len(e)] for e in ends if w.endswith(e) and len(w) > len(e)}
            forms = {w} | stems
            if (stems | ({w} if len(w) > 1 else set())) & me or {fold(f, langs) for f in forms} & want:
                return True
    return False


def which_named(texts, readings, langs=None):
    """The index of the ONE reading (word sets) whose distinguishing words against every other reading ``texts`` hold;
    None when no reading or two of them are named (nothing is decided)."""
    hit = [i for i, r in enumerate(readings)
           if says(texts, distinguishing(set(r), set().union(*[set(o) for j, o in enumerate(readings) if j != i]), langs), langs)]
    return hit[0] if len(hit) == 1 else None
