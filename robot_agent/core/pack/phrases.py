"""The default phrase data: the English texts the engine says when nobody gave a better one, read from the shipped packs.

What: the ``phrases`` section of the standard pack's chain (the core pack, then the standard pack: two files,
``packs/core/phrases.yaml`` and ``packs/household/phrases.yaml``). ``union(decls, lang)`` is the pure rule: the text of every
phrase key in one language, a later pack replacing an earlier one's key. ``view()`` is the English table that
``speech.CORE_PHRASES`` shows; ``text(name)`` is one of the single texts the engine says as they are (an entry named
``text.<name>``: the help questions of the step ladders, the run-budget sentence).
Why: the household sentences were a constant in ``core/planning/speech.py``; they are declared, labelled data of a pack now
(docs/proposals/generalize_core_design.md, "Phrases"), and a language other than English can be added to a pack without code.
Lookup order, unchanged: the robot's own i18n, the language pack's ``speech`` block, then this chain, and it speaks a language
only when its files hold that language (so a Korean run still goes to the translator for a key with no Korean text).
Off: nothing to switch; delete a key from a file and the phrase falls back to its own key as text, as an unknown key does.
"""

from __future__ import annotations

import functools
from typing import Iterable, Optional

from . import loader

TEXT_PREFIX = "text."                 # an entry named text.<name> is a single text, not a phrase key


def union(decls: Iterable, lang: str) -> dict:
    """{key: text} in ``lang`` over the packs of a chain, first to last: a later pack replaces the text of an earlier one.
    A key that has no ``lang`` text, or no text at all, is left out."""
    out: dict = {}
    for d in decls:
        for key, entry in (d.tables.get("phrases") or {}).items():
            text = entry.get(lang) if isinstance(entry, dict) else None
            if isinstance(text, str) and text:
                out[key] = text
    return out


def repeated(decls: Iterable) -> list:
    """The phrase keys that more than one pack of the chain declares (the shipped files declare each key once)."""
    seen: dict = {}
    for d in decls:
        for key in d.tables.get("phrases") or {}:
            seen[key] = seen.get(key, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


@functools.lru_cache(maxsize=None)
def default_chain(root=None) -> tuple:
    """The declarations of the packs a robot with no pack of its own gets: the root pack, then the schema-1 base."""
    base = loader.index(root).get("schema_1_base")
    return tuple(loader.chain_of(loader.shipped(base, root), root)) if base else ()


def texts(lang: str, root=None) -> dict:
    """The default text of every phrase key in ``lang`` ({} for a language the shipped files do not hold)."""
    return union(default_chain(root), (lang or "en")[:2])


def view(root=None) -> dict:
    """The English phrase table (a new dict each call): every key of the chain except the ``text.<name>`` entries."""
    return {k: t for k, t in texts("en", root).items() if not k.startswith(TEXT_PREFIX)}


def text(name: str, lang: str = "en", root=None) -> str:
    """The single text ``name`` (``text.<name>``) in ``lang``. Raises KeyError when the shipped files do not hold it."""
    return texts(lang, root)[TEXT_PREFIX + name]


def other(key: str, lang: str, root=None) -> Optional[str]:
    """The default phrase of ``key`` in a language that is not English, or None (the caller translates instead)."""
    return texts(lang, root).get(key)


__all__ = ["TEXT_PREFIX", "union", "repeated", "default_chain", "texts", "view", "text", "other"]
