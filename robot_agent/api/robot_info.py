"""``GET /robot/manifest``: what this robot can do and where, for a fleet manager (docs/fleet_seams.md). Read-only: it starts nothing.

``{robot, version, plan_actions, capabilities, places, site}``: the manifest's ``robot``, ``version`` and ``capabilities``, the abstract
actions a plan may use on it (``RobotView.plan_actions``, sorted), the ENV keys of the ACTIVE site (the robot's own place names, which may
differ from another robot's) and that site's name. Nothing is bundled: every value is what the running robot already loaded. 503 when the
robot package's manifest cannot be loaded.

Added keys (docs/fleet_seams.md, Step S27a): ``pack`` (its header), ``goal_kinds`` (every kind with ``feasible``, ``needs``, ``slots``) and
``question_kinds``, from the robot's resolved pack (``core/pack/report.py``). They are left out when the view has no readable pack.
"""

from fastapi import APIRouter, HTTPException

from ..state import current
from . import models as M

router = APIRouter()


def manifest_of(view, env, site) -> dict:
    """The manifest answer from a RobotView, the active site's ENV (a mapping) and the site's name. Pure."""
    m = view.manifest
    return {'robot': str(m.robot), 'version': str(m.version), 'plan_actions': sorted(view.plan_actions),
            'capabilities': dict(m.capabilities or {}), 'places': [str(k) for k in env.keys()], 'site': str(site or ''),
            **_pack_keys(view)}


def _pack_keys(view) -> dict:
    """The keys the robot's pack adds ({} for a view with no readable pack: a stub, or a pack that fails to resolve)."""
    try:
        pack = view.pack
        if pack is None:
            return {}
        from ..core.pack import report
        from ..core.planning.goals import missing_actions
        actions = set(view.plan_actions)
        return report.fields(pack, lambda kind: missing_actions({'type': kind}, actions, pack))
    except Exception:
        return {}


@router.get('/robot/manifest', response_model=M.RobotManifestPack, **M.OPEN)
def get_robot_manifest():
    from ..core.planning.robot import robot_for
    from ..skill_configs import ENV
    st = current()
    view = robot_for(st.robot_pkg)
    if view is None:
        raise HTTPException(status_code=503, detail=f'the manifest of {st.robot_pkg!r} could not be loaded')
    return manifest_of(view, dict(ENV), getattr(st, 'location', ''))
