"""What the robot is, for clients that must not bundle it.

``GET /robot/languages`` -> ``{robot, language, languages}``: the manifest's default language and every language the
robot talks in (``manifest.yaml``: ``language`` / ``languages``). The dashboard builds its language picker from it.
"""

from fastapi import APIRouter

from ..core.languages import robot_languages
from ..state import current
from . import models as M

router = APIRouter()


@router.get('/robot/languages', response_model=M.RobotLanguages, **M.OPEN)
def get_robot_languages():
    return robot_languages(current().robot_pkg)
