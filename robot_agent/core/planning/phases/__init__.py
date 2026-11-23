"""The phase modules of AssistLoop: one mixin class per phase (understand, generate-and-certify, execute, verify, recover, answer).

What: ``PHASE_MIXINS`` lists the phase mixins, one entry per line, in method-resolution order; ``AssistLoop`` is declared as
``class AssistLoop(*PHASE_MIXINS, WhichOne, SearchHooks, StepLadder, ClosedLoop)``.
Why: a step of the core generalization moves one phase verbatim into a mixin here and adds ONE line to this tuple, instead of
editing the class line (docs/proposals/generalize_core_design.md, "Phase pipeline").
Switch off: an empty tuple (today) is the old class exactly; a step is reverted by removing its line and its module.
"""

from ..find_ladder import FindLadder
from .answer import AnswerPhase
from .exec_helpers import ExecHelpers
from .gate import GatePhase
from .ground import GroundPhase
from .recover import RecoverPhase
from .understand_call import UnderstandCall
from .verify_goals import VerifyGoals

PHASE_MIXINS: tuple = (
    ExecHelpers,
    GatePhase,
    GroundPhase,
    VerifyGoals,
    FindLadder,
    RecoverPhase,
    AnswerPhase,
    UnderstandCall,
)
