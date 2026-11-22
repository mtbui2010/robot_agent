"""The callables of the standard (household) pack: every module in this folder is imported and registers its ``std.*``
names with ``robot_agent.core.pack.register``. Why: a step that moves a household rule here adds its own module and never
edits this file. Empty until then (no module = nothing registered)."""

from robot_agent.packs import import_siblings

import_siblings(__name__, __path__)
