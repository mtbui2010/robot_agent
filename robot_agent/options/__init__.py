"""The option layer: one module per option, listed here so that the order is reviewable (docs/options.md).

Layer rule: an option imports the engine (``robot_agent.core``, ``robot_agent.contract``), never another option and never
``robot_agent.interact`` / ``api``. The engine loads this package by name through ``core/options/registry.py``; it does not
import it. A new option is a new module plus one line below.
"""

from . import ask_next
from . import answer_align, ask_agree, ask_focus, asr_guard, ask_source, cond_extract, cond_goal, confirm_look, dedupe_keep_end, end_state, entity_cover, gate_prune, goal_ledger, goal_words, hazard, hw_wait, lang_words, name_recover, object_words, place_find, plan_complete, plan_release, precond_repair, reach_check, reach_delivery, recipient_keep, reopen_on_miss, repair_sync, replan_guard, search_budget, search_per_object, translate_retry, unapplied_change   # noqa: F401  (importing a module registers its option)
from . import trace   # noqa: F401,E402  (a new option on a line of its own: no merge on the line above)
from . import drop_search   # noqa: F401,E402  (a new option on a line of its own)
from . import heat_safety   # noqa: F401,E402  (a new option on a line of its own)
from . import bt_goal   # noqa: F401,E402  (a new option on a line of its own)
from . import heard_check   # noqa: F401,E402  (a new option on a line of its own)
from . import shared_instances   # noqa: F401,E402  (a new option on a line of its own)
from . import look_then_ask   # noqa: F401,E402  (a new option on a line of its own)
from . import bring_ask   # noqa: F401,E402  (a new option on a line of its own)
from . import fetch_guard   # noqa: F401,E402  (a new option on a line of its own)
