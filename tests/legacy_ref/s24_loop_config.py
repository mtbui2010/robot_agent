"""Reference copy for step S24: the config resolution of AssistLoop EXACTLY as it was in assist_loop.py at e789b86 (lines 81-99 and
140-370, before it moved to loop_config.py): ASSIST_OPTIONS, __init__, _resolve_options_cfg, _v2_cfg, _v2_cfg_base, _um_cfg, _rm_cfg,
_r3_cfg. Only two things changed: the imports (absolute names; the module names the old bodies read as globals of assist_loop.py
are imported here), and ``super().__init__(`` in __init__ is written ``ClosedLoop.__init__(self, `` (a reference class sits in
front of AssistLoop, whose own __init__ must not run twice). Used by tests/test_loop_config.py as the differential reference;
never edit the bodies."""
import json
import os
from pathlib import Path

from robot_agent.contract import STEP_LADDER, FailureCode
from robot_agent.core.options import REGISTRY as _OPTIONS, Dispatcher
from robot_agent.core.planning import exec_integrity, role_models
from robot_agent.core.planning import search as search_belief
from robot_agent.core.planning.assist_loop import AssistLoop
from robot_agent.core.planning.budget import DEFAULT_LLM_BUDGET
from robot_agent.core.planning.loop import ClosedLoop
from robot_agent.core.planning.option_services import LoopServices
from robot_agent.observe.paths import data_dir
from robot_agent.state import current, host_id

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


class RefLoopConfig:
    def __init__(self, agent, emit=None, **overrides) -> None:
        overrides = dict(overrides)
        self.original = overrides.pop("original", None)
        overrides["planner"] = overrides.get("generator") or os.environ.get("ROBOT_AGENT_SAGE_GENERATOR", "sage")
        ClosedLoop.__init__(self, agent, emit=emit, **overrides)
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
