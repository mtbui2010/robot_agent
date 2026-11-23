"""AssistLoop — the assistive layer around a planner: understanding, clarification, a guard per step.

Planning is the planner's (``ROBOT_AGENT_SAGE_GENERATOR`` / ``generator``, default SAGE — paper: "SAGE: Symbolic
Action-Gating and Editing for LLM Task Planners"; registry name ``sage`` — or any plug-in, ``docs/planners.md``):
every plan and every change of plan comes from it. This loop never adds,
drops or reorders steps itself. Selected with planner ``'sage'`` on ``/ws/agent`` (dashboard: "SAGE + assist")
or ``ROBOT_AGENT_PLANNER=sage``; ``'sage_plain'`` is the plain ``ClosedLoop`` around the same planner.

1. **Understand** (one LLM call, JSON): the task becomes goals ``{type: on|in|open|close, object, from, to}`` or
   a refusal (``unsafe`` / ``infeasible``). Places are grounded on the live ENV without the LLM; a place word
   naming several places, a place the user never said, or "me" without a place is a question for the user
   (the robot's Ask skill, from its manifest); answers are read back ("I will … Is that right?"). A non-JSON
   answer gets one repair call; the goals are then checked against the person's words (``understand.find_issues``:
   a place / object / open-close they named and no goal uses, a destination they never said): on an issue the
   understanding is sampled again and an agreed issue-free reading is taken, else the person is asked. An LLM
   that is down ends the run with a FailureCode and a spoken reason; every run has an LLM-call budget.
2. **Plan**: the planner's ``generate_plan``; the checker (``_check``) simulates the plan as written and reports rule
   violations (wrong place, Pick without Find, Open with a full hand, goals not reached …); critical ones go back
   to the planner's sub-goal repair (optional ``SubgoalRepair``, SAGE's Editing), up to ``edit_rounds`` times,
   else one fresh plan told the findings; a plan still critical never runs.
3. **Guard each step**: preconditions before the robot moves (a violation -> a replan); after the skill
   reported success, never the preconditions again: its effects are applied and the skill's VerifySpecs run (the
   manifest's sensors: MoveTo measured against the ENV loc, Pick looked at again; a missing sensor = UNKNOWN), plus
   a Place looked at twice and a Find seen out of reach. Skill-level retries in place stay here (move again, look
   + grasp again), as do requests to the person (clear the way, move the object to the edge, where is it?).
   Anything that needs a different plan — the object is not here, the robot is at the wrong place, the plan ended
   short of the goals — goes to the planner's suffix repair (``replan``) with an E / R / S reason (``max_replans``). A failure the
   recovery policy does not retry (a skill bug, an e-stop: ``contract.recovery`` by FailureCode), a dropped object
   or the same failure twice stops the run and says why. The robot comes in only through its RobotAdapter
   (``RobotView``): skill names, sensors, geometry, what opens, its phrases.
   The policy's own actions run first: WAIT_HEALTH (``adapter.health()``, then the same step again in place),
   HEALTH_CHECK, STOP once a rule's retries are used up. The belief is reconciled through the adapter at the
   start and after a pause (``RobotView.reconcile``).
4. **Done** only when every goal holds in the belief state *and* is seen to hold: each on / in goal is looked
   at (MoveTo back + Find at its destination, ``goal_check``); one seen not to hold goes back to the planner, else the
   run fails with its FailureCode. A failed run's ``done`` carries ``code`` / ``device`` / ``evidence``. The
   object memory (``object_memory.<host>.json``) is updated on every Find / Pick / Place.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import time
import traceback
from pathlib import Path
from typing import Iterator, Optional

from robot_agent.observe.paths import data_dir
from robot_agent.contract import STEP_LADDER, FailureCode, classify_legacy_failure
from robot_agent.state import current, host_id
from .loop import _DONE, _NEXT, _STOP, ClosedLoop, reconcile_world
from . import action_model, episode_memory
from . import search as search_belief
from .search_hooks import SearchHooks
from .step_ladder import StepLadder
from .which_one import WhichOne
from .phases import PHASE_MIXINS
from .phases.ground import USER_WORDS as _USER_WORDS   # moved with the grounding; the understand rounds use it too
from .places import Memory, Places, strip_article as _strip_article, words  # noqa: F401  (moved; re-exported)
from . import langdata
from . import (action_roles, dialog_agree, dialog_converge, dialog_ext, dialog_unstick, exec_integrity, goal_filter, goal_fix, language,
               role_models, speech)
from ..options import REGISTRY as _OPTIONS, Dispatcher
from ..pack import actions as action_data, goal_semantics, prompts as pack_prompts
from ..options.dispatch import NULL as _NO_OPTIONS
from .option_services import LoopServices
from . import reconcile as sensed
from .robot import run_skill
from .verify_vlm import refuted
from .budget import DEFAULT_LLM_BUDGET, budget_end
from .sensor_health import arrival_check
from .understand import (LLMBadOutput, LLMBudget, LLMBudgetExceeded, LLMUnavailable, consensus,
                         describe, find_issues, vacuous_goal_issues, issue_question, misheard_question, sampling, typed_refusal, verb_lost,
                         without_places, signature)

_LOC_ACTIONS = set(action_data.names(action_data.default_table(), arg=("place", "container")))   # pack: actions.yaml
# per-run overrides accepted from /ws/agent `options`
ASSIST_OPTIONS = {"confirm_task", "confirm_answers", "silence_retries", "clarify_rounds", "search_max",
                "pick_retries", "ask_help", "arrive_tol", "find_min_conf", "find_verify", "reach_far", "reach_lateral",
                "goal_check", "health_wait_s", "understand_retries", "understand_samples", "llm_budget",
                "goal_readback", "understand_temperature", "understand_seed", "understand_sample_temperature",
                "yes_no_reasks", "search", "search_rank", "search_half_life_s", "vlm_verify", "sensor_health",
                "reconcile", "search_habit_weight", "search_route_weight", "edit_reserve", "plan_tidy",
                "goal_filter", "sage_subgoals", "translate_model", "translate_url", "translate_budget",
                "gate_complete", "person_seat_m", "person_surface_m", "search_total",
                "search_legs", "object_check", "detector_miss_rate", "person_goals", "accept_goals",
                "go_steps", "kind_anchor", "name_canon", "reask_abstain",
                "exec_integrity", "gate_strict", "endstate_check", "reset_container", "wait_on_robot", "inventory_ask",
                "dialog_unstick", "dialog_agree", "dialog_agree2", "relook", "kind_anchor_ask", "prompt_lang", "reask_anchor", "no_shared_listener", "understand_views",
                  "understand_goals_first", "understand_model", "understand_url", "understand_model_lang",
                "understand_max_tokens", "understand_max_goals", "understand_schema_strict", "understand_give_kind",
                "understand_verify_verdict", "understand_think", "repair_model", "repair_url", "repair_max_tokens",
                "repair_think", "dialog_model", "dialog_model_lang", "dialog_url", "dialog_max_tokens", "dialog_think",
                "request_coverage"}
ASSIST_OPTIONS = frozenset(ASSIST_OPTIONS) | _OPTIONS.all_keys()      # + the registered options' keys (core/options)


# ─────────────────────────────────────────────────────────────────────────────
# task understanding
# ─────────────────────────────────────────────────────────────────────────────
UNDERSTAND_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["clear", "ambiguous", "unsafe", "infeasible"]},
        "reason": {"type": "string"},
        # why a refusal (SAGE-v2 F4.1): the contract checks it (same call, no extra LLM call)
        "reason_type": {"type": "string", "enum": ["", "missing_skill", "payload", "size", "reach", "unknown_task",
                                                   "other"]},
        "question": {"type": "string"},
        "goals": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["on", "in", "open", "close"]},
            "object": {"type": "string"},
            # the person's own words for the object (SAGE-v2 F4.5) and, for a message, only what to pass on (F4.6)
            "said": {"type": "string"},
            "content": {"type": "string"},
            "from": {"type": "string"},
            "to": {"type": "string"}},
            "required": ["type", "object", "from", "to"]}},
    },
    "required": ["status", "reason", "question", "goals"],
}

UNDERSTAND_PROMPT = pack_prompts.text("understand")   # pack: packs/household/prompts/understand.en.txt, byte for byte


# ─────────────────────────────────────────────────────────────────────────────
# the loop
# ─────────────────────────────────────────────────────────────────────────────
class AssistLoop(*PHASE_MIXINS, WhichOne, SearchHooks, StepLadder, ClosedLoop):
    """See the module docstring."""

    _answer_source = "robot"          # where the last answer came from (interact.dialog: robot | typed | '')
    _verify_with_body = True          # F6.1: a successful Handover empties the hand in the post-step verify
    options = _NO_OPTIONS             # the registered options of this loop (core/options): set in __init__

    def __init__(self, agent, emit=None, **overrides) -> None:
        overrides = dict(overrides)
        self.original = overrides.pop("original", None)
        overrides["planner"] = overrides.get("generator") or os.environ.get("ROBOT_AGENT_SAGE_GENERATOR", "sage")
        super().__init__(agent, emit=emit, **overrides)
        env = os.environ.get
        common = Path(getattr(current(), "common_dir", "."))
        geo = self.robot.geometry
        find = self.robot.spec("Find")
        find_props = (find.params.get("properties") or {}) if find is not None else {}
        self._find_params = set(find_props)
        min_conf = (find_props.get("min_conf") or {}).get("default", 0)
        self.cfg.update({
            "clarify_rounds": int(overrides.get("clarify_rounds", env("ROBOT_AGENT_CLARIFY_ROUNDS", "3"))),
            # Find failures before asking the person where the object is (each one goes to the planner first): the ABSENT rule's retries
            "search_max": int(overrides.get("search_max", env("ROBOT_AGENT_SEARCH_MAX", self.robot.policy.rule_for(FailureCode.ABSENT).retries))),
            # searches now go through the planner's replan, so the budget is larger than the plain loop's 3
            "max_replans": int(overrides.get("max_replans", env("ROBOT_AGENT_MAX_REPLANS", "6"))),
            "pick_retries": int(overrides.get("pick_retries", self.robot.policy.rule_for(STEP_LADDER["Pick"][0]).retries)),
            # planner repairs (SubgoalRepair) of the first plan driven by the checker's findings
            "edit_rounds": int(overrides.get("edit_rounds", env("ROBOT_AGENT_EDIT_ROUNDS", "3"))),
            # LLM calls the gate's edits leave for the rest of the run (subgoal_repair.edit_reserve)
            "edit_reserve": int(overrides.get("edit_reserve", env("ROBOT_AGENT_EDIT_RESERVE", "6"))),
            # steps the action model proves do nothing are removed before the gate (plan_tidy.py)
            "plan_tidy": str(overrides.get("plan_tidy", env("ROBOT_AGENT_PLAN_TIDY", "1"))) not in ("0", "False", "false"),
            # understood goals the words do not ask for are dropped (goal_filter.py)
            "goal_filter": str(overrides.get("goal_filter", env("ROBOT_AGENT_GOAL_FILTER", "1"))) not in ("0", "False", "false"),
            "silence_retries": int(overrides.get("silence_retries", env("ROBOT_AGENT_SILENCE_RETRIES", "1"))),
            "confirm_answers": overrides.get("confirm_answers", env("ROBOT_AGENT_CONFIRM_ANSWERS", "1") == "1"),
            "confirm_task": overrides.get("confirm_task", env("ROBOT_AGENT_CONFIRM_TASK", "0") == "1"),
            # MoveTo: farther than this from the ENV loc counts as not arrived (nav "succeeded" off the spot);
            # default: the robot's geometry (manifest arrive_tol_m)
            "arrive_tol": float(overrides.get("arrive_tol", env("ROBOT_AGENT_ARRIVE_TOL", str(geo.arrive_tol_m)))),
            # Find: detections below this score are treated as not seen; sent only when the robot's Find takes a
            # `min_conf` param, default its manifest default (the robot's measurements live there)
            "find_min_conf": float(overrides.get("find_min_conf", env("ROBOT_AGENT_FIND_MIN_CONF", str(min_conf)))),
            # Find: a VLM checks the crop of each low-scoring detection (the Find skill's `verify` param, if any)
            "find_verify": overrides.get("find_verify", env("ROBOT_AGENT_FIND_VERIFY", "0") == "1"),
            # base-frame distances (manifest reach_far_m / reach_m): beyond reach_far a detection is "over there"
            # (look-and-go); a pick whose target is more than reach_lateral to the side is not retried
            "reach_far": float(overrides.get("reach_far", env("ROBOT_AGENT_REACH_FAR", str(geo.reach_far_m)))),
            "reach_lateral": float(overrides.get("reach_lateral", env("ROBOT_AGENT_REACH_LATERAL", str(geo.reach_m)))),
            "ask_help": overrides.get("ask_help", env("ROBOT_AGENT_ASK_HELP", "1") == "1"),
            # understand: repair calls after a non-JSON answer; re-samples when the goals do not match the
            # person's words (CLARA-style consistency, understand.find_issues: `goal_readback`); the run's cap
            # on core LLM calls
            "understand_retries": int(overrides.get("understand_retries", env("ROBOT_AGENT_UNDERSTAND_RETRIES", "1"))),
            "understand_samples": int(overrides.get("understand_samples", env("ROBOT_AGENT_UNDERSTAND_SAMPLES", "2"))),
            # precedence: the run's option > $ROBOT_AGENT_LLM_BUDGET > the robot's manifest planner.llm_budget >
            # the core default (budget.DEFAULT_LLM_BUDGET)
            "llm_budget": int(overrides.get("llm_budget", env("ROBOT_AGENT_LLM_BUDGET", str(
                self._manifest_planner("llm_budget", DEFAULT_LLM_BUDGET))))),

            "goal_readback": overrides.get("goal_readback", env("ROBOT_AGENT_GOAL_READBACK", "1") == "1"),
            # understand sampling (docs/dialog.md): temperature 0 + a fixed seed, so the same sentence gives the
            # same goals; the consistency re-samples need some spread (their seeds: seed + 1, seed + 2 ...)
            "understand_temperature": overrides.get("understand_temperature",
                                                    env("ROBOT_AGENT_UNDERSTAND_TEMPERATURE", "0")),
            "understand_seed": overrides.get("understand_seed", env("ROBOT_AGENT_UNDERSTAND_SEED", "0")),
            "understand_sample_temperature": overrides.get("understand_sample_temperature",
                                                           env("ROBOT_AGENT_UNDERSTAND_SAMPLE_TEMPERATURE", "0.7")),
            # an unclear answer to a yes / no question ("yet", noise) is asked again this many times
            "yes_no_reasks": int(overrides.get("yes_no_reasks", env("ROBOT_AGENT_YES_NO_REASKS", "1"))),
            # done: every on / in goal is looked at (Find at its destination, going back there when the robot
            # is elsewhere); "belief" trusts the belief as before
            "goal_check": overrides.get("goal_check", env("ROBOT_AGENT_GOAL_CHECK", "observe")),
            # recovery policy WAIT_HEALTH (camera / detector gave no image): seconds to wait for health
            "health_wait_s": float(overrides.get("health_wait_s", env("ROBOT_AGENT_HEALTH_WAIT_S", "0.5"))),
            "memory_path": Path(overrides.get("memory_path") or data_dir("object_memory", common, host_id())),  # observe.paths
            # search with a belief (search.py, docs/search.md): prior from the object memory, one LLM ranking
            # per object, searched places never again
            "search": search_belief.SearchConfig.from_env(overrides, miss_default=self._manifest_planner("detector_miss_rate", 0.1)),
            # the belief against the sensors at the start / on resume and a non-sensor `arrived` against a fresh
            # pose before a step at a place, with provenance (reconcile.py, docs/guard.md); 0: the adapter only
            "reconcile": str(overrides.get("reconcile", env("ROBOT_AGENT_RECONCILE", "1"))) not in ("0", "False", "false"),
            "generator": self.cfg["planner"],
            "planner": "sage",
        })
        self._resolve_options_cfg(overrides)
        self._reaches = lambda steps, world, goals, loop=None: AssistLoop._reaches(steps, world, goals, self)

    def _resolve_options_cfg(self, overrides) -> None:
        """The SAGE-v2, understand-model and role-model keys, then the registered options (``core/options``): their keys are
        resolved by the options' own specs and mirrored into ``self.cfg`` by their legacy names."""
        self.cfg.update(self._v2_cfg(overrides), **self._um_cfg(overrides), **self._rm_cfg(overrides))
        self.options = Dispatcher(LoopServices(self), overrides, os.environ, getattr(self.robot.manifest, "planner", None))
        self.cfg.update(self.options.mirror())

    def _v2_cfg(self, overrides) -> dict:
        """The SAGE-v2 options (docs/env.md): run option > environment > the robot's manifest > default. Round 14:
        ``dialog_agree2`` is the extension of ``dialog_agree`` and includes it."""
        cfg = self._v2_cfg_base(overrides)
        if cfg.get("dialog_agree2"):
            cfg["dialog_agree"] = True
        return cfg

    def _v2_cfg_base(self, overrides) -> dict:
        env = os.environ.get
        return {
            # the planner's sub-goals: one per understood goal ("goals", no decompose call) or the planner's own
            # decomposition ("model", SAGE before v2)
            "sage_subgoals": str(overrides.get("sage_subgoals", env("ROBOT_AGENT_SAGE_SUBGOALS", "goals"))),
            # plan_repair operator 6 (the gate writes a missing goal's segment): off; a separate ablation row
            "gate_complete": str(overrides.get("gate_complete", env("ROBOT_AGENT_GATE_COMPLETE", "0"))),
            # SAGE-v2 search (F6.1 the belief's next look spliced in place of a failed Find, no replan; F6.2 "I don't
            # know" continues over the likely places): option > env > manifest planner.search_legs > off
            "search_legs": str(overrides.get("search_legs", env("ROBOT_AGENT_SEARCH_LEGS", str(
                self._manifest_planner("search_legs", "0"))))).lower() in ("1", "true", "yes", "on"),
            # looks in all before a search gives up (F6.2), after "I don't know" too
            "search_total": int(overrides.get("search_total", env("ROBOT_AGENT_SEARCH_TOTAL", str(
                self._manifest_planner("search_total", 8))))),
            # a person's seat / position within this of a place (F2.1, a priori arm's length) and the surface for a
            # person who said where they are (F2.1 untracked, Hall's personal-zone edge), metres
            "person_seat_m": float(overrides.get("person_seat_m", env("ROBOT_AGENT_PERSON_SEAT_M", "1.0"))),
            "person_surface_m": float(overrides.get("person_surface_m", env("ROBOT_AGENT_PERSON_SURFACE_M", "1.2"))),
            # the model that translates what the robot says into the user's language (default: the planner's), its
            # server, and its own cap of calls per run (F5.3; manifest planner.translate_*)
            "translate_model": str(overrides.get("translate_model", env("ROBOT_AGENT_TRANSLATE_MODEL",
                                                                        self._manifest_planner("translate_model", ""))) or ""),
            "translate_url": overrides.get("translate_url", env("ROBOT_AGENT_TRANSLATE_URL",
                                                                self._manifest_planner("translate_url", None))),
            "translate_budget": int(overrides.get("translate_budget", env("ROBOT_AGENT_TRANSLATE_BUDGET", str(
                self._manifest_planner("translate_budget", 10))))),
            # the language layer (language.py): what a non-English user says reaches the understand prompt as the
            # original only (none), its English translation (translate) or both; option > env > manifest
            "input_pivot": str(overrides.get("input_pivot", env("ROBOT_AGENT_INPUT_PIVOT",
                                                                self._manifest_planner("input_pivot", "none")))).lower(),
            # F4.8: a Korean request's English object names checked against the person's words (translator)
            "object_check": str(overrides.get("object_check", env("ROBOT_AGENT_OBJECT_CHECK", "1"))),
            # R4b item 2 (dialog_converge.py): a correction edits the goal it names, a rejected reading is not read
            # back again, the caps count what gained nothing; "0" = the earlier dialogue loop (the ablation)
            "dialog_converge": str(overrides.get("dialog_converge", env("ROBOT_AGENT_DIALOG_CONVERGE", "1"))),
            # solution-lab round 1 (default OFF): a request that mixes go / say / deliver goals with moves is planned
            # with the template goals as the loop's own fixed steps, the planner expands only the moves, a hand-over
            # goes to the person its goal names, a duplicated goal is dropped, an Open of what cannot be opened goes
            "person_goals": str(overrides.get("person_goals", env("ROBOT_AGENT_PERSON_GOALS", str(
                self._manifest_planner("person_goals", "0"))))).lower() in ("1", "true", "yes", "on"),
            # accept goals from the caller (default OFF): run_blocking(task, goals=[...]) skips the understand step and
            # starts from the goals it is given (grounded on ENV, not filtered); no goals given = the usual understanding
            "accept_goals": str(overrides.get("accept_goals", env("ROBOT_AGENT_ACCEPT_GOALS", str(
                self._manifest_planner("accept_goals", "0"))))).lower() in ("1", "true", "yes", "on"),
            # solution-lab round 2 (all default OFF; off = the earlier grounding byte for byte):
            #   go_steps      a go goal that is only a step (to a person, the speaker, a listener, the named source of a
            #                 later move) is dropped from the end states
            #   kind_anchor   an open goal the person's words carry no open verb for is dropped
            #   name_canon    an English object name is mapped onto the robot's object memory (several places: asks which)
            #   reask_abstain a goal-less "where is it" abstention is understood once more with the robot's Find stated
            **{k: str(overrides.get(k, env("ROBOT_AGENT_" + k.upper(), str(self._manifest_planner(k, "0"))))).lower()
               in ("1", "true", "yes", "on") for k in ("go_steps", "kind_anchor", "name_canon", "reask_abstain")},
            # solution-lab round 3 (all default OFF; off = the earlier behaviour byte for byte), exec_integrity.py:
            # ``exec_integrity`` sets the four execution options; ``inventory_ask`` asks which of the robot's own
            # remembered instances (name_canon made general: canonical language and plural folding are pack data)
            **self._r3_cfg(overrides),
            # solution-lab round 6 (all default OFF; off = the earlier behaviour byte for byte), docs/env.md:
            #   reask_anchor            the abstention re-ask (as reask_abstain) takes its new reading only when every place
            #                           in it is named in the person's words
            #   no_shared_listener      the recipient of a delivery is not moved to the speaker when the words name a person once
            #   understand_views        a non-English request is read from the original and from the translator's pivot; the
            #                           run acts when the two readings agree and asks "A or B?" when they differ
            #   understand_goals_first  the understand schema asks for the goals before the verdict (dialog_agree: round 12, dialog_agree.py)
            **{k: str(overrides.get(k, env("ROBOT_AGENT_" + k.upper(), str(self._manifest_planner(k, "0"))))).lower()
               in ("1", "true", "yes", "on") for k in ("reask_anchor", "no_shared_listener", "understand_views",
                                                       "understand_goals_first", "dialog_agree", "dialog_agree2", "relook")},
            # solution-lab round 4 (default OFF, dialog_unstick.py): a restatement that gives the refused reading again is
            # read back once more instead of ending the run; a "not sure" answer to a where-is question is a cue to
            # search; a question with its own apology is not asked again behind the silence apology
            "dialog_unstick": str(overrides.get("dialog_unstick", env("ROBOT_AGENT_DIALOG_UNSTICK", str(
                self._manifest_planner("dialog_unstick", "0"))))).lower() in ("1", "true", "yes", "on"),
            # kind_anchor_ask (default OFF; acts only with kind_anchor): an open goal the person's words carry no open word
            # for is kept and read back as a doubt ("I am not sure ...") instead of being dropped silently
            "kind_anchor_ask": str(overrides.get("kind_anchor_ask", env("ROBOT_AGENT_KIND_ANCHOR_ASK", str(
                self._manifest_planner("kind_anchor_ask", "0"))))).lower() in ("1", "true", "yes", "on"),
        }

    def _um_cfg(self, overrides) -> dict:
        """Lab round 7 (docs/env.md; every key fails open to today's behaviour): the model that reads the request
        (``understand_model`` / ``understand_url`` / ``understand_model_lang``), the runaway guard of a model other than the
        planner (``understand_max_tokens`` / ``understand_max_goals``; absent = no cap) and three schema / prompt flags
        (``understand_schema_strict``, ``understand_give_kind``, ``understand_verify_verdict``; default off)."""
        env = os.environ.get

        def pick(k, default=None):                # option > $ROBOT_AGENT_<KEY> > the manifest's planner.<key> > default
            return overrides.get(k, env("ROBOT_AGENT_" + k.upper(), self._manifest_planner(k, default)))

        def on(k):
            return str(pick(k, "0")).lower() in ("1", "true", "yes", "on")

        def count(k):
            try:
                n = int(pick(k, None))
            except (TypeError, ValueError):
                return None
            return n if n > 0 else None
        langs = pick("understand_model_lang", None)
        if isinstance(langs, str):
            try:
                langs = json.loads(langs)
            except ValueError:
                langs = None
        return {
            "understand_model": str(pick("understand_model", "") or "").strip(),
            "understand_url": pick("understand_url", None) or None,
            "understand_model_lang": {str(k).strip().lower(): str(v).strip() for k, v in langs.items()
                                      if str(k).strip() and str(v or "").strip()} if isinstance(langs, dict) else {},
            "understand_max_tokens": count("understand_max_tokens"),
            "understand_max_goals": count("understand_max_goals"),
            "understand_schema_strict": on("understand_schema_strict"),
            "understand_give_kind": on("understand_give_kind"),
            "understand_verify_verdict": on("understand_verify_verdict"),
            # the language the core's prompts and the model's own questions are written in (langdata.prompt_lang);
            # "" = the pack default (English)
            "prompt_lang": str(pick("prompt_lang", "") or "").strip().lower()[:2],
        }

    def _rm_cfg(self, overrides) -> dict:
        """Lab round 8 (role_models.py, docs/env.md; every key fails open to today's behaviour): a model per role (plan-gate
        repair, the robot's words in the user's language), the ``think`` switch of the role clients, and the flag
        ``request_coverage`` (default off)."""
        env = os.environ.get

        def pick(k, default=None):
            return overrides.get(k, env("ROBOT_AGENT_" + k.upper(), self._manifest_planner(k, default)))
        return {**role_models.config(pick),
                "request_coverage": str(pick("request_coverage", "0")).lower() in ("1", "true", "yes", "on")}

    def _r3_cfg(self, overrides) -> dict:
        def on(k):
            return str(overrides.get(k, os.environ.get("ROBOT_AGENT_" + k.upper(), str(self._manifest_planner(k, "0"))))
                       ).lower() in ("1", "true", "yes", "on")
        fam = on("exec_integrity")
        return {**{k: fam or on(k) for k in exec_integrity.FLAGS}, "inventory_ask": on("inventory_ask")}

    def _converge(self) -> bool:
        return str(self.cfg.get("dialog_converge", "1")).lower() not in ("0", "false", "no", "off")

    def _lab(self, flag: str, **info) -> None:
        """Record that an opt-in lab option acted in this run (``self.lab_log``: [{"flag": name, ...}]). Read by a
        harness after the run; never part of the event stream, never read by the loop. Off options never call it."""
        self.options.observe("on_lab", flag, info)                # watchers see every entry, past the cap too (trace)
        log = self.__dict__.setdefault("lab_log", [])
        if len(log) < 50:
            log.append({"flag": flag, **info})

    def _now(self) -> float:
        """Seconds on the clock of the host (``self.clock``, a callable a host with a simulated or robot clock sets), else wall time."""
        c = self.__dict__.get("clock")
        try:
            return float(c()) if callable(c) else time.time()
        except Exception:                                  # noqa: BLE001 - a broken host clock: wall time
            return time.time()

    def _role(self, role: str):
        """The robot's action for a role (manifest ``planner.action_roles``, ``action_roles.py``), or None: the declared one,
        when the robot has it. The options that splice or recognise steps read their action names here, never from code."""
        try:
            return action_roles.action_for(getattr(self.robot.manifest, "planner", None), role, self.robot.plan_actions)
        except Exception:                                  # noqa: BLE001 - no robot / manifest: no role
            return None

    def _hand_action(self):
        """The robot's action that hands the held object to a person (its first release action other than Place), or None."""
        rel = sorted(self._releases())
        return rel[0] if rel else None

    def _manifest_planner(self, key, default=None):
        """The robot's manifest ``planner.<key>`` (robot-pack planner data), else ``default``."""
        v = (getattr(self.robot.manifest, "planner", None) or {}).get(key)
        return default if v is None or v == "" else v

    def _make_planner(self):
        name, self.cfg["planner"] = self.cfg["planner"], self.cfg["generator"]
        try:
            return super()._make_planner()
        finally:
            self.cfg["planner"] = name

    # ── LLM ───────────────────────────────────────────────────────────────
    def _llm(self):
        if getattr(self, "_llm_client", None) is None:
            from ...connect.llm import init_llm_client
            self._llm_client = init_llm_client(cfg=self._llm_cfg)
        return self._llm_client

    def run_blocking(self, task: str, lang: str = "en", plan_only: bool = False, goals=None) -> Iterator[dict]:
        if self.cfg.get("prompt_lang"):
            langdata.set_prompt_lang(self.cfg["prompt_lang"])      # the manifest / option names the prompt language
        gen = self._run_blocking(task, lang, plan_only, goals)
        gen = self._with_understand_events(gen) if self._um_for(lang) else gen     # no understand model of its own: today's generator
        return self.options.tap("on_event", gen)              # options that watch every event (trace); none: the generator itself

    def _with_understand_events(self, gen):
        """The run's events plus ``understand_fallback{reason}`` (set by ``_ask_understand_routed``), each placed before the
        event the run yields next."""
        try:
            for ev in gen:
                pend, self._um_pending = self.__dict__.get("_um_pending") or [], []
                r = getattr(self, "_ctx", None)
                for f in pend:
                    yield r.ev("understand_fallback", **f) if r is not None else {"event": "understand_fallback", **f}
                yield ev
        finally:
            gen.close()

    def _heard_as(self, text) -> str:
        """A robot line of the dialogue as the person heard it (``_ask_io`` records it; the English text when
        the run is in English or the line was not said through the robot's Ask skill)."""
        return str((getattr(self, "_said_as", None) or {}).get(str(text), text))

    _LANGS = language.LANGS
    _REGISTER = language.REGISTER

    @property
    def language(self) -> "language.LanguageAdapter":
        """The language layer (core/planning/language.py): what the user says in, and what the robot says out."""
        ad = self.__dict__.get("_language")
        if ad is None:
            ad = self.__dict__["_language"] = language.LanguageAdapter(self)
        return ad

    def _translator(self):
        """The client that translates (see ``LanguageAdapter.translator``)."""
        return self.language.translator()

    def _glossary(self, text, lang) -> dict:
        """{English name in ``text``: its name in ``lang``} for the ENV places / people the text names."""
        return self.language.glossary(text, lang)

    def _announce(self, r, kind, step, action, obj, **kw):
        """A step milestone in the user's language. The robot's phrase for it is declared data (the pack's i18n); only the
        object's name is English. It is said as the robot's own phrase with the name as a slot (declared name, else the
        run's cached name, else the English name inside the sentence), so a progress line costs no translator call (it
        used to spend the translate budget the questions and the final message need). A failure with a reason in free
        text, an English run and a language with no phrase for the milestone are as before."""
        text = super()._announce(r, kind, step, action, obj, **kw)
        lg = (getattr(r, "lang", None) or "en")[:2]
        if not text or langdata.is_prompt_lang(lg) or kind == "step_fail" and kw.get("reason") or \
                speech.phrase(self.robot, kind, lg) is None:
            return text
        slots = r.announcer.slots_for(kind, **self._say(step, action, obj))
        slots["object"] = self.language.names(str(slots.get("object") or ""), lg)      # no raw ENV key reaches the user
        if slots.get("verb") and not speech._in_script(str(slots["verb"]), lg):
            return text                               # the robot has no word for this skill in the language: translated as before
        return speech.LocalText(kind, {k: v for k, v in slots.items() if k in ("object", "verb", "eul")}, text=text)

    @staticmethod
    def summary(goals, places=None):
        """The read-back "I will ..., then ...": a ``speech.LocalText`` (English text; the robot's phrases in the
        user's language, ``speech.render``). With ``places`` the keys are named as a person says them (F5.1) and a
        delivery to a person reads "bring you the cup" / "hand the cup to grandpa"."""
        nice = (lambda k: speech.say_name(places, k, "en")) if places is not None else \
            (lambda k: (k or "").replace("@", " in the "))
        ps = [AssistLoop._goal_phrase(g, places, nice) for g in goals]
        return speech.LocalText("rb_will", {"what": speech.LocalText.seq(ps)}, text="I will " + ", then ".join(str(p) for p in ps) + ".")

    @staticmethod
    def _goal_phrase(g, places, nice):
        """One goal's read-back phrase: asked of the rules object its kind names in the pack (``goal_semantics.phrase``)."""
        return goal_semantics.phrase(g, places, nice)

    # ── plan grounding + gate ──────────────────────────────────────────────
    def _ground_step(self, st, places, goals, task):
        st = dict(st)
        a = str(st.get("action") or "")
        o = str(st.get("object") or st.get("target") or "")
        st["action"] = a
        if a in _LOC_ACTIONS:
            hint = " ".join([task] + [g.get("to", "") + " " + g.get("from", "") for g in goals])
            c = places.candidates(o, hint)
            goal_places = [g.get(s) for g in goals for s in ("to", "from") if g.get(s)]
            if len(c) > 1:
                c = [k for k in c if k in goal_places] or c[:1]
            st["object"] = c[0] if c else words(o)
            st["grounded"] = bool(c)
        elif a not in action_model.ROBOT_ACTIONS:
            st["object"] = o                          # the robot's own action (Say ...): its text as is
        else:
            st["object"] = _strip_article(words(o))
            # the plan's 'cup' for the goal's 'red cup', or what was said ('grin book') for the renamed goal 'book'
            for g in goals:
                if st["object"] and st["object"] != g["object"] and (st["object"] in g["object"].split() or st["object"] == words(g.get("said") or "")):
                    st["object"] = g["object"]
            if getattr(self, "cfg", {}).get("gate_strict"):           # round 3: a token subset of exactly one goal object (any length)
                bound = exec_integrity.bind_subset(st["object"], [g.get("object") for g in goals],
                                                   str(self._manifest_planner("label_tokens", "words")))
                if bound != st["object"]:
                    self._lab("gate_strict", part="bind_subset", frm=st["object"], to=bound)
                st["object"] = bound
        return st

    def _check(self, steps, world, places, goals):
        """The plan, simulated as written: [(step index, problem, critical)] (the body is phases/gate.py)."""
        return self._check_plan(steps, world, places, goals)

    def _repair_plan(self, planner, task, obs, ground, world, places, goals, goal_txt, budget=None):
        """The planner's own sub-goal repair (optional ``SubgoalRepair``, e.g. SAGE's Editing), driven by the
        checker's findings: subgoal_repair.repair_plan. Returns (plan, [edit notes]); (None, []) for a planner
        without it. Each repair call takes one unit of ``budget`` (LLMBudgetExceeded past it)."""
        from .subgoal_repair import repair_plan
        with self._repair_scope(planner, budget):
            return repair_plan(self, planner, task, obs, ground, world, places, goals, goal_txt, budget=budget)

    def _repair_scope(self, planner, budget=None):
        """Option ``repair_model``: the planner's LLM calls inside the ``with`` go to the repair model (guarded, one fallback
        call to the planner's own model). Nothing changes when the option is off or the planner has no ``chat_override``."""
        fn = role_models.repair_chat(self, budget)
        from .base import chat_override
        co = chat_override(planner) if fn is not None else None
        return co(fn) if co is not None else contextlib.nullcontext()

    _sage_edit = _repair_plan            # pre-v2 name

    # ── goal-reach rules: asked of each goal kind's rules object in the robot's pack (core/pack/goal_semantics.py); these
    # class attributes are the seams (tests and tools patch them), the household bodies are packs/household/callables/goals.py
    def _releases(self) -> set:
        """The robot's own actions that release the held object (RobotView.release_actions: a hand-over)."""
        return goal_semantics.releases(self if isinstance(self, AssistLoop) else None)

    def _simulate_goals(self, steps, world):  # self: the loop, or None (no release actions)
        return goal_semantics.simulate(self, steps, world, AssistLoop._releases(self) if self is not None else set())

    def _on_reached(self, g, got) -> bool:
        return goal_semantics.reached_by(self, g, got)

    def _missing(self, steps, world, goals):
        return goal_semantics.missing(self, steps, world, goals, goal_semantics.Seams(self._simulate_goals, self._on_reached))

    @staticmethod
    def _reaches(steps, world, goals, loop=None) -> bool:
        """Do ``steps`` from ``world`` reach ``goals``? With ``loop`` (bound calls pass it: ``self._reaches``
        is wrapped in ``__init__``) a hand-over to a person counts too (F2.2)."""
        return goal_semantics.reaches(loop, steps, world, goals, goal_semantics.Seams(
            lambda s, w: AssistLoop._simulate_goals(loop, s, w), lambda g, got: AssistLoop._on_reached(loop, g, got)))

    def _goals_hold(self, goals, world, placed, done=(), at=None) -> bool:
        return goal_semantics.holds(self, goals, goal_semantics.Belief(world, placed, done, at))

    # ── execution helpers ─────────────────────────────────────────────────
    def _effect(self, st, world, memory, placed):
        a, o, held = st["action"], st["object"], world.holding
        action_model.apply(st, world, inplace=True)     # the symbolic effect is the shared action model's
        if a == "Find":
            memory.see(o, world.arrived, "found")
        elif a == "Pick":
            world.holding_since = time.time()
            memory.see(world.holding, "robot", "picked")
        elif a in ("Place", "PutIn") or (a in self._releases() and held):
            if held:
                where = o if a in ("Place", "PutIn") else (o or world.arrived)
                placed[held] = where; memory.see(held, where, "placed" if a in ("Place", "PutIn") else "handed over")
                if a not in ("Place", "PutIn"):
                    world.holding = None                    # the hand-over released it (manifest effects)
                    self.__dict__.setdefault("_handed", set()).add(held)
                else:
                    self.__dict__.setdefault("_handed", set()).discard(held)
            world.holding_since = None; world.holding_pose = None
        try:
            current().save_world()
        except Exception:
            pass

    # ── main: the phases of one run ───────────────────────────────────────
    def _run_blocking(self, task: str, lang: str, plan_only: bool, goals) -> Iterator[dict]:
        r = self._ctx = self._open_run(task, lang, plan_only, "sage+assist")
        self.um_fallbacks, self._um_pending, self.um_last = [], [], None     # lab round 7: this run's understand-model log
        r.given_goals = [dict(g) for g in goals if isinstance(g, dict)] if self.cfg.get("accept_goals") and goals else None
        fixed = self._reconcile(r, "start")              # adapter.reconcile + the declared sensors
        from robot_agent.skill_configs import ENV
        r.places = Places(dict(ENV), openable=r.robot.openable)
        self.lab_log = []                             # what the opt-in lab options did this run (see _lab), on_start's included
        self.options.observe("on_start", r)           # the options' per-run state
        if self.cfg.get("dialog_agree2"):
            r.places.ext = dialog_ext.build(lang, r.places, getattr(self.robot.manifest, "planner", None))
        self._env_places = r.places
        r.verifier = self._verifier(r.node, r.places.env)
        r.memory = Memory(self.cfg["memory_path"], clock=self._now)
        r.llm_budget, self._llm_unreachable = LLMBudget(self.cfg["llm_budget"]), False
        self._person_at = {}                          # {person: place they said they are at} (this run)
        self._where_asked = None                      # the person "Where are you?" was asked about (this run)
        self._handed = self.options.query("belief_set", default=set())      # objects this run handed over (release actions)
        self._tr_budget = LLMBudget(self.cfg["translate_budget"])   # translation calls: their own cap (F5.3)
        self._say_not_in_lang, self._run_lang, self._said_as = 0, lang, {}
        self.language.reset()
        r.speak = lambda text: self.language.speak(text, lang)        # every `say` of the run's events (RunContext.ev)
        self._mute(r)
        try:
            yield r.ev("task_start", say=r.announcer.announce("task_start"), task=task, run_id=r.run_id,
                       world=r.world.to_dict())
            if fixed is not None:
                yield fixed
            if (yield from self._understand_phase(r)):            # 1. understand + clarify
                return
            if (yield from self._plan_phase(r)):                  # 2. the planner plans, the checker certifies
                return
            if (yield from self._execute_phase(r)):               # 3. guard each step, recover, replan
                return
            yield self._final_check(r)                            # 4. done only when the goals are seen to hold
        except Exception as e:
            from ..run_control import is_cancel
            # a stop during an LLM / planner call (run_control.call_cancellable) ends the run at once
            yield self._finish(r, "aborted", msg="Cancelled by user") if is_cancel(e) else self._crashed(r, e)
        finally:
            self._last_budget_used = getattr(getattr(r, "llm_budget", None), "used", None)
            self._close_run(r)

    def _finish(self, r, status, msg="", **kw) -> dict:
        """The run's `done` event; a failure / refusal tells the user why (in their language). A failure
        carries the deciding FailureCode, device and evidence (``code=`` / ``device=`` / ``evidence=``, else
        the run's first failure not recovered since, else the code of `msg`)."""
        r.run.status = status; r.run.ended_at = time.time()
        kind = "done_success" if status == "success" else "done_fail"
        if status == "failed":
            kw = {**self._deciding_failure(r, msg, kw), **kw}
        say, localized = kw.pop("say", None), kw.pop("localized", False)
        if say is None and status == "failed" and getattr(kw.get("code"), "value", kw.get("code")) == FailureCode.CANCELLED.value:
            say = speech.LocalText("say_stopped")         # the person stopped the run: not the skill's error text
        elif say is None and status == "failed":
            # the run's own dialogue messages carry the last question: one plain sentence is said instead
            say = {"no answer to": speech.LocalText("say_no_answer"),
                   "the user did not confirm": speech.LocalText("say_not_confirmed")}.get(
                       next((k for k in ("no answer to", "the user did not confirm") if msg.startswith(k)), ""))
        if say is None and status in ("failed", "refused") and msg and not msg.startswith(("internal error", "plan finished")):
            # tell the user why (dropped it, can't find it, ...), without the skill's error text or the gate's findings
            say = self._say_of_msg(msg)
        if say and not localized:
            say = self._humanize(say)                         # no raw ENV key reaches the user (F5.1)
        self.options.observe("on_finish", r, kw)          # e.g. hazard: the safety refusals on every final status
        return r.ev("done", status=status, run_id=r.run_id, msg=msg,
                    say=(say if localized else self._in_lang(say, r.lang)) if say else r.announcer.announce(kind), world=r.world.to_dict(), **kw)

    def _say_of_msg(self, msg):
        """What a failure's message says to the person: its first clause, no failure code; a step's own text
        ("Place the X: the robot is at ...", "... failed again the same way") is not said, a plain sentence is."""
        say = re.split(r": [\'\"{]|: Exception| \(replan| \(searched| \(looked|: step \d|: the plan does", msg)[0].strip()
        codes = "|".join(re.escape(c.value) for c in FailureCode)
        say = re.sub(r"\s*\((?:[a-z_]+: )?(?:" + codes + r")\)", "", say)   # no failure code said (F5.1)
        if re.match(r"(?:" + "|".join(map(re.escape, self.robot.plan_actions)) + r")\b", say) or "the same way" in say:
            return speech.LocalText("say_could_not_do")
        return say

    def _humanize(self, text):
        """``text`` with every ENV key it names said as a person names it (speech.say_name, English)."""
        places = getattr(self, "_env_places", None)
        if places is None or isinstance(text, speech.LocalText) or not text:
            return text
        out = str(text)
        for k in sorted(places.keys, key=len, reverse=True):
            if ("@" in k or "_" in k) and k in out:
                out = out.replace(k, speech.say_name(places, k, "en"))
        return out

    def _llm_failed(self, r, e, what="language model") -> dict:
        """The run's end when an LLM / planner call raised (understand.LLMUnavailable) or the run's LLM-call
        budget is used: `done failed` with the FailureCode and a spoken reason, never a bare `error` event."""
        if isinstance(e, LLMBudgetExceeded):
            # the robot's own phrase (budget.budget_end), not translated again when it has one for this language
            return self._finish(r, "failed", msg=str(e), **budget_end(r.robot, r.llm_budget, r.lang))
        if not isinstance(e, LLMUnavailable):
            e = LLMUnavailable(e, what)
        self._llm_unreachable = self._llm_unreachable or e.unreachable
        return self._finish(r, "failed", msg=e.message(what), code=e.code.value, device=what,
                            evidence={"call": e.where, "error": str(e.cause)[:300]},
                            say=r.announcer.announce("llm_down") if e.unreachable else None)

    @staticmethod
    def _deciding_failure(r, msg, kw) -> dict:
        """code / device / evidence of a failed run (pops them from `kw`)."""
        out = {k: kw.pop(k) for k in ("code", "device", "evidence") if kw.get(k) is not None}
        if "code" not in out:
            open_ = [f for f in getattr(r, "failures", []) if not f.get("recovered")]
            out = {**(open_[0] if open_ else {}), **out}
            out.pop("sig", None); out.pop("recovered", None)
        out["code"] = getattr(out.get("code"), "value", out.get("code")) or classify_legacy_failure(msg).value
        r.run.failure = dict(out)                                    # the TaskRecord keeps it too
        return out

    def _note_failure(self, r, st, code, device=None, reason="", **evidence) -> None:
        """A failed step, kept until the same step succeeds (then it was recovered)."""
        r.failures.append({"sig": (st.get("action"), st.get("object")), "code": getattr(code, "value", code),
                           "device": device, "evidence": dict(step=f"{st.get('action')} {st.get('object')}",
                                                              reason=str(reason)[:300], **evidence)})

    def _cancelled(self, r) -> Optional[dict]:
        from ..run_control import cancel_requested
        return self._finish(r, "aborted", msg="Cancelled by user") if cancel_requested() else None

    def _refused_reading(self, r):
        """The goals equal a reading the person refused in a read-back: reading it back again only gets the same
        "no". Ask once which part is wrong (a choice); a second time stop. None = read it back as usual."""
        repeat_ok, r.allow_repeat = getattr(r, "allow_repeat", False), False
        if repeat_ok or not self._converge() or not r.goals \
                or dialog_converge.goal_sig(r.goals) not in getattr(r, "rejected", ()):
            return None
        if self.cfg.get("dialog_unstick") and not getattr(r, "restate_taken", False) and self._restated_by_person(r):
            # the person said the whole request again and it reads the same: one more read-back, said as such
            r.restate_taken = r.restate_readback = True
            self._lab("dialog_unstick", part="restated_reading", confirms=r.confirms)
            yield r.ev("understand_check", issues=["a restatement gave the refused reading again"], correction="restated")
            return None
        if not getattr(r, "part_asked", False):
            r.part_asked = True
            yield r.ev("understand_check", issues=["a refused reading again"], correction="repeat")
            return (yield from self._ask_clarify(r, speech.LocalText("q_which_part")))
        if self.cfg.get("dialog_agree") and not self._agree_state(r)["tried"]:
            out = yield from self._agreed_reading(r)
            if out is not None:
                return out
        yield self._finish(r, "failed", msg="the user did not confirm the task"); return _STOP

    def _unsure_data(self, r):
        """The robot pack's i18n phrases, plus (option ``dialog_agree2``) the language pack's ``dialogue_ext.answer_unsure``
        as pack data of the run's language: what ``dialog_unstick.unsure_answer`` reads."""
        phrases = getattr(getattr(self.robot, "i18n", None), "phrases", None)
        ext = getattr(r.places, "ext", None)
        if ext is None or not ext.unsure:
            return phrases
        phrases = phrases if isinstance(phrases, dict) else {}
        lg = (r.lang or "")[:2]
        by = dict(phrases.get(dialog_unstick.KEY) or {})
        by[lg] = list(by.get(lg) or ()) + list(ext.unsure)
        return {**phrases, dialog_unstick.KEY: by}

    @staticmethod
    def _agree_state(r) -> dict:
        """The history ``dialog_agree`` reads: where each reading was first read back, every refusal, the doubts asked."""
        st = r.__dict__.get("agree")
        if st is None:
            st = r.agree = {"first": {}, "refusals": [], "asked": [], "tried": False}
        return st

    # ── 2. plan: the planner generates, the checker reports, the planner repairs
    def _obs(self, r) -> str:
        txt = ("Places the robot can go to (use exactly these):\n" + "\n".join(f"- {k}" for k in r.places.keys)
               + "\nGoals: " + r.goal_txt + "\nRobot state: " + r.world.as_text())
        if r.world.arrived is None and r.world.provenance_of("arrived").get("source") == sensed.UNKNOWN:
            txt += "\nRobot position: unknown (no fresh base pose): go to a place before acting there"
        if r.searched:
            txt += "\nAlready looked (object not there): " + ", ".join(r.searched)
        return txt + self._search_obs(r)

    def _ground_all(self, r, seq) -> list:
        return [self._ground_step(x, r.places, r.goals, r.task) for x in seq]

    def _refine_plan(self, r, findings) -> list:
        """A fresh plan from the planner, told why the last one was rejected (the gate refines at most once)."""
        obs = self._obs(r) + "\nA checker rejected the previous plan:\n" + "\n".join(f"- {t}" for t in findings)
        from ..run_control import call_cancellable, is_cancel
        try:
            with self._repair_scope(r.planner, r.llm_budget):
                raw, _m = self._plan_call(r, "generate_plan", r.restated, obs, [], what="plan refinement",
                                          subgoals=self._subgoals(r))
        except LLMBudgetExceeded as e:
            r.plan_error, raw = e, []
        except Exception as e:
            if is_cancel(e):
                raise
            raw = []
        return self._ground_all(r, raw or [])

    # ── 3. execute: the sensors of the belief, the post-step check, a failed step (the step loop: phases/execute.py)
    def _sense(self, r):
        return lambda skill: run_skill(self.sr, skill, {}, r.node, raise_errors=True)

    def _reconcile(self, r, when):
        """The adapter's reconcile, then the sensors with provenance (cfg reconcile; reconcile.py): the
        ``reconcile`` event when the belief changed."""
        if not self.cfg["reconcile"]:
            return super()._reconcile(r, when)
        from robot_agent.skill_configs import ENV
        r.reconciled = {}
        changed = r.robot.reconcile(r.world, r.node, sense=self._sense(r), places=dict(ENV),
                                    arrive_tol=self.cfg["arrive_tol"], report=r.reconciled)
        if not changed:
            return None
        self._save_world()
        return r.ev("reconcile", when=when, changed=changed, sensors=r.reconciled,
                    provenance=r.world.provenance_view(), world=r.world.to_dict())

    @staticmethod
    def _save_world():
        try:
            current().save_world()
        except Exception:
            pass

    def _post_check(self, r, st, skill, res, params, rec) -> list:
        """After a reported success, the effects + the skill's VerifySpecs (manifest: Pick looked at again
        once the arm is folded, MoveTo measured against the ENV loc) and a stale pose; then the robot pack's
        step verifiers (phases/verify_goals.py: the standard pack looks at a release twice and fails a Find
        seen out of reach)."""
        vres = r.verifier.verify(st, res, r.world, r.node, params=params)
        vres += arrival_check(r.robot, r.node, st, vres, self.cfg["sensor_health"])   # a stale pose: not arrived
        return self._step_checks(r, st, skill, res, rec, vres)

    def _step_failed(self, r, st, skill, rec, res, reason):
        """A failed step: stop (policy / same failure again), recover in place, or replan."""
        a, o = st["action"], st["object"]
        rec.status = "failed"; r.run.steps.append(rec)
        yield r.ev("step_done", index=r.i, status="failed", result=rec.result, reason=reason,
                   say=self._announce(r, "step_fail", st, a, o, reason=reason),
                   world=r.world.to_dict())
        if st.get("verify_goal"):
            r.world.found = None; r.i += 1              # not there: the goal does not hold yet (replan at the end)
            return _NEXT
        if self.cfg.get("reset_container") and a == "PutIn":
            out = yield from self._reset_container(r, st, res, reason)
            if out is not None:
                return out
        # ── stop at once: the recovery policy does not retry it (a skill bug, an e-stop), or the same
        # failure twice ──
        code = self.options.query("failure_code", r, a, base := r.robot.failure_code(res, reason), default=base)     # e.g. fetch_guard: a silent Find miss is ABSENT
        rule = r.robot.policy.rule_for(code)
        device = res.device or skill
        self._note_failure(r, st, code, device, reason)
        out = yield from self.options.decide_flow("on_step_failed", r, st, code, reason, a, o)   # e.g. reach_delivery: the person left
        if out is not None:
            return out
        if not rule.retry_allowed:
            what = "internal error in" if code is FailureCode.SOFTWARE else f"{code.value} in"
            yield self._finish(r, "failed", msg=f"{what} the {skill} skill (not retried): {reason}", code=code,
                               device=device, evidence=r.failures[-1]["evidence"]); return _STOP
        out = yield from self._apply_policy(r, st, skill, code, rule, reason, device)
        if out is not None:
            return out
        sig = (a, o, r.world.arrived, re.sub(r"[-\d.]+", "#", str(reason))[:160])
        r.seen_fail[sig] = r.seen_fail.get(sig, 0) + 1
        # Find has its own budget (search_max misses -> ask where -> stop); this stop kept it from the question (sim 2026-10-23)
        if r.seen_fail[sig] > 2 and a != "Find" and not self._search_open(r, a):
            yield self._finish(r, "failed", msg=f"{a} {o} failed again the same way: {reason}"); return _STOP
        # ── skill-level retry in place / ask the person (not planning) ──
        # only a sensor check disagrees with the skill's own success (the layers before it passed)
        r.failed_layers = {v.layer for v in rec.verifies if not v.ok}
        r.sensor_only = bool(rec.verifies) and all(v.ok for v in rec.verifies if v.layer != "sensor") \
            and any(v.layer == "sensor" and not v.ok for v in rec.verifies)
        no = refuted(rec.verifies)                     # the VLM / its Find said it did not happen: replan
        # the pack's recovery policy of the step (phases/recover.py) runs one of the engine's ladders, which it gets by name
        ladders = {"find": self._recover_find, "pick": self._recover_pick, "move": self._recover_move,
                   "search_open": self._recover_open, "release": self._recover_place}
        out, extra = (yield from self._run_policy(r, st, reason, rule, ladders)) if no is None else (None, self._refuted(r, st, no))
        if out is not None:
            return out
        # ── a new plan: the planner's suffix repair ──
        return (yield from self._replan_or_fail(r, st, f"{reason}{extra}", f"{a} {o} failed: {reason} (replan budget used)"))
