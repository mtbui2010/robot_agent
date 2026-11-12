"""Manifest `language` / `languages`, the helper behind GET /robot/languages, and the run default."""

import pytest

from robot_agent.contract import Manifest, ManifestError
from robot_agent.core import languages as L


def _base(**kw):
    d = {"robot": "r", "skills": [{"name": "move", "action": "MoveTo"}]}
    d.update(kw)
    return d


def test_single_language_by_default():
    m = Manifest.from_dict(_base(language="ko"))
    assert m.languages == [] and m.supported_languages == ["ko"]
    assert Manifest.from_dict(_base()).supported_languages == ["en"]


def test_several_languages_default_first():
    m = Manifest.from_dict(_base(language="ko", languages=["en", "KO", " ja "]))
    assert m.languages == ["en", "ko", "ja"] and m.supported_languages == ["ko", "en", "ja"]
    assert m.to_dict()["languages"] == ["en", "ko", "ja"]
    assert "languages" not in Manifest.from_dict(_base(language="ko")).to_dict()
    assert "languages" not in m.extra


@pytest.mark.parametrize("bad, text", [
    (dict(language="ko", languages=["en"]), "must include the default language 'ko'"),
    (dict(languages=[]), "non-empty list"),
    (dict(languages="ko"), "non-empty list"),
    (dict(languages=["ko", "ko"]), "listed twice"),
    (dict(languages=["korean"]), "language code"),
    (dict(languages=[1]), "language code"),
])
def test_bad_languages_name_the_field(bad, text):
    with pytest.raises(ManifestError) as e:
        Manifest.from_dict(_base(**bad))
    assert "languages" in str(e.value) and text in str(e.value)


def test_robot_languages_of_a_package(monkeypatch):
    L.robot_languages.cache_clear()
    class _A:  # an adapter carrying a manifest
        manifest = Manifest.from_dict(_base(robot="demo", language="ko", languages=["ko", "en"]))
    monkeypatch.setattr("robot_agent.contract.load_adapter", lambda pkg: _A())
    assert L.robot_languages("demo") == {"robot": "demo", "language": "ko", "languages": ["ko", "en"]}


def test_unreadable_manifest_is_english_only(monkeypatch):
    L.robot_languages.cache_clear()
    def boom(pkg):
        raise ImportError("no adapter")
    monkeypatch.setattr("robot_agent.contract.load_adapter", boom)
    assert L.robot_languages("nope") == {"robot": "nope", "language": "en", "languages": ["en"]}
    assert L.robot_languages("") == {"robot": "", "language": "en", "languages": ["en"]}


def test_default_language_follows_the_running_robot(monkeypatch):
    L.robot_languages.cache_clear()
    class _S:
        robot_pkg = "demo"
    monkeypatch.setattr("robot_agent.state.current", lambda: _S())
    monkeypatch.setattr(L, "robot_languages", lambda pkg: {"robot": pkg, "language": "ko", "languages": ["ko", "en"]})
    assert L.default_language() == "ko"
    monkeypatch.setattr("robot_agent.state.current", lambda: (_ for _ in ()).throw(RuntimeError("no state")))
    assert L.default_language() == "en"


def test_endpoint_lists_the_robot_languages(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import robot as api
    class _S:
        robot_pkg = "demo"
    monkeypatch.setattr(api, "current", lambda: _S())
    monkeypatch.setattr(api, "robot_languages", lambda pkg: {"robot": pkg, "language": "ko", "languages": ["ko", "en"]})
    app = FastAPI(); app.include_router(api.router)
    r = TestClient(app).get("/robot/languages")
    assert r.status_code == 200 and r.json() == {"robot": "demo", "language": "ko", "languages": ["ko", "en"]}
