"""The phrase tables as pack data: the English phrase view the core reads, and the pack-chain lookup of one phrase.

What: the core and household packs keep their phrases in ``phrases.yaml`` next to their ``pack.yaml``. ``default_phrases()`` merges
the shipped chain the same way ``loader.resolve`` does for a robot with no ``pack:`` section (the core, then the base the pack index
names), ``english_view()`` is its English text of every key WITHOUT a dot (the old ``speech.CORE_PHRASES``; a dotted key such as
``help.pick`` or ``text.llm_budget`` is another use of a text, read by ``text()``), and ``chain_text(pack, key, lang)`` is the text of
one key in one language from a resolved pack (a robot's own phrases win over the shipped ones, as every entry of a later pack does).
Why: the sentences of the household vocabulary leave the core as declared data
(docs/proposals/generalize_core_design.md, "Phrases"); a robot pack may add or replace a phrase and give it in other languages.
Off: a pack with no ``phrases`` table says nothing here; nothing is read from disk until ``default_phrases()`` is called, once.
"""

from __future__ import annotations

import functools
from typing import Any, Optional

from . import loader


@functools.lru_cache(maxsize=None)
def _default() -> tuple:
    """(the merged ``phrases`` table of the shipped chain, which pack each entry comes from), read once."""
    base = loader.shipped(loader.index().get("schema_1_base") or "")
    tables, origin, _patterns, _problems = loader.merge(loader.chain_of(base))
    return tables.get("phrases", {}), origin.get("phrases", {})


def default_phrases() -> dict:
    """The merged phrase entries of the shipped chain: {key: {"en": text, "slots": [...]}}. A copy; the caller may change it."""
    return {k: dict(v) for k, v in _default()[0].items()}


def _says(entry: Any, lang: str) -> Optional[str]:
    t = entry.get(lang) if isinstance(entry, dict) else None
    return t if isinstance(t, str) and t else None


def english_view(entries: Optional[dict] = None) -> dict:
    """{key: the English text} of the entries (default: the shipped chain) whose key has no dot: the ``CORE_PHRASES`` mapping."""
    return {k: t for k, e in (default_phrases() if entries is None else entries).items() if "." not in k and (t := _says(e, "en"))}


def text(key: str, lang: str = "en", entries: Optional[dict] = None) -> str:
    """The text of ``key`` in ``lang`` from the entries (default: the shipped chain); raises KeyError when it has none."""
    t = _says((default_phrases() if entries is None else entries).get(key), lang)
    if t is None:
        raise KeyError(f"no {lang} text for the phrase {key!r} in the pack")
    return t


def chain_text(pack: Any, key: str, lang: str) -> Optional[str]:
    """The text of ``key`` in ``lang`` in the ``phrases`` table of a resolved pack (core, base, robot: a later pack replaced an
    earlier entry whole); None when the pack is not a resolved pack, has no such key, or the entry has no text in ``lang``."""
    if not isinstance(pack, loader.Pack) or not key:
        return None
    return _says(pack.entry("phrases", key), (lang or "en")[:2])


__all__ = ["default_phrases", "english_view", "text", "chain_text"]
