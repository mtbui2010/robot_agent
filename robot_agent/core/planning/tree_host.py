"""Pure helpers of the bt_goal tree host: the opt-in keys of a tree entry, the public node event, the choice words of a pack question, and
the answer of a pack question kept as a fact.

What: ``tree_keys(entry)`` reads the two opt-in keys that sit next to ``tree:`` in a declared tree entry (``ladder: false``: the tree's
steps do not run the engine's recovery ladder, the tree decides; ``events: true``: every node is sent as a ``tree_node`` event);
``node_event`` builds that event's fields; ``pack_words`` reads the words of a ``block.key`` list of the language pack and, when a ref has
no such list, the words of one choice of a question of the robot's pack (ref ``<question>.<choice>``: ``answer.choices.<choice>.words``
of the language, and its ``lists``). ``question_of`` / ``read_choice`` / ``keep_answer`` / ``answered`` (Step S25) let an ``ask`` leaf name a
question of the pack (``[ask, <question>]``): the answer is read as one of its choices and kept as the fact ``answered(<question>, <choice>)``
(and, with ``answer.keep_text``, its raw text as ``answer_text(<question>, <text>)``) in the belief's fact store, so a later ``cond answered``
leaf branches on the content of the answer.
Why: a robot declares the recovery of a goal in the goal's own tree and shows it in the public event stream, and a tree branches on a
choice a pack declares (docs/bt_goal.md, "Tree keys"); the seams exist already (``try_step(ladder=False)``, service ``emit``).
Switch off: an entry with neither key, a ref no pack question names, and an ``ask`` whose first argument names no pack question behave as before
(the defaults below are the old behaviour).
Pure: data in, data out; the host (options/bt_goal.py) calls the services.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from . import langdata, names

EVENT = "tree_node"                                    # the public event of a node (key ``events``)
DEFAULT_KEYS = {"ladder": True, "events": False}       # the behaviour of an entry with neither key: the engine's ladder runs, no node event
ANSWERED, ANSWER_TEXT = "answered", "answer_text"      # the facts of a pack question's answer: (question, choice) and (question, raw text)


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
    return spec_words(spec, lang)


def spec_words(spec, lang: str) -> list:
    """The words of one declared choice (``{words: {lang: [...]}, lists: [...]}``) in ``lang``; [] for anything else."""
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


def question_of(questions: Callable[[], dict], name) -> Optional[dict]:
    """The pack question ``name`` an ``ask`` leaf can put (pure): its entry when it has a phrase key and reads a ``choice`` answer, else None
    (not declared, no phrase, another answer type, a table that cannot be read: the leaf is then the old ``[ask, <phrase key>, *refs]``)."""
    try:
        spec = (questions() or {}).get(name)
    except Exception:
        return None
    answer = spec.get("answer") if isinstance(spec, dict) else None
    ok = isinstance(answer, dict) and answer.get("type") == "choice" and isinstance(spec.get("phrase"), str) and bool(spec["phrase"])
    return spec if ok else None


def _said(text: str, words: list, lang: str) -> bool:
    """Does ``text`` say one of ``words``: a word as ``names.says`` reads it, a phrase of several words as that run of whole words (case and
    punctuation do not matter)."""
    tokens = " " + " ".join(re.findall(r"\w+", str(text).lower())) + " "
    phrases = [" " + " ".join(t) + " " for t in (re.findall(r"\w+", w.lower()) for w in words if len(w.split()) > 1) if t]
    return any(p in tokens for p in phrases) or names.says([text], [w for w in words if len(w.split()) == 1], (lang,))


def read_choice(spec: dict, text: str, lang: str) -> Optional[str]:
    """The first choice of the question ``spec`` (declaration order) whose words the answer ``text`` says in ``lang`` (``_said``); None for
    silence or no word."""
    for choice, c in ((spec.get("answer") or {}).get("choices") or {}).items():
        if _said(text, spec_words(c, lang), lang):
            return choice
    return None


def has_phrase(says: Callable[[str], bool], questions: Callable[[], dict], key) -> bool:
    """Can an ``ask`` / ``reply`` leaf with first argument ``key`` be put: the robot says the phrase ``key`` (``says``), or ``key`` is a
    question of the pack whose phrase it says. The pack is read only when ``key`` is no phrase of the robot."""
    if says(key):
        return True
    spec = question_of(questions, key)
    return spec is not None and says(spec["phrase"])


def take_answer(facts, question: str, spec: dict, text: str, lang: str) -> bool:
    """Read the answer ``text`` of the pack question ``spec`` (``read_choice``) and keep it (``keep_answer``, with the question's ``keep_text``);
    True when a choice was read."""
    choice = read_choice(spec, text, lang)
    keep_answer(facts, question, choice, text, bool((spec.get("answer") or {}).get("keep_text")))
    return choice is not None


def keep_answer(facts, question: str, choice: Optional[str], text: str, keep_text: bool) -> None:
    """Keep the answer of ``question`` in the fact store ``facts`` (name -> set of tuples; changed in place; anything else is left alone): the
    earlier answer of the question is replaced, ``answered(question, choice)`` is set when a choice was read, and with ``keep_text`` and a
    non-empty answer ``answer_text(question, text)`` too."""
    if not isinstance(facts, dict):
        return
    for name, row in ((ANSWERED, choice), (ANSWER_TEXT, text if keep_text and text else None)):
        facts[name] = {t for t in facts.get(name, ()) if t[:1] != (question,)} | ({(question, row)} if row else set())


def answered(facts, args) -> bool:
    """The tree condition ``answered <question> <choice>``: that choice was read for that question (False for a wrong number of arguments)."""
    return isinstance(facts, dict) and tuple(args) in facts.get(ANSWERED, ()) and len(args) == 2


__all__ = ["EVENT", "DEFAULT_KEYS", "ANSWERED", "ANSWER_TEXT", "tree_keys", "node_event", "language_words", "choice_words", "spec_words",
           "pack_words", "question_of", "read_choice", "has_phrase", "take_answer", "keep_answer", "answered"]
