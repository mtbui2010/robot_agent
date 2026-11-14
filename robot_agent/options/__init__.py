"""The option layer: one module per option, listed here so that the order is reviewable (docs/options.md).

Layer rule: an option imports the engine (``robot_agent.core``, ``robot_agent.contract``), never another option and never
``robot_agent.interact`` / ``api``. The engine loads this package by name through ``core/options/registry.py``; it does not
import it. A new option is a new module plus one line below.
"""

from . import ask_focus, ask_source, confirm_look, end_state, entity_cover, goal_words, hazard, hw_wait, object_words, place_find, precond_repair, reach_delivery, reopen_on_miss, search_per_object, translate_retry, unapplied_change   # noqa: F401  (importing a module registers its option)
