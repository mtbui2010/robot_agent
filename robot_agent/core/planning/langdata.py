"""Language packs: what the planner knows about a human language is DATA, never code.

One JSON file per language in ``lang/<code>.json`` (``en``, ``ko``, ``ja``, ``zh`` ship with the core; a robot pack adds
or extends words through its manifest ``i18n.phrases``, see ``lexicon.patterns``). A pack holds only what is declared:

* ``words``: ``{table: [regex, ...]}``: the word lists of ``lexicon`` (putin, on_top, hand, first_person, recipient,
  tell) and of the lab options (``open_words``).
* ``answer_unsure``: phrases that say "I do not know" (matched as substrings of the casefolded answer).
* ``script``: ``{"letters": regex, "min_share": x}``: the letters that mark a text as written in the language and the
  share of them a localized text must reach; ``other_script``: a regex a localized text must not contain.
* ``name`` / ``register``: how the translate prompts name the language and the register of what the robot says.
* ``non_ascii_min``: for a Latin-script language, the share of non-ASCII letters that marks a text as localized.
* ``articles`` / ``plural_rules``: leading words an object label may carry, and ``[suffix, replacement]`` singular
  rules (the first that matches is applied, if at least three letters are left of the stem).
* ``particles``: closed list of the case particles a noun may take.
* ``patterns``: ``{key: regex | [regex, ...]}``, the regexes of the core's grammar checks (into / on-top markers before or after a
  place name, the self-correction markers, the verbs "open" / "close", what a refusal reason names, the endings an answer
  may carry). ``rx(key)`` joins the patterns of every language that has the key (data, read on use); a key no pack
  has gives None and the check does nothing (fail open).
* ``grammar``: small closed lists and numbers the checks read (``noun_particles``, ``verb_noun_suffix``, ``sep_or``, ``speaker_forms`` ...).
* ``dialogue``: the spoken words of a short exchange (yes / no / fillers / near-misses of yes) and the control words of the
  run (stop / pause / resume, negations, fallback keywords), see ``interact/dialog.py`` and ``interact/intent_router.py``.
* ``detect_marks``: letters of a Latin-script language that no other pack uses (it marks a text as written in it).
* ``guard``: what a translation into the language must satisfy (``forbid`` characters, ``min_share`` of its letters, no Latin word).
* ``object_aliases``: ``["cup|mug", ...]`` regional names of one object (the first is the canonical one).
* ``latin_script``: true for a language written in Latin letters with marks (the non-ASCII share decides, not a script block).

A language with no file (or a file without the key) has no data for it: every option that needs the data does nothing
for that language (fail open). Nothing in the code names a language.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Optional

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang")
_CACHE: dict = {}
# The language the core's prompts are written in, so the language of the model's own questions and reasons ("where is
# the mug?") and of a translated reading (``Heard.english``). A contract of the prompts, not a fact about any user.
# Default "en"; a robot sets it with the manifest ``planner.prompt_lang`` (or ``$ROBOT_AGENT_PROMPT_LANG``), which the
# AssistLoop hands to ``set_prompt_lang`` at the start of a run. ``PROMPT_LANG`` stays the default for readers of the old name.
PROMPT_LANG = "en"
_PROMPT_LANG: Optional[str] = None


def prompt_lang() -> str:
    """The language of the core's prompts for this run: ``set_prompt_lang``, else the module default ``PROMPT_LANG``."""
    return _PROMPT_LANG or PROMPT_LANG


def is_prompt_lang(code) -> bool:
    """Is ``code`` (a language code, first two letters) the language the core's prompts are written in?"""
    return str(code or "").strip().lower()[:2] == prompt_lang()


def set_prompt_lang(code) -> str:
    """Set the prompt language of the runs that follow (None / "" = back to the default); returns the effective code."""
    global _PROMPT_LANG
    c = str(code or "").strip().lower()[:2]
    _PROMPT_LANG = c if c.isalpha() and len(c) == 2 else None
    return prompt_lang()


def _load(code: str) -> dict:
    if code not in _CACHE:
        data: dict = {}
        path = os.path.join(_DIR, code + ".json")
        if code.isalpha() and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                data = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                data = {}
        _CACHE[code] = data
    return _CACHE[code]


_FILES: Optional[frozenset] = None


def languages() -> tuple:
    """The language codes that have a pack (sorted)."""
    global _FILES
    if _FILES is None:
        try:
            _FILES = frozenset(f[:-5] for f in os.listdir(_DIR) if f.endswith(".json") and f[:-5].isalpha())
        except OSError:
            _FILES = frozenset()
    return tuple(sorted(_FILES | {c for c, d in _CACHE.items() if d}))


def get(lang) -> dict:
    """The pack of ``lang`` (first two letters, lower case), ``{}`` when the language has none."""
    code = str(lang or "").strip().lower()[:2]
    return _load(code) if code else {}


def words(lang, table: str) -> list:
    """The regexes of word list ``table`` for ``lang`` ([] when no data)."""
    v = (get(lang).get("words") or {}).get(table)
    return [str(p) for p in v] if isinstance(v, list) else []


def phrases(lang, key: str) -> list:
    v = get(lang).get(key)
    return [str(p) for p in v] if isinstance(v, list) else []


def value(lang, key: str, default=None):
    v = get(lang).get(key)
    return default if v is None else v


