"""Read-only view of the direct planner's prompt (docs/direct_prompt.md).

``GET /planner/prompt?command=...[&planner=...]`` returns ``{prompt, examples, planner,
uses_prompt}``: the prompt the direct planner (``direct`` / ``llm_direct``) would send for that
command, built from the robot's manifest, the live ENV places and its memory, exactly as it is
built when it plans. Nothing is stored or edited here.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..state import current

router = APIRouter()


@router.get('/planner/prompt')
def planner_prompt(command: str = Query('', description='the command to build the prompt for'),
                   planner: Optional[str] = Query(None, description="the dashboard's planner choice "
                                                  "(direct | sage | grace | a plug-in); default: the server's"),
                   k: Optional[int] = Query(None, ge=0, le=20, description='examples (default: the env knob)'),
                   leave_one_out: Optional[bool] = Query(None, description='never the command itself')):
    from ..core.planning import direct_prompt as dp
    from ..core.planning.robot import RobotView
    try:
        view = RobotView.load(current().robot_pkg)
        out = dp.build(view, command or '', k=k, leave_one_out=leave_one_out)
    except Exception as e:
        raise HTTPException(500, f'could not build the prompt: {e}')
    p = (planner or '').strip() or dp.default_planner()
    return {'prompt': out['prompt'], 'planner': p, 'uses_prompt': dp.uses_direct_prompt(p),
            'examples': [{key: ex.get(key) for key in ('task', 'score', 'source')} for ex in out['examples']]}
