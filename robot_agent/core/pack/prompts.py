"""The model prompts of the shipped packs: long texts kept as files of a pack, read byte for byte.

What: a pack may hold ``prompts/<name>.<language>.txt`` in its folder. ``pick(folders, name, lang)`` is the pure rule: of the
pack folders of a chain, first to last, the LAST one that holds the file gives it (a later pack replaces an earlier one's
text, as for phrases). ``text(name)`` reads it for the chain a robot with no pack of its own gets (the root pack, then the
schema-1 base): ``assist_loop.UNDERSTAND_PROMPT`` is ``text("understand")``, the standard pack's
``prompts/understand.en.txt``.
Why: the household wording of the understand prompt was a constant of the loop; it is declared data of the standard pack now
(docs/proposals/generalize_core_design.md, Step S16b), so a robot pack can bring its own text without code. The file is
read as raw UTF-8 bytes (no newline translation, no strip): one changed character would change every understand prompt.
The text is not a ``{file: ...}`` entry of ``phrases.yaml``: the phrase files keep exactly the entries Step S04 proved
(tests/test_pack_phrases.py), so the file is found by its place in the pack folder instead.
Off: nothing to switch; with no such file ``text`` raises KeyError (the engine ships the file, so it is always there).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Iterable, Optional

from . import loader

FOLDER = "prompts"                    # the sub-folder of a pack that holds its prompt texts


def file_name(name: str, lang: str = "en") -> str:
    return f"{name}.{(lang or 'en')[:2]}.txt"


def pick(folders: Iterable, name: str, lang: str = "en") -> Optional[Path]:
    """The prompt file ``name`` in ``lang`` of the last pack folder (of ``folders``, first to last) that holds it; else None."""
    found = None
    for folder in folders:
        f = Path(folder) / FOLDER / file_name(name, lang)
        if f.is_file():
            found = f
    return found


def default_names(root=None) -> list:
    """The index names of the packs a robot with no pack of its own gets, the root pack first (as ``loader.chain_of``)."""
    idx = loader.index(root)
    names, base = [], idx.get("schema_1_base")
    while base and base not in names:
        names.append(base)
        base = loader.shipped(base, root).extends
    first = idx.get("root")
    return ([first] if first and first not in names else []) + names[::-1]


def default_folders(root=None) -> list:
    packs = loader.index(root)["packs"]
    return [loader.packs_root(root) / str((packs.get(n) or {}).get("dir", n)) for n in default_names(root)]


@functools.lru_cache(maxsize=None)
def text(name: str, lang: str = "en", root=None) -> str:
    """The prompt ``name`` in ``lang`` of the default chain, exactly as its file holds it. KeyError when no pack holds it."""
    f = pick(default_folders(root), name, lang)
    if f is None:
        raise KeyError(f"no shipped pack holds {FOLDER}/{file_name(name, lang)}")
    return f.read_bytes().decode("utf-8")


__all__ = ["FOLDER", "file_name", "pick", "default_names", "default_folders", "text"]
