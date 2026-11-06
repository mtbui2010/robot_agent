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

A language with no file (or a file without the key) has no data for it: every option that needs the data does nothing
for that language (fail open). Nothing in the code names a language.
"""

from __future__ import annotations

import json
import os
from typing import Optional

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lang")
_CACHE: dict = {}
# The language the core's prompts are written in, so the language of the model's own questions and reasons ("where is
# the mug?") and of a translated reading (``Heard.english``). A contract of the prompts, not a fact about any user.
PROMPT_LANG = "en"


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


def languages() -> tuple:
    """The language codes that have a pack (sorted)."""
    try:
        files = {f[:-5] for f in os.listdir(_DIR) if f.endswith(".json") and f[:-5].isalpha()}
    except OSError:
        files = set()
    return tuple(sorted(files | {c for c, d in _CACHE.items() if d}))


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


def has_data(lang) -> bool:
    return bool(get(lang))


def number(source, key: str, default: Optional[float] = None) -> Optional[float]:
    """A number from a mapping (a manifest section); ``default`` (None = not declared) when absent or not a number."""
    v = source.get(key) if isinstance(source, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


__all__ = ["PROMPT_LANG", "languages", "get", "words", "phrases", "value", "has_data", "number"]
