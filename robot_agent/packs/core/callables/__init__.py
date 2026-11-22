"""The callables of the core pack: every module in this folder is imported and registers its ``core.*`` names with
``robot_agent.core.pack.register``. Why: a step that moves a rule here adds its own module and never edits this file.
Empty until then (no module = nothing registered)."""

from robot_agent.packs import import_siblings

import_siblings(__name__, __path__)
