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
    """``{robot, language, languages}`` of ``robot_pkg``'s manifest; ``languages`` lists the default first."""
    out = {"robot": robot_pkg or "", "language": FALLBACK, "languages": [FALLBACK]}
    if not robot_pkg:
        return out
    try:
        from ..contract import load_adapter
        m = load_adapter(robot_pkg).manifest
        langs = m.supported_languages
        out.update(robot=m.robot, language=langs[0], languages=langs)
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
