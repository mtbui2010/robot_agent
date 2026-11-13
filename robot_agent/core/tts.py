"""Text to speech as audio data, for clients that must put the robot's voice into something they record.

The dashboard's screen recording cannot capture the browser's own speech synthesis on every machine, so it asks for the
sentence as an mp3 (``GET /tts``) and plays it through its own audio graph, which it can record. gTTS needs the
internet from the machine running the backend, as the robot's speaker path (``utils.text2voice``) does.
"""

from __future__ import annotations

import io
from functools import lru_cache

MAX_CHARS = 600


class TtsError(RuntimeError):
    """The sentence could not be turned into audio (no gTTS, no network, a language gTTS does not have)."""


@lru_cache(maxsize=64)
def synthesize_mp3(text: str, lang: str) -> bytes:
    """``text`` spoken in ``lang`` ('ko', 'en', …) as mp3 bytes. A repeated sentence (the robot says the same
    lines again and again) is served from a small cache."""
    text = (text or "").strip()
    if not text:
        raise TtsError("empty text")
    if len(text) > MAX_CHARS:
        raise TtsError(f"text longer than {MAX_CHARS} characters")
    try:
        from gtts import gTTS
    except Exception as e:
        raise TtsError(f"gTTS is not installed ({e})") from e
    try:
        buf = io.BytesIO()
        gTTS(text=text, lang=(lang or "en")[:2].lower()).write_to_fp(buf)
        return buf.getvalue()
    except Exception as e:      # ValueError for an unknown language, gTTSError / network errors otherwise
        raise TtsError(f"{type(e).__name__}: {e}") from e
