"""Pure helpers of the bt_goal tree host: the opt-in keys of a tree entry, the public node event, and the choice words of a pack question.

What: ``tree_keys(entry)`` reads the two opt-in keys that sit next to ``tree:`` in a declared tree entry (``ladder: false``: the tree's
steps do not run the engine's recovery ladder, the tree decides; ``events: true``: every node is sent as a ``tree_node`` event);
``node_event`` builds that event's fields; ``pack_words`` reads the words of a ``block.key`` list of the language pack and, when a ref has
no such list, the words of one choice of a question of the robot's pack (ref ``<question>.<choice>``: ``answer.choices.<choice>.words``
of the language, and its ``lists``).
Why: a robot declares the recovery of a goal in the goal's own tree and shows it in the public event stream, and a tree branches on a
choice a pack declares (docs/bt_goal.md, "Tree keys"); the seams exist already (``try_step(ladder=False)``, service ``emit``).
Switch off: an entry with neither key, and a ref no pack question names, behave as before (the defaults below are the old behaviour).
Pure: data in, data out; the host (options/bt_goal.py) calls the services.
"""

from __future__ import annotations

from typing import Callable, Optional

from . import langdata

EVENT = "tree_node"                                    # the public event of a node (key ``events``)
DEFAULT_KEYS = {"ladder": True, "events": False}       # the behaviour of an entry with neither key: the engine's ladder runs, no node event


def tree_keys(entry) -> dict:
    """The opt-in keys of one tree entry: ``ladder`` (False only for a literal ``false``) and ``events`` (True only for a literal ``true``);
    anything else, and a non-dict entry, gives the defaults."""
    e = entry if isinstance(entry, dict) else {}
    return {"ladder": e.get("ladder") is not False, "events": e.get("events") is True}


def node_event(goal, path: str, label: str, status: str) -> dict:
    """The fields of the ``tree_node`` event of one node: the goal's number, the node's place in the tree ("0.1.2"), what it is, its status."""
    return {"goal": goal, "path": path, "node": label, "status": status}


def _strings(words) -> list:
    return [w for w in words if isinstance(w, str) and w.strip()] if isinstance(words, list) else []


def language_words(lang: str, ref) -> list:
    """The words of the language-pack list ``ref`` (``block.key``, e.g. ``ask_next.retry``) of ``lang``; [] for a list it lacks."""
    blk, _dot, key = str(ref).partition(".")
    return _strings(langdata.block(lang, blk).get(key))


def choice_words(questions, ref, lang: str) -> list:
    """The words of the choice ``<question>.<choice>`` (``ref``) of a pack's ``questions`` table, in ``lang``: the entry's ``words[lang]`` and
    the language-pack lists named in its ``lists``; [] when the table, the question, the choice or the language is not declared."""
    name, _dot, choice = str(ref).rpartition(".")
    answer = (questions or {}).get(name) if isinstance(questions, dict) else None
    spec = ((answer.get("answer") or {}).get("choices") or {}).get(choice) if isinstance(answer, dict) else None
    if not isinstance(spec, dict):
        return []
    words = spec.get("words")
    out = _strings(words.get(lang) if isinstance(words, dict) else None)
    for ref2 in spec.get("lists") or () if isinstance(spec.get("lists"), list) else ():
        out += language_words(lang, ref2)
    return out


def pack_words(lang: str, refs, questions: Optional[Callable[[], dict]] = None) -> list:
    """The words of ``refs`` (each a ``block.key`` of the language pack): where the language pack has no such list, the choice of a pack
    question the ref names (``questions``: a function that gives the pack's ``questions`` table, called only then). [] for a ref with neither."""
    out = []
    for ref in refs or ():
        found = language_words(lang, ref)
        if not found and questions is not None:
            found = choice_words(questions(), ref, lang)
        out += found
    return out


__all__ = ["EVENT", "DEFAULT_KEYS", "tree_keys", "node_event", "language_words", "choice_words", "pack_words"]
