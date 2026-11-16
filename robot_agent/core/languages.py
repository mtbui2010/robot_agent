"""The languages a robot talks in, as its manifest declares them.

``manifest.yaml`` says ``language: ko`` (the default) and, when the robot has several, ``languages: [ko, en]``.
Nothing here names a language: a robot without a manifest, or one that cannot be loaded, talks English only.
The dashboard builds its language picker from :func:`robot_languages` (``GET /robot/languages``); a run that
names no language gets :func:`default_language`.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("robot_agent.languages")

FALLBACK = "en"


@lru_cache(maxsize=8)
def robot_languages(robot_pkg: str) -> dict:
    """``{robot, language, languages, declared}`` of ``robot_pkg``'s manifest; ``languages`` lists the default first. A manifest
    that names no language (``declared`` False) has ``languages == []`` and the English fallback as its ``language``: the
    dashboard then offers only "detect"."""
    out = {"robot": robot_pkg or "", "language": FALLBACK, "languages": [], "declared": False}
    if not robot_pkg:
        return out
    try:
        from ..contract import load_adapter
        m = load_adapter(robot_pkg).manifest
        if m.language_declared:
            langs = m.supported_languages
            out.update(robot=m.robot, language=langs[0], languages=langs, declared=True)
        else:
            out.update(robot=m.robot)
    except Exception as e:          # no adapter / bad manifest: English only, said once (the result is cached)
        log.warning("languages of %r: manifest not read (%s); English only", robot_pkg, e)
    return out


# ── what this site switched on ────────────────────────────────────────────────────────────────────────────────
# The manifest says what the robot CAN talk in (``languages``); the site may switch some off. ``<common_dir>/languages.json``
# is ``{"enabled": ["ko", "en"]}``; no file (or nothing in it that the manifest lists) = all of them are on. A site can only
# switch the manifest's languages on or off: a language the skills have no phrases for is never added.
FILE = "languages.json"


def _enabled(common_dir) -> list | None:
    if not common_dir:
        return None
    try:
        from .shared_json import read_fresh
        raw = json.loads(read_fresh(Path(common_dir) / FILE) or "{}")
        got = raw.get("enabled") if isinstance(raw, dict) else None
        return [str(x).strip().lower() for x in got] if isinstance(got, list) else None
    except (OSError, ValueError):
        return None


def site_languages(pkg: str, common_dir=None) -> dict:
    """``{robot, language, languages, supported, declared}``: ``supported`` is the manifest's list, ``languages`` what the site has
    on (the picker's options and the languages detection may switch to), ``language`` the manifest's default, or the first
    language on when the site switched the default off."""
    out = dict(robot_languages(pkg))
    out["supported"] = list(out["languages"])
    on = _enabled(common_dir)
    keep = [l for l in out["supported"] if on is None or l in on]
    if keep and keep != out["supported"]:
        out["languages"] = keep
        if out["language"] not in keep:
            out["language"] = keep[0]
    return out


def set_site_languages(pkg: str, common_dir, codes) -> dict:
    """Switch the site's languages: ``codes`` must be a non-empty subset of the manifest's. Returns the new :func:`site_languages`;
    ValueError (the message for the client) otherwise."""
    supported = robot_languages(pkg)["languages"]
    want = [str(c).strip().lower() for c in (codes or [])]
    bad = [c for c in want if c not in supported]
    if bad:
        raise ValueError(f"not in this robot's manifest: {', '.join(bad)} (it talks in: {', '.join(supported) or 'nothing declared'})")
    if not want:
        raise ValueError("at least one language must stay on")
    path = Path(common_dir) / FILE
    tmp = path.with_name(f".{FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"enabled": [l for l in supported if l in want]}, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return site_languages(pkg, common_dir)


def current_languages() -> dict:
    """:func:`site_languages` of the running robot."""
    from ..state import current
    st = current()
    return site_languages(st.robot_pkg, getattr(st, "common_dir", None))


def default_language() -> str:
    """The running robot's default language."""
    try:
        return current_languages()["language"]
    except Exception:
        return FALLBACK


# ── "detect": the dashboard asked for the language the user speaks (lang 'auto') ─────────────────────────────────
# One run at a time, so a flag for the run in flight is enough (as the cancel flag is). Set by /ws/agent for the length of the
# run; a skill that listens reads it and then picks the language of each turn from what Whisper hears (utils.speech_to_text).
AUTO = "auto"
_RUN_AUTO = False


def set_run_language_auto(on: bool) -> None:
    global _RUN_AUTO
    _RUN_AUTO = bool(on)


def run_language_auto() -> bool:
    return _RUN_AUTO


def start_run_language(requested) -> str:
    """The language a run starts in, from the dashboard's ``lang``: none -> the manifest's default; ``'auto'`` -> the default too,
    with detection switched on for the run (the skills then follow the language each answer is heard in); else as given."""
    lang = str(requested or "").strip().lower()
    set_run_language_auto(lang == AUTO)
    return default_language() if lang in ("", AUTO) else lang