def block(lang, name: str) -> dict:
    """The mapping ``name`` of ``lang``'s pack ({} when the pack has none)."""
    b = get(lang).get(name)
    return b if isinstance(b, dict) else {}


def _strs(v) -> list:
    if isinstance(v, str):
        return [v] if v else []
    return [str(x) for x in v if isinstance(x, str) and x] if isinstance(v, (list, tuple)) else []


def pattern_list(key: str, langs=None) -> list:
    """The regexes of ``patterns[key]`` of ``langs`` (None: every language with a pack, sorted), in language order."""
    out = []
    for lg in (languages() if langs is None else tuple(str(x)[:2].lower() for x in langs)):
        out += _strs(block(lg, "patterns").get(key))
    return out


_RX: dict = {}


def rx(key: str, flags: int = 0, langs=None, **fmt):
    """One compiled regex joining ``patterns[key]`` of ``langs`` (None: every language that has the key), or None when no
    pack has it. ``fmt``: a ``{name}`` field of a pattern is replaced by the escaped value. Same patterns, same object."""
    pats = pattern_list(key, langs)
    if not pats:
        return None
    if fmt:
        for k, v in fmt.items():
            pats = [p.replace("{" + k + "}", re.escape(str(v))) for p in pats]
    src = pats[0] if len(pats) == 1 else "|".join(f"(?:{p})" for p in pats)
    k = (src, flags)
    if k not in _RX:
        try:
            _RX[k] = re.compile(src, flags)
        except re.error:
            _RX[k] = None
    return _RX[k]


def grammar(lang, key: str, default=None):
    """``grammar[key]`` of ``lang``'s pack."""
    v = block(lang, "grammar").get(key)
    return default if v is None else v


def grammar_all(key: str, langs=None) -> list:
    """The strings of ``grammar[key]`` over ``langs`` (None: every language with a pack), in language order, duplicates kept."""
    out = []
    for lg in (languages() if langs is None else tuple(str(x)[:2].lower() for x in langs)):
        out += _strs(block(lg, "grammar").get(key))
    return out


def dialogue_all(key: str, langs=None) -> list:
    """The strings of ``dialogue[key]`` over ``langs`` (None: every language with a pack), in language order."""
    out = []
    for lg in (languages() if langs is None else tuple(str(x)[:2].lower() for x in langs)):
        out += _strs(block(lg, "dialogue").get(key))
    return out


def script_rx(lang):
    """The compiled ``script.letters`` of ``lang`` (None when its pack declares no script)."""
    letters = block(lang, "script").get("letters")
    if not letters:
        return None
    k = (str(letters), 0)
    if k not in _RX:
        try:
            _RX[k] = re.compile(str(letters))
        except re.error:
            _RX[k] = None
    return _RX[k]


def script_langs() -> list:
    """[(code, compiled letters)] of every pack that declares a script, in language order."""
    return [(lg, script_rx(lg)) for lg in languages() if script_rx(lg) is not None]


def in_script(text, lang) -> bool:
    """Does ``text`` hold a letter of ``lang``'s script? False when its pack declares none."""
    r = script_rx(lang)
    return bool(r and r.search(str(text or "")))


def attached_langs() -> list:
    """The languages whose pack declares a script and ``grammar.noun_particles`` (particles attach to the noun)."""
    return [lg for lg, _r in script_langs() if grammar(lg, "noun_particles")]


def attached_script(text) -> bool:
    """Does ``text`` hold a letter of a language whose particles attach to the noun (so its names are found inside a word)?"""
    return any(in_script(text, lg) for lg in attached_langs())


def any_script(text) -> bool:
    """Does ``text`` hold a letter of the script of any pack that declares one?"""
    return any(r.search(str(text or "")) for _lg, r in script_langs())


def marks_rx(lang):
    """The compiled ``detect_marks`` of ``lang``'s pack (None when none)."""
    m = get(lang).get("detect_marks")
    if not m:
        return None
    k = (str(m), 0)
    if k not in _RX:
        try:
            _RX[k] = re.compile(str(m))
        except re.error:
            _RX[k] = None
    return _RX[k]


def has_marks(text) -> bool:
    """Does ``text`` hold a letter that a Latin-script pack marks as its own (``detect_marks``)?"""
    return any(r.search(str(text or "")) for r in (marks_rx(lg) for lg in languages()) if r is not None)


def latin_text(text) -> bool:
    """Is ``text`` written in Latin letters beyond ASCII (accents, tone marks)? Only when some pack declares
    ``latin_script`` (a language written so); every letter must be Latin. False for plain ASCII (callers test that first)."""
    if not any(get(lg).get("latin_script") for lg in languages()):
        return False
    letters = [c for c in str(text or "") if c.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(c, "") for c in letters)


def has_data(lang) -> bool:
    return bool(get(lang))


def number(source, key: str, default: Optional[float] = None) -> Optional[float]:
    """A number from a mapping (a manifest section); ``default`` (None = not declared) when absent or not a number."""
    v = source.get(key) if isinstance(source, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


__all__ = ["PROMPT_LANG", "prompt_lang", "is_prompt_lang", "set_prompt_lang", "languages", "get", "block", "words", "phrases", "value", "has_data",
           "number", "pattern_list", "rx", "grammar", "grammar_all", "dialogue_all", "script_rx", "script_langs", "in_script",
           "any_script", "attached_langs", "attached_script", "marks_rx", "has_marks", "latin_text"]
