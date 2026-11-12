"""STT vocabulary hint (``ROBOT_AGENT_STT_HINT``, default off): the ENV's place and room names, in the run language, go into the
transcription ``prompt``; a server that rejects the field gets the call again without it."""
import types

import numpy as np
import pytest

from robot_agent.core.planning import stt_hint

ENV = {"desk@study room": {"aliases": ["책상", "writing desk"]}, "sofa@living room": {"aliases": ["couch"]},
       "toolbox@tool_crib": {}, "mom@study room": {"person": "mom", "aliases": ["mother"]},
       "me@study room": {"speaker": True, "aliases": ["I"]}}


def test_vocabulary_has_rooms_places_and_aliases_of_the_language_without_persons():
    v = stt_hint.vocabulary(ENV, "en")
    assert v.split(", ")[:3] == ["study room", "living room", "tool crib"]
    assert {"desk", "writing desk", "couch", "toolbox"} <= set(v.split(", "))
    assert "mom" not in v and "mother" not in v and "책상" not in v           # no persons / speaker, no other script


def test_vocabulary_in_another_script_takes_that_scripts_aliases():
    assert "책상" in stt_hint.vocabulary(ENV, "ko") and "couch" not in stt_hint.vocabulary(ENV, "ko")


def test_vocabulary_is_bounded_deduplicated_and_cut_at_a_name():
    env = {f"item{i}@room": {"aliases": ["Item0", "item0"]} for i in range(200)}
    v = stt_hint.vocabulary(env, "en", 100)
    assert len(v) <= 100 and not v.endswith(",") and len(set(w.lower() for w in v.split(", "))) == len(v.split(", "))
    assert stt_hint.vocabulary({}, "en") == "" and stt_hint.vocabulary(ENV, "en", 0) == ""


def test_merge_keeps_the_callers_prompt_first_and_does_not_repeat_words():
    assert stt_hint.merge("kitchen, mug", "kitchen, desk") == "kitchen, mug desk"
    assert stt_hint.merge(None, "desk, sofa") == "desk, sofa" and stt_hint.merge("a", "") == "a" and stt_hint.merge(None, "") is None


def test_enabled_only_by_the_flag():
    assert not stt_hint.enabled({}) and not stt_hint.enabled({"ROBOT_AGENT_STT_HINT": "0"})
    assert stt_hint.enabled({"ROBOT_AGENT_STT_HINT": "on"})
    assert stt_hint.max_chars({"ROBOT_AGENT_STT_HINT_CHARS": "90"}) == 90 and stt_hint.max_chars({}) == 400


class FakeStt:
    last_dropped = ""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, lang=None, prompt=None, mime="audio/wav"):
        self.calls.append(prompt)
        return "study room"


@pytest.fixture
def stt(monkeypatch):
    import robot_agent.skill_configs as sc
    import robot_agent.state as state
    fake = FakeStt()
    monkeypatch.setattr(sc, "ENV", ENV)
    monkeypatch.setattr(state, "current", lambda: types.SimpleNamespace(dm=types.SimpleNamespace(stt_client=lambda name=None: fake)))
    return fake


def test_speech_to_text_sends_no_vocabulary_by_default(stt, monkeypatch):
    from robot_agent.utils import speech_to_text
    monkeypatch.delenv("ROBOT_AGENT_STT_HINT", raising=False)
    speech_to_text(b"x", lang="en", prompt="hello", mime="audio/webm")
    assert stt.calls == ["hello"]


def test_dashboard_listen_audio_carries_the_vocabulary_when_enabled(stt, monkeypatch):
    from robot_agent import utils
    monkeypatch.setenv("ROBOT_AGENT_STT_HINT", "1")
    utils._LISTEN_PENDING["r1"] = {"event": utils.threading.Event(), "text": None, "error": None, "note": None, "lang": "en",
                                   "hint": None, "stt": None, "kind": "listen"}
    try:
        assert utils.transcribe_listen_audio("r1", b"audio", "audio/webm") == "study room"
    finally:
        utils._LISTEN_PENDING.pop("r1", None)
    assert stt.calls and stt.calls[0].startswith("study room, living room") and "mom" not in stt.calls[0]


class FakeResp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body or {"text": "study room"}
        self.ok = code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._b


def _client(responses, forms):
    from robot_agent.connect.stt import WhisperClient
    c = WhisperClient("http://x", "m")
    c._session = types.SimpleNamespace(post=lambda url, data, files, timeout: (forms.append(dict(data)), responses.pop(0))[1])
    return c


def test_a_server_that_rejects_the_prompt_gets_the_call_again_without_it():
    from robot_agent.connect.stt import WhisperClient
    WhisperClient._prompt_warned = False
    forms = []
    c = _client([FakeResp(422), FakeResp(200)], forms)
    assert c.transcribe(np.zeros(1600, dtype=np.float32), lang="en", prompt="study room") == "study room"
    assert "prompt" in forms[0] and "prompt" not in forms[1]


def test_a_real_server_error_is_not_retried_as_a_prompt_problem():
    forms = []
    c = _client([FakeResp(500)], forms)
    with pytest.raises(RuntimeError):
        c.transcribe(np.zeros(1600, dtype=np.float32), prompt="x")
    assert len(forms) == 1
