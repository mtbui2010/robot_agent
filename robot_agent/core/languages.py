"""The languages a robot talks in, as its manifest declares them.

``manifest.yaml`` says ``language: ko`` (the default) and, when the robot has several, ``languages: [ko, en]``.
Nothing here names a language: a robot without a manifest, or one that cannot be loaded, talks English only.
The dashboard builds its language picker from :func:`robot_languages` (``GET /robot/languages``); a run that
names no language gets :func:`default_language`.
"""

from __future__ import annotations

import logging
from functools import lru_cache

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


def default_language() -> str:
    """The running robot's default language."""
    try:
        from ..state import current
        return robot_languages(current().robot_pkg)["language"]
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
