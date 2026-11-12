"""The speech recogniser's vocabulary hint: the names of the ENV's places and rooms, in the language of the run, as a short
list for the transcription ``prompt`` field (Whisper's initial prompt: words to expect). A recogniser that knows no vocabulary
hears "Story Room" for "study room"; with the names in its prompt it leans to them.

Facts come from the ENV the robot already has (heads, aliases, rooms) and the language layer (the script of the language); nothing is written here. Persons and speaker entries are left out (their names are private and not a place).
An ENV without names, or a language with none, gives ``""`` and the call is made as before. Enabled by ``stt_hint_enabled`` (the
environment variable ``ROBOT_AGENT_STT_HINT``, default off); the length is ``ROBOT_AGENT_STT_HINT_CHARS`` (default 400).
"""

from __future__ import annotations

import os

from . import speech

DEFAULT_CHARS = 400


def enabled(environ=None) -> bool:
    v = (os.environ if environ is None else environ).get("ROBOT_AGENT_STT_HINT", "")
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def max_chars(environ=None) -> int:
    try:
        return max(0, int((os.environ if environ is None else environ).get("ROBOT_AGENT_STT_HINT_CHARS", DEFAULT_CHARS)))
    except (TypeError, ValueError):
        return DEFAULT_CHARS


def _words(s: str) -> str:
    return " ".join(str(s or "").replace("_", " ").split())


def names(env: dict, lang: str) -> list:
    """The place and room names for ``lang``, deduplicated (case-insensitive), in ENV order: rooms first, then places."""
    out, seen = [], set()

    def add(n):
        n = _words(n)
        if n and "@" not in n and n.lower() not in seen and speech.script_ok(n, lang):
            seen.add(n.lower())
            out.append(n)
    ents = {k: e for k, e in (env or {}).items() if isinstance(e, dict) and not e.get("person") and not e.get("speaker")}
    for k in ents:
        if "@" in k:
            add(k.split("@", 1)[1])
    for k, e in ents.items():
        add(k.split("@")[0])
        for a in e.get("aliases") or []:
            add(a)
    return out


def vocabulary(env: dict, lang: str, limit: int = DEFAULT_CHARS) -> str:
    """``names`` joined by ", ", cut at a name boundary to at most ``limit`` characters."""
    out, size = [], 0
    for n in names(env, lang or "en"):
        add = len(n) + (2 if out else 0)
        if size + add > limit:
            break
        out.append(n)
        size += add
    return ", ".join(out)


def merge(prompt, vocab: str) -> str:
    """The caller's own prompt first, then the vocabulary (words already in the prompt are not repeated); None when both are empty."""
    base = str(prompt or "").strip()
    extra = ", ".join(w for w in (x.strip() for x in vocab.split(",")) if w and w.lower() not in base.lower())
    return (f"{base} {extra}".strip() if base and extra else base or extra) or None
