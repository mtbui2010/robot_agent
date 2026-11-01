"""The direct planner's prompt, built by code each time it plans (docs/direct_prompt.md).

The direct planner (``llm_direct``; the dashboard's ``direct`` choice and the open-loop path of
``UnifiedAgent.run``) turns a command into a ``skill::input`` program with one LLM call. Its
prompt is made here from the robot, never stored or edited by hand:

* the robot: :func:`robot_prompt.describe_robot` (manifest ``description`` / ``capabilities``);
* its plan skills, one ``- skill::<input> (Action): what it does`` line each
  (:func:`robot_prompt.action_lines`, ``style="skill"``), plus the dashboard's plan skills;
* the ENV places with their aliases (``env_names.describe_env``);
* the robot's notes: manifest ``planner.notes`` (a list of short strings);
* ``k`` examples: the plans most similar to the command from the robot's seed pool (manifest
  ``planner.seed_memory``) and the verified episodes of the planner's live memory
  (``backends/examples.py``, embodiplan's ranking); ``ROBOT_AGENT_DIRECT_EXAMPLES`` (default 3),
  ``ROBOT_AGENT_DIRECT_LOO=1`` leave-one-out (old names ``ROBOT_AGENT_GUIDE_*`` still read);
* fixed answer rules and the command.

The template names no robot: every word about the robot comes from its manifest and ENV.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from . import robot_prompt

log = logging.getLogger(__name__)

# env knob -> its older name (still read when the new one is unset)
ENV_EXAMPLES = ("ROBOT_AGENT_DIRECT_EXAMPLES", "ROBOT_AGENT_GUIDE_EXAMPLES")
ENV_LOO = ("ROBOT_AGENT_DIRECT_LOO", "ROBOT_AGENT_GUIDE_LOO")
DEFAULT_K = 3

RULES = ("Answer with the program only: one step per line, written skill::input, using only the "
         "skills above and the place names exactly as listed. No explanation, no notes, no numbering.")


def _env(names: tuple) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v is not None and v.strip() != "":
            return v.strip()
    return None


def example_count() -> int:
    """``ROBOT_AGENT_DIRECT_EXAMPLES`` (alias ``ROBOT_AGENT_GUIDE_EXAMPLES``), default 3."""
    try:
        return max(0, int(_env(ENV_EXAMPLES) or DEFAULT_K))
    except ValueError:
        return DEFAULT_K


def leave_one_out_default() -> bool:
    """``ROBOT_AGENT_DIRECT_LOO=1`` (alias ``ROBOT_AGENT_GUIDE_LOO``): never the command's own task."""
    return _env(ENV_LOO) == "1"


def live_memory_path() -> Optional[str]:
    """The planner's live memory file (the one ClosedLoop records into), None outside a runtime."""
    try:
        from ...observe.paths import data_dir
        from ...state import current, host_id
        return str(data_dir("grace_memory", Path(getattr(current(), "common_dir", ".")), host_id()))
    except Exception:
        return None


# ── examples ─────────────────────────────────────────────────────────
def example_program(view, steps) -> str:
    """A plan as ``skill::input`` lines in the robot's skill names (no-ops dropped)."""
    sm = view.skill_map
    return "\n".join(f"{sm[st.get('action', '')]}::{st.get('object', '')}"
                     for st in steps or [] if st.get("action", "") in sm)


def format_examples(view, examples) -> str:
    """``Command: ...`` / ``Program:`` blocks of the retrieved examples."""
    return "\n\n".join(f"Command: {ex['task']}\nProgram:\n{example_program(view, ex['steps'])}"
                       for ex in examples)


def similar_examples(view, command: str = "", k: Optional[int] = None, leave_one_out: Optional[bool] = None,
                     pool=None, live_path: Optional[str] = None) -> list:
    """The ``k`` examples most similar to ``command``: from ``pool`` (``[{task, steps}]``) when
    given, else the robot's seed pool plus the verified live memory (``live_path``, default the
    runtime's)."""
    from .backends import examples as ex
    k = example_count() if k is None else int(k)
    loo = leave_one_out_default() if leave_one_out is None else bool(leave_one_out)
    if pool is not None:
        return ex.rank(command or "", pool, k, loo)
    live = live_path if live_path is not None else live_memory_path()
    return ex.similar_examples(command or "", view.seed_memory(), live, k, loo)


