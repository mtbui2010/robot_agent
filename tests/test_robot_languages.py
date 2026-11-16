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
    assert L.robot_languages("demo") == {"robot": "demo", "language": "ko", "languages": ["ko", "en"], "declared": True}


def test_unreadable_manifest_is_english_only(monkeypatch):
    L.robot_languages.cache_clear()
    def boom(pkg):
        raise ImportError("no adapter")
    monkeypatch.setattr("robot_agent.contract.load_adapter", boom)
    assert L.robot_languages("nope") == {"robot": "nope", "language": "en", "languages": [], "declared": False}
    assert L.robot_languages("") == {"robot": "", "language": "en", "languages": [], "declared": False}


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
    monkeypatch.setattr(api, "current_languages", lambda: {"robot": "demo", "language": "ko", "languages": ["ko", "en"], "declared": True, "supported": ["ko", "en"]})
    app = FastAPI(); app.include_router(api.router)
    r = TestClient(app).get("/robot/languages")
    assert r.status_code == 200 and r.json() == {"robot": "demo", "language": "ko", "languages": ["ko", "en"], "declared": True, "supported": ["ko", "en"]}


def test_a_manifest_that_names_no_language_is_not_declared(monkeypatch):
    assert Manifest.from_dict(_base()).language_declared is False
    assert Manifest.from_dict(_base(language="ko")).language_declared is True
    assert Manifest.from_dict(_base(languages=["ko", "en"], language="ko")).language_declared is True
    L.robot_languages.cache_clear()
    class _A:
        manifest = Manifest.from_dict(_base(robot="bare"))
    monkeypatch.setattr("robot_agent.contract.load_adapter", lambda pkg: _A())
    # no language in the manifest: nothing declared, the English fallback only as the default
    assert L.robot_languages("bare") == {"robot": "bare", "language": "en", "languages": [], "declared": False}


def test_the_auto_flag_is_per_run():
    L.set_run_language_auto(True)
    assert L.run_language_auto() is True
    L.set_run_language_auto(False)
    assert L.run_language_auto() is False


def _demo(monkeypatch, langs=("ko", "en", "ja")):
    L.robot_languages.cache_clear()
    class _A:
        manifest = Manifest.from_dict(_base(robot="demo", language=langs[0], languages=list(langs)))
    monkeypatch.setattr("robot_agent.contract.load_adapter", lambda pkg: _A())


def test_site_switches_languages_off_and_on(tmp_path, monkeypatch):
    _demo(monkeypatch)
    assert L.site_languages("demo", tmp_path)["languages"] == ["ko", "en", "ja"]       # no file: all on
    out = L.set_site_languages("demo", tmp_path, ["ja", "ko"])                          # manifest order kept
    assert out["languages"] == ["ko", "ja"] and out["supported"] == ["ko", "en", "ja"] and out["language"] == "ko"
    assert L.site_languages("demo", tmp_path)["languages"] == ["ko", "ja"]              # read back from the file
    out = L.set_site_languages("demo", tmp_path, ["en"])                                # the default switched off: the first one on
    assert out["language"] == "en" and out["languages"] == ["en"]
    assert L.robot_languages("demo")["languages"] == ["ko", "en", "ja"]                 # the manifest itself is untouched


def test_site_cannot_add_what_the_manifest_lacks_or_switch_all_off(tmp_path, monkeypatch):
    _demo(monkeypatch)
    with pytest.raises(ValueError, match="not in this robot's manifest: qq"):
        L.set_site_languages("demo", tmp_path, ["ko", "qq"])
    with pytest.raises(ValueError, match="at least one"):
        L.set_site_languages("demo", tmp_path, [])
    assert not (tmp_path / "languages.json").exists()


def test_a_stale_or_broken_site_file_is_ignored(tmp_path, monkeypatch):
    _demo(monkeypatch)
    (tmp_path / "languages.json").write_text('{"enabled": ["qq"]}')                     # nothing the manifest lists: all on
    assert L.site_languages("demo", tmp_path)["languages"] == ["ko", "en", "ja"]
    (tmp_path / "languages.json").write_text("{oops")
    assert L.site_languages("demo", tmp_path)["languages"] == ["ko", "en", "ja"]


def test_put_endpoint(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import robot as api
    _demo(monkeypatch)
    class _S:
        robot_pkg = "demo"; common_dir = tmp_path
    monkeypatch.setattr(api, "current", lambda: _S())
    monkeypatch.setattr("robot_agent.state.current", lambda: _S())
    app = FastAPI(); app.include_router(api.router)
    c = TestClient(app)
    r = c.put("/robot/languages", json={"languages": ["en", "ko"]})
    assert r.status_code == 200 and r.json()["languages"] == ["ko", "en"] and r.json()["supported"] == ["ko", "en", "ja"]
    assert c.get("/robot/languages").json()["languages"] == ["ko", "en"]
    assert c.put("/robot/languages", json={"languages": ["xx"]}).status_code == 400
    assert c.put("/robot/languages", json={"languages": []}).status_code == 400
