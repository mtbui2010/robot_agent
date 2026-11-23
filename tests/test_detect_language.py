"""Detecting the user's language: speech_to_text(auto=True) and the listen plumbing, with a fake Whisper."""

import pytest

from robot_agent import utils


class _Stt:
    """A fake stt client: answers by the language it is pinned to, or what the audio 'is' when unpinned."""
    def __init__(self, spoken, heard, texts):
        self.spoken, self.heard, self.texts = spoken, heard, texts          # what the user said; what Whisper hears unpinned
        self.last_dropped, self.last_language, self.calls = '', '', []

    def transcribe(self, audio, lang=None, prompt=None, mime='audio/wav', **k):
        self.calls.append(lang)
        self.last_language = lang or self.heard
        return self.texts[self.last_language]


@pytest.fixture
def stt(monkeypatch):
    holder = {}
    class _Dm:
        def stt_client(self, name=None):
            return holder["c"]
    class _Cur:
        dm = _Dm()
    monkeypatch.setattr("robot_agent.state.current", lambda: _Cur())
    return holder


def _client(heard, **texts):
    return _Stt(spoken=heard, heard=heard, texts=texts)


def test_a_clear_other_language_is_taken(stt):
    stt["c"] = _client("en", en="please bring me the noodles", ko="플리즈 브링 미")
    text = utils.speech_to_text(b"x", lang="ko", mime="audio/webm", auto=True, candidates=["ko", "en"])
    assert text == "please bring me the noodles" and utils.last_stt_language() == "en"
    assert stt["c"].calls == [None]                                   # one unpinned call, no retry


def test_a_short_phrase_keeps_the_language_in_use(stt):
    stt["c"] = _client("en", en="Nay", ko="네")
    text = utils.speech_to_text(b"x", lang="ko", auto=True, candidates=["ko", "en"])
    assert text == "네" and utils.last_stt_language() == "ko"         # heard unpinned as 'en', too short: transcribed again as ko
    assert stt["c"].calls == [None, "ko"]


def test_a_language_the_robot_does_not_talk_in_is_ignored(stt):
    stt["c"] = _client("ja", ja="ナマエ ハ ナニ デスカ", ko="이름이 뭐예요 정말로")
    text = utils.speech_to_text(b"x", lang="ko", auto=True, candidates=["ko", "en"])
    assert text == "이름이 뭐예요 정말로" and utils.last_stt_language() == "ko"
    assert stt["c"].calls == [None, "ko"]


def test_no_candidates_means_any_language(stt):
    stt["c"] = _client("ja", ja="ナマエ ハ ナニ デスカ ありがとう", ko="x")
    assert utils.speech_to_text(b"x", lang="ko", auto=True, candidates=None) == "ナマエ ハ ナニ デスカ ありがとう"
    assert utils.last_stt_language() == "ja"


def test_not_auto_is_pinned_as_before(stt):
    stt["c"] = _client("en", en="hello there my friend", ko="안녕 하세요 친구")
    assert utils.speech_to_text(b"x", lang="ko") == "안녕 하세요 친구"
    assert stt["c"].calls == ["ko"] and utils.last_stt_language() == "ko"


def test_the_listen_slot_carries_the_detected_language(stt):
    stt["c"] = _client("en", en="please bring me the noodles", ko="x")
    slot = {"event": utils.threading.Event(), "text": None, "error": None, "note": None, "lang": "ko", "hint": None, "stt": None,
            "keep": False, "kind": "listen", "auto": True, "candidates": ["ko", "en"]}
    utils._LISTEN_PENDING["req1"] = slot
    try:
        assert utils.transcribe_listen_audio("req1", b"audio", "audio/webm") == "please bring me the noodles"
        assert slot["detected"] == "en"
    finally:
        utils._LISTEN_PENDING.pop("req1", None)


def test_whisper_client_keeps_the_reported_language(monkeypatch):
    from robot_agent.connect.stt import WhisperClient
    class _R:
        def __init__(self, body): self.body = body
        def raise_for_status(self): pass
        def json(self): return self.body
    c = WhisperClient("http://x")
    for reported, kept in (("ko", "ko"), ("English", ""), ("", ""), ("jpn", "jpn")):
        monkeypatch.setattr(c._session, "post", lambda *a, **k: _R({"language": reported, "text": "hello there", "segments": []}))
        c.transcribe(b"x", mime="audio/webm")
        assert c.last_language == kept, reported


def test_start_run_language(monkeypatch):
    from robot_agent.core import languages as L
    monkeypatch.setattr(L, "default_language", lambda: "ko")
    assert L.start_run_language(None) == "ko" and L.run_language_auto() is False
    assert L.start_run_language("en") == "en" and L.run_language_auto() is False
    assert L.start_run_language("auto") == "ko" and L.run_language_auto() is True
    assert L.start_run_language("AUTO") == "ko" and L.run_language_auto() is True
    assert L.start_run_language("en") == "en" and L.run_language_auto() is False        # the next run clears it
    L.set_run_language_auto(False)
