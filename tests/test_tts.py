"""core.tts / GET /tts: the sentence as mp3 for clients that record the robot's voice."""

import pytest

from robot_agent.core import tts as T


def test_empty_and_too_long_are_refused():
    T.synthesize_mp3.cache_clear()
    with pytest.raises(T.TtsError):
        T.synthesize_mp3("  ", "en")
    with pytest.raises(T.TtsError):
        T.synthesize_mp3("x" * (T.MAX_CHARS + 1), "en")


def test_gtts_missing_or_failing_is_a_tts_error(monkeypatch):
    import builtins, sys
    T.synthesize_mp3.cache_clear()
    monkeypatch.setitem(sys.modules, "gtts", None)            # `from gtts import gTTS` -> ImportError
    with pytest.raises(T.TtsError, match="not installed"):
        T.synthesize_mp3("hello", "en")
    class _Boom:
        def __init__(self, **k): pass
        def write_to_fp(self, fp): raise ValueError("Language not supported: xx")
    monkeypatch.setitem(sys.modules, "gtts", type(sys)("gtts"))
    sys.modules["gtts"].gTTS = _Boom
    T.synthesize_mp3.cache_clear()
    with pytest.raises(T.TtsError, match="Language not supported"):
        T.synthesize_mp3("hello", "xx")


def test_mp3_bytes_and_cache(monkeypatch):
    import sys
    calls = []
    class _Fake:
        def __init__(self, text, lang): calls.append((text, lang))
        def write_to_fp(self, fp): fp.write(b"ID3fake")
    mod = type(sys)("gtts"); mod.gTTS = _Fake
    monkeypatch.setitem(sys.modules, "gtts", mod)
    T.synthesize_mp3.cache_clear()
    assert T.synthesize_mp3("안녕하세요", "ko") == b"ID3fake"
    assert T.synthesize_mp3("안녕하세요", "ko") == b"ID3fake"
    assert calls == [("안녕하세요", "ko")]                      # the second one came from the cache
    T.synthesize_mp3("hello", "EN-us")
    assert calls[-1] == ("hello", "en")                        # the code is cut to two lower-case letters


def test_endpoint(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import tts as api
    monkeypatch.setattr(api, "default_language", lambda: "ko")
    seen = []
    def fake(text, lang):
        seen.append((text, lang))
        if text == "bad":
            raise T.TtsError("no network")
        return b"MP3"
    monkeypatch.setattr(api, "synthesize_mp3", fake)
    app = FastAPI(); app.include_router(api.router)
    c = TestClient(app)
    r = c.get("/tts", params={"text": "안녕"})
    assert r.status_code == 200 and r.content == b"MP3" and r.headers["content-type"] == "audio/mpeg"
    assert seen == [("안녕", "ko")]                              # the manifest's default language
    assert c.get("/tts", params={"text": "hi", "lang": "en"}).status_code == 200 and seen[-1] == ("hi", "en")
    assert c.get("/tts", params={"text": "   "}).status_code == 400
    assert c.get("/tts", params={"text": "x" * (T.MAX_CHARS + 1)}).status_code == 400
    r = c.get("/tts", params={"text": "bad"})
    assert r.status_code == 503 and "no network" in r.json()["detail"]
