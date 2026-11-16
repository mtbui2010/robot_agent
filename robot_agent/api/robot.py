"""What the robot is, for clients that must not bundle it.

``GET /robot/languages`` -> ``{robot, language, languages, supported, declared}``: ``supported`` is what the manifest says the robot
talks in (``manifest.yaml``: ``language`` / ``languages``), ``languages`` what this site has switched on (the picker's options),
``language`` the default. ``PUT /robot/languages`` body ``{languages: [...]}`` switches the site's languages (a non-empty subset of
``supported``; saved in ``<configs>/languages.json``).
"""

from fastapi import APIRouter, HTTPException

from ..core.languages import current_languages, set_site_languages
from ..state import current
from . import models as M

router = APIRouter()


@router.get('/robot/languages', response_model=M.RobotLanguages, **M.OPEN)
def get_robot_languages():
    return current_languages()


@router.put('/robot/languages', response_model=M.RobotLanguages, **M.OPEN)
def put_robot_languages(body: M.LanguagesIn):
    st = current()
    try:
        return set_site_languages(st.robot_pkg, st.common_dir, body.languages)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