# ── the robot's parts ────────────────────────────────────────────────
def places_text(env: Any = None) -> str:
    """One line per ENV place with its aliases (``env``: default the robot's live ENV)."""
    from ...env_names import describe_env
    if env is None:
        from ... import skill_configs
        env = getattr(skill_configs, "ENV", None) or {}
    return describe_env(env)


def plan_skills_text() -> str:
    """The dashboard's plan skills (one line each), '' when there are none or no runtime."""
    try:
        from ...state import current
        from ..plan_skill import describe_plan_skills
        return describe_plan_skills(current().sr)
    except Exception:
        return ""


def robot_notes(view) -> list:
    """The manifest's ``planner.notes``: short strings, one per line in the prompt."""
    notes = (getattr(view.manifest, "planner", None) or {}).get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    return [" ".join(str(n).split()) for n in notes if isinstance(n, str) and n.strip()]


# ── the prompt ───────────────────────────────────────────────────────
def _sections(view, places: str, examples: list) -> list:
    out = [f"You plan tasks for this robot: {robot_prompt.describe_robot(view)}",
           "Skills (write a step as skill::input):\n" + "\n".join(robot_prompt.action_lines(view, style="skill"))]
    extra = plan_skills_text()
    if extra:
        out.append("More skills (defined on the dashboard):\n" + extra)
    if places:
        out.append("Places:\n" + places)
    notes = robot_notes(view)
    if notes:
        out.append("Notes:\n" + "\n".join(f"- {n}" for n in notes))
    if examples:
        out.append("Examples:\n\n" + format_examples(view, examples))
    return out


def build(view, command: str, places: Optional[str] = None, **examples_kw) -> dict:
    """``{prompt, examples}``: the whole prompt for ``command`` and the examples it shows
    (``examples_kw``: :func:`similar_examples`' k / leave_one_out / pool / live_path).
    ``places``: the place lines (default :func:`places_text` of the live ENV)."""
    if places is None:
        try:
            places = places_text()
        except Exception as e:                  # no ENV: the prompt goes without places
            log.warning("direct prompt: no places (%s)", e)
            places = ""
    examples = similar_examples(view, command or "", **examples_kw)
    parts = _sections(view, places, examples)
    parts.append(RULES)
    parts.append(f"Command: {' '.join(str(command or '').split())}\nProgram:\n")
    return {"prompt": "\n\n".join(parts), "examples": examples}


def build_prompt(view, command: str, places: Optional[str] = None, **examples_kw) -> str:
    """The prompt text of :func:`build`."""
    return build(view, command, places, **examples_kw)["prompt"]


def plan_answer(llm, view, command: str, **examples_kw) -> str:
    """Ask ``llm`` (a robot_agent LLM client: ``chat(prompt=...)``, recorded and replayed by
    robot_agent.observe) for the program of ``command``; its answer text."""
    return str(llm.chat(prompt=build_prompt(view, command, **examples_kw)))


# ── which planners use it ────────────────────────────────────────────
DIRECT_PLANNERS = ("direct", "llm_direct")


def default_planner() -> str:
    """The planner a request that names none runs: the direct path, unless
    ``ROBOT_AGENT_CLOSED_LOOP=1`` (then ``ROBOT_AGENT_PLANNER``, default grace)."""
    if os.environ.get("ROBOT_AGENT_CLOSED_LOOP") == "1":
        return os.environ.get("ROBOT_AGENT_PLANNER") or "grace"
    return "direct"


def uses_direct_prompt(planner: Optional[str] = None) -> bool:
    """Does ``planner`` (a /ws/agent choice; None = :func:`default_planner`) plan with this prompt?
    direct / llm_direct do; grace does not; sage only when its generator
    (``ROBOT_AGENT_SAGE_GENERATOR``) is llm_direct; a plug-in when its class says
    ``uses_direct_prompt = True``."""
    p = (planner or default_planner()).strip()
    if p in DIRECT_PLANNERS:
        return True
    if p == "sage":
        return uses_direct_prompt(os.environ.get("ROBOT_AGENT_SAGE_GENERATOR") or "grace")
    if p == "grace":
        return False
    try:
        from .registry import resolve_planner
        factory, _builtin = resolve_planner(p)
        return bool(getattr(factory, "uses_direct_prompt", False))
    except Exception:
        return False
