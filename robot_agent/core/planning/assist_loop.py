"""AssistLoop — the assistive layer around a planner: understanding, clarification, a guard per step.

Planning is the planner's (``ROBOT_AGENT_SAGE_GENERATOR`` / ``generator``, default SAGE — paper: "SAGE: Symbolic
Action-Gating and Editing for LLM Task Planners"; code identifier GRACE — or any plug-in, ``docs/planners.md``):
every plan and every change of plan comes from it. This loop never adds,
drops or reorders steps itself. Selected with planner ``'sage'`` on ``/ws/agent`` (dashboard: "SAGE + assist")
or ``ROBOT_AGENT_PLANNER=sage``; ``'grace'`` is the plain ``ClosedLoop`` around the same planner.

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

import json
import math
import os
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Iterator, Optional

from robot_agent.observe.paths import data_dir
from robot_agent.contract import FailureCode, Recovery, Unbindable, classify_legacy_failure
from robot_agent.state import current, host_id
from .loop import _DONE, _NEXT, _STOP, ClosedLoop, _vr_list, reconcile_world
from .records import StepRecord, VerifyResult
from . import action_model, concrete, episode_memory, goal_change
from . import search as search_belief
from .search_hooks import SearchHooks
from . import goals as goal_kinds
from . import goal_filter, goal_fix, persons, plan_tidy, speech
from . import reconcile as sensed
from .robot import run_skill
from .verify import symbolic_check, verdict
from .verify_vlm import refuted
from .budget import DEFAULT_LLM_BUDGET, budget_end
from .sensor_health import arrival_check, find_precheck
from .understand import (LLMBadOutput, LLMBudget, LLMBudgetExceeded, LLMUnavailable, ask_json, consensus,
                         describe, find_issues, issue_question, misheard_question, sampling, typed_refusal,
                         without_places)

_LOC_ACTIONS = {"MoveTo", "Place", "PutIn", "Open", "Close"}
# per-run overrides accepted from /ws/agent `options`
ASSIST_OPTIONS = {"confirm_task", "confirm_answers", "silence_retries", "clarify_rounds", "search_max",
                "pick_retries", "ask_help", "arrive_tol", "find_min_conf", "find_verify", "reach_far", "reach_lateral",
                "goal_check", "health_wait_s", "understand_retries", "understand_samples", "llm_budget",
                "goal_readback", "understand_temperature", "understand_seed", "understand_sample_temperature",
                "yes_no_reasks", "search", "search_rank", "search_half_life_s", "vlm_verify", "sensor_health",
                "reconcile", "search_habit_weight", "edit_reserve", "plan_tidy",
                "goal_filter", "sage_subgoals", "translate_model", "translate_url", "translate_budget",
                "gate_complete", "hazard_rules", "person_seat_m", "person_surface_m", "search_total",
                "search_legs"}
_USER_WORDS = {"me", "user", "here", "you", "myself", "person"}


# ─────────────────────────────────────────────────────────────────────────────
# names
# ─────────────────────────────────────────────────────────────────────────────
def words(name) -> str:
    """'TennisBall' / 'tennis_ball' / ' Tennis  ball ' -> 'tennis ball'."""
    s = str(name or "").replace("_", " ").replace("-", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return " ".join(s.lower().split())


def _strip_article(s: str) -> str:
    return re.sub(r"^(the|a|an|my|your)\s+", "", s or "").strip()


def _fmt_en(key: str, **slots) -> str:
    from .speech import CORE_PHRASES
    return CORE_PHRASES[key].format(**slots)


class Places:
    """The live ENV as a closed vocabulary of places. ``openable(key, entry)``: the robot's rule
    (``RobotView.openable``); default, the ENV entry's ``openable`` flag."""

    def __init__(self, env: dict, openable=None):
        self.env = {k: v for k, v in (env or {}).items() if isinstance(v, dict)}
        self.keys = list(self.env)
        self._openable = openable

    @staticmethod
    def head(key: str) -> str:
        return key.split("@")[0].strip()

    @staticmethod
    def room(key: str) -> str:
        return key.split("@")[1].strip() if "@" in key else ""

    def rooms(self) -> set:
        return {self.room(k) for k in self.keys if self.room(k)}

    def openable(self, key: str) -> bool:
        v = self.env.get(key, {})
        if self._openable is not None:
            return bool(self._openable(key, v))
        return bool(v.get("openable"))

    def describe(self) -> str:
        out = []
        for k in self.keys:
            al = self.env[k].get("aliases") or []
            out.append(f"- {k}" + (f" (also: {', '.join(al)})" if al else ""))
        return "\n".join(out)

    def candidates(self, name: str, hint: str = "") -> list:
        """ENV keys *name* may mean. A room name -> ['@room'] marker."""
        w = _strip_article(words(name))
        if not w:
            return []
        try:
            from robot_agent.env_names import resolve_env_name
            k = resolve_env_name(w, self.env)
        except Exception:
            k = None
        if k in self.env:
            from robot_agent.env_names import normalize
            if w == k.lower() or "@" in w or w in {normalize(x) for x in self.env[k].get("aliases") or []}:
                return [k]
        # 'dining table', 'kitchen counter', 'shelf in the restroom'
        room = next((r for r in self.rooms() if r in w), "")
        rest = (w.replace(room, " ") if room else w).replace(" in the ", " ").replace(" in ", " ").replace(" at ", " ")
        # a whole answer ("It is on the kitchen counter.") grounded to the room: the filler words and the full
        # stop kept 'counter' from matching (sim 2026-10-23)
        rest = re.sub(r"[.,!?;:]", " ", rest)
        rest = " ".join(t for t in rest.split() if t not in ("the", "of", "on", "kitchen's", "it", "is", "it's",
                                                             "its", "there", "a", "an"))
        hits = [k for k in self.keys if rest and (self.head(k) == rest or rest in self.head(k)
                                                     or self.head(k) in rest.split())]
        if not hits and rest:                         # 'table' -> 'dining table', 'test table'
            hits = [k for k in self.keys if any(t == x for t in rest.split() for x in self.head(k).split())]
        if room:
            in_room = [k for k in hits if self.room(k) == room]
            if in_room:
                hits = in_room
            elif not rest or not hits:
                return ["@" + room]
        if not hits and w in self.rooms():
            return ["@" + w]
        if len(hits) > 1 and hint:
            # the user's own words name one of them: an alias (화장실 선반) or the room
            ht = words(hint)
            by_alias = [k for k in hits if any(words(a) in ht for a in self.env[k].get("aliases") or [] if a)]
            if len(by_alias) == 1:
                return by_alias
            # the room said with this very place ('shelf in the restroom', 'kitchen shelf'), not any room the
            # task mentions elsewhere ('... the kitchen drawer')
            near = [k for k in hits if self.room_said(k, ht)]
            if len(near) == 1:
                return near
            hinted = [k for k in hits if self.room(k) and self.room(k) in hint]
            if len(hinted) == 1:
                return hinted
        return hits

    def room_said(self, key: str, text: str) -> bool:
        """Does *text* name *key* with its room ('shelf in the restroom', 'restroom shelf', "kitchen's shelf")?"""
        h, rm = re.escape(words(self.head(key))), re.escape(words(self.room(key)))
        return bool(rm) and bool(re.search(rf"\b{h}\s+(?:in|of|at|from)\s+(?:the\s+)?{rm}\b|\b{rm}(?:'s)?\s+{h}\b",
                                           words(text)))

    def said(self, key: str, text: str) -> bool:
        """Does *text* name *key* (its name or an alias), not only a word of it?"""
        t = words(text)
        return self.head(key) in t or any(words(a) in t for a in self.env.get(key, {}).get("aliases") or [] if a)

    def ground(self, name: str, hint: str = "") -> Optional[str]:
        c = self.candidates(name, hint)
        return c[0] if len(c) == 1 and not c[0].startswith("@") else None

    def in_room(self, room: str) -> list:
        return [k for k in self.keys if self.room(k) == room]


# ─────────────────────────────────────────────────────────────────────────────
# memory (object -> last place, clarification answers)
# ─────────────────────────────────────────────────────────────────────────────
class Memory:
    _lock = threading.Lock()

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            self.data = {}
        self.data.setdefault("objects", {})
        self.data.setdefault("answers", [])

    def where(self, obj: str) -> Optional[str]:
        e = self.data["objects"].get(words(obj))
        return e.get("place") if e else None

    def see(self, obj: str, place: Optional[str], how: str):
        if not obj:
            return
        self.data["objects"][words(obj)] = {"place": place, "ts": time.time(), "how": how}
        self.save()

    def remember_answer(self, task: str, question: str, answer: str):
        if answer:
            self.data["answers"] = (self.data["answers"] + [{"task": task, "q": question, "a": answer,
                                                              "ts": time.time()}])[-30:]
            self.save()

    def objects_text(self) -> str:
        o = self.data["objects"]
        return "\n".join(f"- {k}: {v.get('place') or 'unknown'}" for k, v in o.items()) or "(nothing yet)"

    def answers_text(self) -> str:
        return "\n".join(f"- task '{a['task']}': asked '{a['q']}' -> '{a['a']}'" for a in self.data["answers"][-8:]) or "(none)"

    def save(self):
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(f".tmp{os.getpid()}")
                tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
                os.replace(tmp, self.path)
        except Exception:
            pass


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
            "from": {"type": "string"},
            "to": {"type": "string"}},
            "required": ["type", "object", "from", "to"]}},
    },
    "required": ["status", "reason", "question", "goals"],
}

UNDERSTAND_PROMPT = """You turn a household robot task into goals. {abilities}
{cannot}

Places (use these names):
{places}

Earlier answers from this user:
{answers}

Robot now: {robot}

Return JSON:
- status: "unsafe" if it could hurt someone or damage things (throwing, out of a window, near fire...);
  "infeasible" only if it needs an action the robot does not have (any object may be moved; unknown objects are
  fine); "ambiguous" only if WHAT to do is unclear (e.g. "clean the table" without saying what to move where) — not
  for a missing or unclear place or an object whose location is unknown, those are handled separately; else "clear".
- reason: one short sentence.
- reason_type: for "infeasible" one of "missing_skill" (an action the robot does not have), "payload" (too heavy),
  "size" (too big), "reach" (out of reach), "unknown_task" (not a household task the robot knows), "other"; else "".
- question: when ambiguous, one short question to the user; else "".
- goals: one per object to move / container to open, in order:
  {{"type": "on", "object": <object>, "from": <place it is on now, or "">, "to": <place to put it, or "me" when the user
  wants it given to them>}}; "in" = put into a drawer (to = the drawer); "open" / "close": object = the drawer, from/to "".{goal_kinds}
  Object names: lower case, as the user said them ("tennis ball", "red cup").

Task: {task}{original}
{dialogue}"""


# ─────────────────────────────────────────────────────────────────────────────
# the loop
# ─────────────────────────────────────────────────────────────────────────────
class AssistLoop(SearchHooks, ClosedLoop):
    """See the module docstring."""

    _answer_source = "robot"          # where the last answer came from (interact.dialog: robot | typed | '')

    def __init__(self, agent, emit=None, **overrides) -> None:
        overrides = dict(overrides)
        self.original = overrides.pop("original", None)
        overrides["planner"] = overrides.get("generator") or os.environ.get("ROBOT_AGENT_SAGE_GENERATOR", "grace")
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
            # Find failures before asking the person where the object is (each one goes to the planner first)
            "search_max": int(overrides.get("search_max", env("ROBOT_AGENT_SEARCH_MAX", "3"))),
            # searches now go through the planner's replan, so the budget is larger than the plain loop's 3
            "max_replans": int(overrides.get("max_replans", env("ROBOT_AGENT_MAX_REPLANS", "6"))),
            "pick_retries": int(overrides.get("pick_retries", 1)),
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
            "search": search_belief.SearchConfig.from_env(overrides),
            # the belief against the sensors at the start / on resume and a non-sensor `arrived` against a fresh
            # pose before a step at a place, with provenance (reconcile.py, docs/guard.md); 0: the adapter only
            "reconcile": str(overrides.get("reconcile", env("ROBOT_AGENT_RECONCILE", "1"))) not in ("0", "False", "false"),
            "generator": self.cfg["planner"],
            "planner": "sage",
        })
        self.cfg.update(self._v2_cfg(overrides))
        self._reaches = lambda steps, world, goals, loop=None: AssistLoop._reaches(steps, world, goals, self)

    def _v2_cfg(self, overrides) -> dict:
        """The SAGE-v2 options (docs/env.md): run option > environment > the robot's manifest > default."""
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
        }

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

    def _sampling(self, sample: int = 0) -> dict:
        """The understand call's temperature / seed (0 = the first reading, k = the k-th re-sample)."""
        c, seed = self.cfg, self.cfg["understand_seed"]
        seed = int(seed) + sample if str(seed).strip() not in ("", "none", "None") and int(seed) >= 0 else None
        return sampling(c["understand_sample_temperature"] if sample else c["understand_temperature"], seed)

    def _understand(self, task, places, memory, world, dialogue, budget=None, sample: int = 0) -> dict:
        """The understand call (understand.ask_json: one repair retry on a non-JSON answer). Raises
        LLMUnavailable / LLMBudgetExceeded; a model that still answers no JSON gives no goals (``unparsed``)."""
        prompt = UNDERSTAND_PROMPT.format(
            abilities=self.robot.robot_description(), cannot=self._cannot_line(),
            goal_kinds=goal_kinds.prompt_lines(goal_kinds.kinds_for(self.robot.plan_actions)),
            # no object memory here: a 7B model refused / asked about every object it did not list
            # (the memory grounds `from` after this call instead)
            places=places.describe(), answers=memory.answers_text(),
            robot=world.as_text() if hasattr(world, "as_text") else "",
            dialogue=("You already asked the user, and the answers below complete the task (a later answer corrects "
                      "an earlier one, and a \"no, ...\" corrects what the robot read back): take the object, source "
                      "and destination from them, and say \"clear\" unless something is still missing.\n"
                      + "\n".join(f"{'Robot' if who == 'robot' else 'User'}: {t}" for who, t in dialogue))
            if dialogue else "", task=task,
            # the user's own words: a translation loses names (식탁 'dining table' came out as 'table')
            original=(f"\n(the user's own words: {self.original})" if self.original and self.original != task else ""))
        from ..run_control import call_cancellable, is_cancel
        try:                                     # every call (repairs too) cancellable: a stop does not wait
            d = ask_json(self._llm(), prompt, self._understand_schema(), budget=budget, retries=self.cfg["understand_retries"],
                         call=call_cancellable, passthrough=is_cancel, options=self._sampling(sample))
        except LLMBadOutput:
            d = {"unparsed": True}
        d.setdefault("status", "clear"); d.setdefault("question", "")
        d["goals"] = [g for g in d.get("goals") or [] if isinstance(g, dict)] if isinstance(d.get("goals"), list) else []
        for g in d["goals"]:
            # a sentence to say stays as said; object names are normalised
            g["object"] = (str(g.get("object") or "").strip() if g.get("type") == "say"
                           else _strip_article(words(g.get("object"))))
            for k in ("from", "to"):
                g[k] = str(g.get(k) or "").strip()
        d["goals"] = goal_kinds.speaker_slots(d["goals"])     # "me" only as a destination (FROM_ME)
        return d

    _CANNOT = ("It cannot: throw, pour, cook, wash, climb, open windows or doors, use tools, handle knives near people, "
               "or leave the house.")

    def _cannot_line(self) -> str:
        """The prompt's "It cannot" line: the robot's i18n ``cannot_list`` (else the core's), with the manifest's
        payload / size limits when it declares them (SAGE-v2 F4.1)."""
        robot = self.robot
        line = robot.text("cannot_list") if robot.has_text("cannot_list") else self._CANNOT
        lim = getattr(robot.manifest, "limits", None) or {}
        if lim.get("payload_kg"):
            line += f" It can carry at most {lim['payload_kg']} kg"
            line += f" and objects up to {lim['max_object_size_m']} m." if lim.get("max_object_size_m") else "."
        return line

    def _over_limits(self, goals) -> Optional[str]:
        """A goal whose object is over the manifest's limits, by the robot's own estimates (manifest
        ``limits.objects``: {name: {weight_kg, size_m}}, its object-class prior): why, else None."""
        lim = getattr(self.robot.manifest, "limits", None) or {}
        est = lim.get("objects") or {}
        pay, size = lim.get("payload_kg"), lim.get("max_object_size_m")
        if not est or not (pay or size):
            return None
        for g in goals:
            if g.get("type") not in ("on", "in"):
                continue
            ws = set(words(g.get("object")).split())
            hit = max(((n, e) for n, e in est.items() if set(words(n).split()) <= ws and isinstance(e, dict)),
                      key=lambda x: len(x[0]), default=None)
            if hit is None:
                continue
            n, e = hit
            if pay and float(e.get("weight_kg") or 0) > float(pay):
                return f"the {g['object']} is too heavy for me (about {e['weight_kg']} kg; I can carry {pay} kg)"
            if size and float(e.get("size_m") or 0) > float(size):
                return f"the {g['object']} is too big for me to carry"
        return None

    def _capability_check(self, r, u) -> Optional[str]:
        """The model's "infeasible" against the contract (F4.1 / F4.2): "repeat" (no goals and nothing heard to
        refuse: say it again), "override" (goals the contract can do and a reason that names a manner it
        covers: read them back instead of refusing), else None (the refusal stands)."""
        rt = str(u.get("reason_type") or "").strip()
        if not r.goals:
            content = [w for w in re.findall(r"[\w']+", r.task) if len(w) > 1]
            nothing = re.search(r"\bno (?:task|request|instruction)|not (?:a )?(?:clear|complete|understand)|unclear|"
                                r"garbled|incomplete|empty|nonsens|meaningless", str(u.get("reason") or ""), re.I)
            return "repeat" if u.get("unparsed") or len(content) < 3 or nothing else None
        if self._cannot_do(r.goals) or self._over_limits(r.goals):
            return None
        return "override" if rt in ("", "missing_skill", "other") else None

    def _understand_schema(self) -> dict:
        """UNDERSTAND_SCHEMA with the goal kinds this robot may be given (``goals.kinds_for``)."""
        schema = json.loads(json.dumps(UNDERSTAND_SCHEMA))
        schema["properties"]["goals"]["items"]["properties"]["type"]["enum"] = \
            goal_kinds.kinds_for(self.robot.plan_actions)
        return schema

    def _cannot_do(self, goals) -> Optional[str]:
        """Why the robot cannot do these goals (an action a goal kind needs is not in its manifest),
        or None. Checked before any planning or motion."""
        acts = self.robot.plan_actions
        missing = list(dict.fromkeys(a for g in goals for a in goal_kinds.missing_actions(g, acts)))
        return ", ".join(missing) if missing else None

    @staticmethod
    def _ground_container(g, places, task):
        """Ground an open / close goal's container on ENV: (goal, question|None)."""
        k = places.ground(g["object"], task) or places.ground(g.get("to") or "", task)
        if not k:
            drawers = [p for p in places.keys if places.openable(p)]
            k = drawers[0] if len(drawers) == 1 else None
        g["object"] = k or g["object"]; g["to"] = k or ""
        return g, (None if k else speech.LocalText("q_which_drawer", {"verb": g["type"]},
                                                   text=f"Which drawer should I {g['type']}?"))

    @staticmethod
    def _ground_template(g, places, task, latest=""):
        """Ground a go / say / deliver goal on ENV: (goal, question|None). A say goal's `to` may be a
        person instead of a place (``person``)."""
        g["from"] = ""
        v, t = g.get("to") or "", g["type"]
        if t == "say":
            if not g.get("object"):
                return g, speech.LocalText("q_what_say")
            k = places.ground(v, task) if v else None
            if v and not k:
                g["person"], g["to"] = v, ""
            elif k:
                g["to"] = k
            return g, None
        if not v:
            return g, speech.LocalText("q_where_go" if t == "go" else "q_where_take")
        c = places.candidates(v, task)
        if len(c) > 1 and latest and len(places.candidates(v, latest)) == 1:
            c = places.candidates(v, latest)
        if len(c) == 1 and c[0].startswith("@") and len(places.in_room(c[0][1:])) == 1:
            c = places.in_room(c[0][1:])
        if t == "go" and len(c) == 1 and c[0].startswith("@"):
            # a room is a fine place to wait: its first place that does not open, is not a person or the floor
            # (SAGE-v2 F3.5; no "which place" question for "wait in the bedroom")
            ok = [k for k in places.in_room(c[0][1:]) if not places.openable(k) and not persons.is_person(places, k)
                  and "floor" not in places.head(k).lower()]
            if ok:
                c = ok[:1]
        if len(c) == 1 and c[0] in places.env:
            g["to"] = c[0]
            return g, None
        opts = places.in_room(c[0][1:]) if len(c) == 1 else c
        nice = [k.replace("@", " in the ") for k in (opts or places.keys[:4])]
        return g, speech.LocalText("q_which_place", {"options": list(opts or places.keys[:4])},
                                   text=f"Which place do you mean: {' or '.join(nice)}?")

    def _ground_goals(self, goals, places, memory, task, latest=""):
        """Ground every goal's places on ENV. Returns (goals, question|None). `latest`: the user's last answer,
        which wins over earlier words ("the kitchen one" ... "no, the restroom")."""
        out, questions = [], []
        for g in goals:
            g = dict(g)
            t = g.get("type")
            if t in goal_kinds.TEMPLATE_KINDS or t in ("open", "close"):
                g, q_ = (self._ground_template(g, places, task, latest) if t in goal_kinds.TEMPLATE_KINDS
                         else self._ground_container(g, places, task))
                out.append(g); questions += [q_] if q_ else []
                continue
            for slot in ("to", "from"):
                v = g.get(slot) or ""
                if not v:
                    if slot == "to":
                        questions.append(speech.LocalText("q_where_put", {"object": g.get("said") or g["object"]},
                                                          text=f"Where should I put the {g['object']}?"))
                    continue
                if words(v) in _USER_WORDS or (slot == "to" and persons.is_speaker_word(v)):
                    q_ = self._ground_speaker(g, slot, places, task, latest)
                    questions += [q_] if q_ else []
                    continue
                c = places.candidates(v, task)
                if len(c) > 1 and latest:
                    c2 = places.candidates(v, latest)
                    if len(c2) == 1:
                        c = c2
                if len(c) == 1 and c[0] in places.env:
                    # the LLM may pick a key ('shelf@kitchen') the user never said: is its name unique in the words?
                    alt = places.candidates(places.head(c[0]), task)
                    if len(alt) > 1 and latest and places.candidates(places.head(c[0]), latest) == [c[0]]:
                        alt = [c[0]]                     # the last answer settles it
                    if len(alt) > 1:
                        c, v = alt, places.head(c[0])
                    elif not places.said(c[0], task):
                        # 'test table' for 식탁 translated to 'table': the user's words (an alias) may name another
                        # place of that kind -> take it; several -> ask instead of guessing
                        alt = places.candidates(places.head(c[0]).split()[-1], task)
                        if len(alt) == 1 and alt[0] != c[0]:
                            c = alt
                        elif len(alt) > 1:
                            c, v = alt, places.head(c[0]).split()[-1]
                if (slot == "from" and len(c) == 1 and c[0] in places.env and not places.said(c[0], task)
                        and memory.where(g["object"]) != c[0]):
                    c = []                                # the model made up where it is: search instead
                if len(c) == 1 and not c[0].startswith("@"):
                    g[slot] = c[0]
                elif slot == "from" and not c:
                    g[slot] = ""                      # unknown source: search for it
                else:
                    opts = places.in_room(c[0][1:]) if len(c) == 1 else c
                    if slot == "from" and opts:
                        g["from_any"] = opts; g[slot] = ""   # 'from the kitchen' / 'the shelf': search them
                    else:
                        nice = [k.replace("@", " in the ") for k in (opts or places.keys[:4])]
                        what = places.head(v) if '@' in v else words(v)
                        questions.append(speech.LocalText("q_which_x", {"what": what, "options": list(opts or places.keys[:4])},
                                                          text=f"Which {what} do you mean: {' or '.join(nice)}?"))
            if g.get("from") and g.get("from") == g.get("to"):
                g["from"] = ""                       # 'from the desk to the desk': the model's slip, search instead
            if t == "in" and g.get("to") and not places.openable(g["to"]):
                drawers = [p for p in places.keys if places.openable(p)]
                if len(drawers) == 1:
                    g["to"] = drawers[0]
            if slot_q := self._person_destination(g, places, task, latest):
                questions.append(slot_q)
            if not g.get("from") and not g.get("from_any"):
                g["from"] = self._remembered(memory, g["object"]) or ""
            if g.get("from") and g.get("from") == g.get("to"):
                g["from"] = ""                       # already where it should go? search rather than trust it
            out.append(g)
        return out, (speech.LocalText.join(*dict.fromkeys(questions)) if questions else None)

    def _ground_speaker(self, g, slot, places, task, latest):
        """A destination in the speaker's own words ("me", "here", 나한테): the speaker's ENV entry (F2.1, no
        question when the robot knows where they are); an untracked speaker's place from their answer, else
        "Where are you?"; without a speaker entry, the place the words name ("to me ... at the sofa"), else the
        question (as before). Returns the question or None."""
        sk = persons.speaker_key(places)
        if sk is not None and slot == "to":
            g[slot] = sk
            return None                                   # untracked: _person_destination asks / maps
        m = re.search(r"\b(?:at|on|by|near) (?:the )?([a-z ]+?)(?:[.,!?]|$)", task.lower())
        k = places.ground(m.group(1), task) if m else None
        if k:
            g[slot] = k
            return None
        return "Where are you? Which place should I bring it to?"

    def _person_destination(self, g, places, task, latest):
        """An ``on`` goal to a person entry (F2.1 / F2.3): a tracked person -> a hand-over (``g['hand']``) or a
        put-down at the support surface beside them (``persons.delivery``); an untracked person (no ENV position,
        ``people_tracking`` off) -> the support surface nearest to the place their answer names, never the seat
        itself, else a hand-over; no answer yet -> "Where are you?" / "Where is X?". Returns the question or None."""
        to = g.get("to") or ""
        radius = float(self.cfg.get("person_seat_m", persons.PERSON_SEAT_M)) if hasattr(self, "cfg") else persons.PERSON_SEAT_M
        robot = getattr(self, "robot", None)
        asked = getattr(self, "_where_asked", None)
        if (g.get("type") == "on" and asked and latest and to in places.env and not persons.is_person(places, to)
                and not persons.is_support(places, to) and persons.place_in_answer(places, latest) == to):
            # the answer to "Where are you?" named where they sit / lie: the delivery is to them, never onto the
            # seat itself (the model put the answer's place as the destination)
            to = g["to"] = persons.seat_person(places, to, radius) or asked
        if g.get("type") != "on" or not persons.is_person(places, to):
            return None
        if persons.tracked(places, to):
            mode, where = persons.delivery(places, to, task, robot, radius)
            if mode == "hand":
                g["hand"] = True
            else:
                g["to"] = where
            return None
        if persons.wants_hand(task, robot):
            g["hand"] = True
            return None
        at = persons.place_in_answer(places, latest) if latest else None
        if at is None:
            self._where_asked = to
            if (places.env.get(to) or {}).get("speaker") or to == persons.speaker_key(places):
                return speech.LocalText("q_where_are_you")
            return speech.LocalText("q_where_is_person", {"person": to},
                                    text=f"Where is {self._say_name(places, to)}?")
        spot = persons.surface_near(places, at, float(self.cfg.get("person_surface_m", 1.2)) if hasattr(self, "cfg") else 1.2)
        if spot:
            g["to"], g["near_person"] = spot, to
        else:
            g["hand"] = True
        return None

    @staticmethod
    def _say_name(places, key, lang="en") -> str:
        """How the robot names ``key`` to a person (F5.1): an alias in the user's script, else the key's head
        in words with its room; a person by their name."""
        from . import speech
        return speech.say_name(places, key, lang)

    _LANGS = {"ko": "Korean", "vi": "Vietnamese", "ja": "Japanese", "zh": "Chinese"}

    _REGISTER = {"ko": "polite spoken Korean, 해요체", "vi": "polite spoken Vietnamese", "ja": "polite spoken Japanese",
                 "zh": "polite spoken Chinese"}

    def _translator(self):
        """The client that translates what the robot says (F5.3): option / $ROBOT_AGENT_TRANSLATE_MODEL / the
        manifest's planner.translate_model; the planner's own client when none is set or it is the same model."""
        tm = str(self.cfg.get("translate_model") or "").strip()
        if not tm or tm == str((self._llm_cfg or {}).get("model") or ""):
            return self._llm()
        if getattr(self, "_tr_client", None) is None:
            from ...connect.llm import init_llm_client
            url = self.cfg.get("translate_url") or (self._llm_cfg or {}).get("url")
            self._tr_client = init_llm_client(cfg=dict(self._llm_cfg or {}, model=tm, url=url))
        return self._tr_client

    def _glossary(self, text, lang) -> dict:
        """{English name in ``text``: its name in ``lang``} for the ENV places / people the text names."""
        places, lg, out = getattr(self, "_env_places", None), (lang or "en")[:2], {}
        if places is None:
            return out
        t = str(text or "")
        for k in places.keys:
            en, local = speech.say_name(places, k, "en"), speech.say_name(places, k, lg)
            if en and local and en != local and en in t and local.isascii() is False:
                out[en] = local
        return out

    def _in_lang(self, text, lang) -> str:
        """``text`` (English) in the user's language: the robot's own phrase when ``text`` is a phrase
        (``speech.LocalText``) it has in that language, else the translator (``translate_model``, its own budget
        ``translate_budget``, an output check); the English text when neither works (counted:
        ``say_not_in_lang``)."""
        lg = (lang or "en")[:2]
        name = self._LANGS.get(lg)
        if not name or not text:
            return str(text) if text else text
        done = speech.render(text, lang, self.robot, getattr(self, "_env_places", None))
        if done is not None:
            return done
        if getattr(self, "_llm_unreachable", False):
            return str(text)                         # (the model is down: no translation call to wait for)
        cache = self.__dict__.setdefault("_tr_cache", {})
        key = (str(text), name, str(self.cfg.get("translate_model") or ""))
        if key not in cache:
            cache[key] = self._translate(str(text), lg, name)
        return cache[key]

    def _translate(self, text, lg, name) -> str:
        """One translation with the output check and one retry naming what was wrong; the English text when it
        fails (``self._say_not_in_lang`` counts it, never hidden)."""
        gl = self._glossary(text, lg)
        names = "; ".join(f"{a} = {b}" for a, b in gl.items())
        base = (f"Translate into natural {self._REGISTER.get(lg, name)} ({name}). Keep the meaning, the names and any "
                f"quoted sentence; do not add anything." + (f" Use these names: {names}." if names else "")
                + " Output only the translation:")
        why = None
        for _k in range(2):
            budget = getattr(self, "_tr_budget", None)
            try:
                if budget is not None:
                    budget.take(what="translate")
                prompt = base if why is None else base.replace(" Output only", f" (Your last answer was wrong: {why}.) Output only")
                out = str(self._translator().chat(prompt=f"{prompt}\n{text}", temperature=0, seed=0)).strip().strip('"')
            except Exception:
                break
            why = speech.guard(out, lg, keep=list(gl.values()))
            if why is None:
                return out
        self._say_not_in_lang = getattr(self, "_say_not_in_lang", 0) + 1
        return text

    def _ask(self, question, node, log_fn, lang) -> str:
        """Ask the user (``interact.dialog`` via run_control: the run waits on the question, so an answer
        typed / sent by any client answers it; the robot's Ask skill does the I/O). '' when there is no
        answer / no channel. Where the answer came from: ``self._answer_source`` (robot | typed | '')."""
        from ..run_control import ask_person
        ans, _n = ask_person(question, lambda q: self._ask_io(q, node, log_fn, lang))
        self._answer_source = ans.source
        return ans.text

    def _ask_io(self, question, node, log_fn, lang) -> str:
        """The robot's Ask skill (manifest): say the question, listen. '' when there is no answer / no channel."""
        spec = self.robot.ask_spec()
        if spec is None:
            return ""
        params = {(spec.required_params[:1] or ["inputs"])[0]: self._in_lang(question, lang)}
        for k, v in (("retries", 0), ("lang", lang)):            # optional params the skill may take
            if k in spec.param_names:
                params[k] = v
        try:
            r = self.sr.execute(spec.name, params, node, log_fn=log_fn)
            return str((r or {}).get("text") or (r or {}).get("answer") or "").strip() if (r or {}).get("isdone") else ""
        except Exception:
            return ""

    def _ask_heard(self, question, node, log_fn, lang):
        """Ask; on silence ask again (`silence_retries`). Returns (answer, n_asked)."""
        from ..run_control import ask_person
        ans, n = ask_person(question, lambda q: self._ask_io(q, node, log_fn, lang),
                            silence_retries=self.cfg["silence_retries"])
        self._answer_source = ans.source
        return ans.text, n

    @staticmethod
    def yes_no(text):
        """(True | False | None, correction text) from a spoken yes / no answer (``interact.dialog``)."""
        from ..run_control import yes_no
        return yes_no(text)

    @staticmethod
    def summary(goals, places=None):
        """The read-back "I will ..., then ...": a ``speech.LocalText`` (English text; the robot's phrases in the
        user's language, ``speech.render``). With ``places`` the keys are named as a person says them (F5.1) and a
        delivery to a person reads "bring you the cup" / "hand the cup to grandpa"."""
        nice = (lambda k: speech.say_name(places, k, "en")) if places is not None else \
            (lambda k: (k or "").replace("@", " in the "))
        return speech.LocalText("rb_will", {"what": speech.LocalText.seq(
            [AssistLoop._goal_phrase(g, places, nice) for g in goals])},
            text="I will " + ", then ".join(str(AssistLoop._goal_phrase(g, places, nice)) for g in goals) + ".")

    @staticmethod
    def _goal_phrase(g, places, nice):
        L, t, obj = speech.LocalText, g["type"], g.get("object") or ""
        o = g.get("said") or obj
        if t == "on" and places is not None and persons.is_person(places, g.get("to")):
            you = bool((places.env.get(g["to"]) or {}).get("speaker"))
            key = ("rb_hand_you" if you else "rb_hand_person") if g.get("hand") else ("rb_to_you" if you else "rb_to_person")
            return L(key, {"object": o, "person": g["to"]}, text=_fmt_en(key, object=obj, person=nice(g["to"])))
        if t == "on":
            if g.get("from"):
                return L("rb_on_from", {"object": o, "source": g["from"], "place": g["to"]},
                         text=f"put the {obj} from the {nice(g['from'])} on the {nice(g['to'])}")
            return L("rb_on", {"object": o, "place": g["to"]}, text=f"put the {obj} on the {nice(g['to'])}")
        if t == "in":
            return L("rb_in", {"object": o, "place": g["to"]}, text=f"put the {obj} in the {nice(g['to'])}")
        if t == "go" and places is not None:
            return L("rb_go", {"place": g.get("to")}, text=f"go to the {nice(g.get('to'))}")
        if t == "say" and places is not None:
            who = g.get("to") or ""
            if who and persons.is_person(places, who):
                return L("rb_say", {"person": who, "text": obj}, text=f"tell {nice(who)}: \"{obj}\"")
            return L("rb_say_here", {"text": obj}, text=goal_kinds.summary(g))
        if t in goal_kinds.TEMPLATE_KINDS:
            return goal_kinds.summary(g)
        return L(f"rb_{t}", {"place": obj}, text=f"{t} the {nice(obj)}") if t in ("open", "close") else f"{t} the {nice(obj)}"

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
            # the plan's 'cup' for the goal's 'red cup'
            for g in goals:
                if st["object"] and st["object"] != g["object"] and st["object"] in g["object"].split():
                    st["object"] = g["object"]
        return st

    def _check(self, steps, world, places, goals):
        """The plan, simulated as written (nothing is changed): [(step index, problem, critical)].
        Critical problems go back to the planner for a repair; the others are only mentioned alongside them.
        The action rules are the shared action model's; this adds the goal / ENV findings and the wording."""
        probs = []
        self._env_places = places
        P = lambda i, txt, crit=True: probs.append((i, txt, crit))
        src_of = {g["object"]: g.get("from") for g in goals}
        c = {"need_open": {g["to"] for g in goals if g["type"] == "in"} | {g["object"] for g in goals if g["type"] == "open"},
             "need_close": {g["object"] for g in goals if g["type"] == "close"}, "src_of": src_of,
             "ctx": dict(capabilities=self.robot.model_capabilities(), actions=self.robot.plan_actions,
                         openable=places.openable, containers=places.openable,
                         object_places={o: f for o, f in src_of.items() if f})}
        w, placed_ok = world.copy(), {}
        c["opened_by_plan"] = set()
        for i, st in enumerate(steps):
            if self._check_step(i, st, w, goals, c, placed_ok, P):
                if st.get("action") == "Open" and st.get("object") not in w.opened:
                    c["opened_by_plan"].add(st.get("object"))
                action_model.apply(st, w, inplace=True, capabilities=c["ctx"]["capabilities"])
        missing = self._missing(steps, world, goals)
        if missing:
            P(len(steps), "the plan does not achieve: " + "; ".join(missing))
        else:
            for k in range(len(steps)):
                if self._reaches(steps[:k], world, goals):
                    if not plan_tidy.closes_only(steps[:k], steps[k:], world):
                        P(k, f"the goals are done after step {k}; remove the steps after it (do not repeat the task)")
                    break
        return probs

    def _check_step(self, i, st, w, goals, c, placed_ok, P) -> bool:
        """The findings of step i in the simulated world `w`, worded for the planner. False: skip its effect."""
        if concrete.skill_only(st):
            return True                              # a skill call with no plan action: no rule, no effect
        a, o = st.get("action"), st.get("object") or ""
        vs, said = {v.code: v for v in action_model.check(st, w, **c["ctx"])}, set()

        def has(*codes):                             # did the model find one of these? (marks them worded)
            hit = [c_ for c_ in codes if c_ in vs]
            said.update(hit)
            return bool(hit)
        if not self._word_step(i, a, o, st, w.holding, has, goals, c, placed_ok, P):
            return False
        for code, v in vs.items():                   # any other rule of the model, in its own words
            if code not in said:
                P(i, f"step {i + 1} '{a} {o}': {v.reason}", v.is_error)
        return True

    def _word_step(self, i, a, o, st, holding, has, goals, c, placed_ok, P) -> bool:
        """The checker's own wording of the model's findings for the step's action. False: skip the step."""
        bad_place = not st.get("grounded", True) or o.startswith("@")
        nice = lambda k: (k or "").replace("@", " in the ")
        src_of = c["src_of"]
        if has("unknown_action"):
            P(i, f"step {i + 1} '{a} {o}': the robot has no action {a} (use MoveTo, Find, Pick, Place, PutIn, Open, Close)")
            return False
        if a == "MoveTo":
            if bad_place:
                P(i, f"step {i + 1} 'MoveTo {o}': {o.lstrip('@')} is not a place the robot can go to; use one of the listed places")
                return False
            if has("already_there"):
                P(i, f"step {i + 1} 'MoveTo {o}': the robot is already there", False)
        elif a in ("Open", "Close"):
            if has("not_openable"):
                P(i, f"step {i + 1} '{a} {o}': {nice(o)} cannot be opened or closed")
                return False
            if o not in (c["need_open"] if a == "Open" else c["need_close"]) and o not in src_of.values():
                # closing what the plan itself opened is the expected default (a goal that needs it open at
                # the end is a finding of _missing), never critical
                P(i, f"step {i + 1} '{a} {o}': no goal needs it; remove it",
                  not (a == "Close" and o in c.get("opened_by_plan", ())
                       and not any(g["type"] == "open" and g["object"] == o for g in goals)))
            if has("hand_full"):
                P(i, f"step {i + 1} '{a} {o}': the robot cannot {a.lower()} it while holding the {holding}; do it before the Pick")
            if has("interact_without_find"):
                P(i, f"step {i + 1} '{a} {o}': the robot must MoveTo {o} first")
            if has("already_open"):
                P(i, f"step {i + 1} 'Open {o}': {nice(o)} is already open; remove this step")
            if has("close_not_opened"):
                P(i, f"step {i + 1} 'Close {o}': {nice(o)} is not open; remove this step")
        elif a == "Find":
            if has("find_held"):
                P(i, f"step {i + 1} 'Find {o}': already done (the {o} is in the hand); delete this step")
            if has("find_wrong_place"):
                P(i, f"step {i + 1} 'Find {o}': the user said it is on {nice(src_of[o])}; MoveTo {src_of[o]} first")
        elif a == "Pick":
            self._word_pick(i, o, holding, has, goals, placed_ok, P)
        elif a in ("Place", "PutIn"):
            self._word_place(i, a, o, holding, bad_place, has, goals, placed_ok, P)
        return True

    @staticmethod
    def _word_pick(i, o, holding, has, goals, placed_ok, P) -> None:
        g_ = next((g for g in goals if g["object"] == o), None)
        if g_ and placed_ok.get(o) == g_.get("to"):
            P(i, f"step {i + 1} 'Pick {o}': the {o} was already placed on {g_['to']} by an earlier step; delete the steps that move it again")
        if has("already_holding", "no_find_before_pick", "pick_not_found"):
            if holding and holding == o:
                P(i, f"step {i + 1} 'Pick {o}': already done by an earlier step (the {o} is in the hand); delete this step")
            elif holding:
                P(i, f"step {i + 1} 'Pick {o}': the hand already holds the {holding}; Place it first")
            else:
                P(i, f"step {i + 1} 'Pick {o}': needs 'Find {o}' just before it, at the same place")

    @staticmethod
    def _word_place(i, a, o, holding, bad_place, has, goals, placed_ok, P) -> None:
        nice = lambda k: (k or "").replace("@", " in the ")
        g = next((g for g in goals if g["object"] == holding), None)
        if has("place_without_holding"):
            P(i, f"step {i + 1} '{a} {o}': nothing is in the hand")
        if bad_place:
            has("empty_object", "place_wrong_location")
            P(i, f"step {i + 1} '{a} {o}': not a place" + (f"; the destination is {g['to']}" if g else ""))
        elif g and g.get("to") and o != g["to"]:
            has("place_wrong_location")
            P(i, f"step {i + 1} '{a} {o}': the {holding} must go to {g['to']}, not {o}")
        elif has("place_wrong_location"):
            P(i, f"step {i + 1} '{a} {o}': the robot must MoveTo {o} first")
        if has("putin_closed_container"):
            P(i, f"step {i + 1} 'PutIn {o}': {nice(o)} is closed; Open it before the Pick")
        if holding:
            placed_ok[holding] = o

    def _repair_plan(self, planner, task, obs, ground, world, places, goals, goal_txt, budget=None):
        """The planner's own sub-goal repair (optional ``SubgoalRepair``, e.g. SAGE's Editing), driven by the
        checker's findings: subgoal_repair.repair_plan. Returns (plan, [edit notes]); (None, []) for a planner
        without it. Each repair call takes one unit of ``budget`` (LLMBudgetExceeded past it)."""
        from .subgoal_repair import repair_plan
        return repair_plan(self, planner, task, obs, ground, world, places, goals, goal_txt, budget=budget)

    _sage_edit = _repair_plan            # pre-v2 name

    def _releases(self) -> set:
        """The robot's own actions that release the held object (RobotView.release_actions: a hand-over)."""
        if not isinstance(self, AssistLoop):
            return set()
        robot = getattr(self, "robot", None)
        try:
            return set(robot.release_actions()) if robot is not None else set()
        except Exception:
            return set()

    def _simulate_goals(self, steps, world) -> tuple:  # self: the loop, or None (no release actions)
        """(opened, on): what the steps leave open and where each object ends: on[obj] = (action, where);
        a release action (hand-over) is ("release", the person / place the robot is at)."""
        holding, opened, on, at = world.holding, set(world.opened), {}, world.arrived
        rel = AssistLoop._releases(self) if self is not None else set()
        for st in steps:
            a, o = st.get("action"), st.get("object")
            if a == "MoveTo":
                at = o
            elif a == "Pick":
                holding = o
            elif a in ("Place", "PutIn") and holding:
                on[holding] = (a, o); holding = None
            elif a in rel and holding:
                on[holding] = ("release", o or at); holding = None
            elif a == "Open":
                opened.add(o)
            elif a == "Close":
                opened.discard(o)
        return opened, on

    def _on_reached(self, g, got) -> bool:
        """Is the ``on`` goal ``g`` reached by ``got`` = (action, where)? A Place there; for a goal to a person
        (F2.2) also the robot's hand-over there, and only the hand-over when the goal asks for the hand."""
        if not got or got[1] != g.get("to"):
            return False
        places = getattr(self, "_env_places", None) if self is not None else None
        to_person = places is not None and persons.is_person(places, g.get("to"))
        if g.get("hand") and to_person:
            return got[0] == "release"
        return got[0] == "Place" or (got[0] == "release" and to_person)

    def _missing(self, steps, world, goals):
        opened, on = self._simulate_goals(steps, world)
        out = []
        for g in goals:
            t = g["type"]
            if t == "on" and not self._on_reached(g, on.get(g["object"])):
                out.append(f"{'Hand over' if g.get('hand') else 'Place'} the {g['object']} "
                           f"{'to' if g.get('hand') else 'on'} {g['to']}")
            elif t == "in" and on.get(g["object"]) != ("PutIn", g["to"]):
                out.append(f"PutIn the {g['object']} into {g['to']}")
            elif t == "open" and g["object"] not in opened:
                out.append(f"Open {g['object']}")
            elif t == "close" and g["object"] in opened:
                out.append(f"Close {g['object']}")
            elif t in goal_kinds.TEMPLATE_KINDS and not goal_kinds.reached(g, steps, world.arrived,
                                                                           last=g is goals[-1]):
                out.append(goal_kinds.summary(g))
        return out

    @staticmethod
    def _reaches(steps, world, goals, loop=None) -> bool:
        """Do ``steps`` from ``world`` reach ``goals``? With ``loop`` (bound calls pass it: ``self._reaches``
        is wrapped in ``__init__``) a hand-over to a person counts too (F2.2)."""
        opened, on = AssistLoop._simulate_goals(loop, steps, world)
        for g in goals:
            t = g["type"]
            if t == "on" and not AssistLoop._on_reached(loop, g, on.get(g["object"])):
                return False
            if t == "in" and on.get(g["object"]) != ("PutIn", g["to"]):
                return False
            if t == "open" and g["object"] not in opened:
                return False
            if t == "close" and g["object"] in opened:
                return False
            if t in goal_kinds.TEMPLATE_KINDS and not goal_kinds.reached(g, steps, world.arrived,
                                                                         last=g is goals[-1]):
                return False
        return bool(goals) or bool(steps)

    def _goals_hold(self, goals, world, placed, done=(), at=None) -> bool:
        """Every goal holds in the belief: objects placed, containers open / closed, and the go / say /
        deliver goals reached by the steps `done` from `at` (the run's start)."""
        for g in goals:
            t = g["type"]
            if t == "go" and g is goals[-1]:
                # the last go goal holds where the robot is now, not where it once passed (or started)
                if world.arrived != g.get("to"):
                    return False
            elif t in goal_kinds.TEMPLATE_KINDS and not goal_kinds.reached(g, list(done), at):
                return False
            if t in ("on", "in") and (placed.get(g["object"]) != g["to"] or world.holding == g["object"]):
                return False
            if t == "on" and g.get("hand") and g["object"] not in getattr(self, "_handed", set()):
                return False                                  # a hand delivery holds only after a hand-over
            if t == "open" and g["object"] not in world.opened:
                return False
            if t == "close" and g["object"] in world.opened:
                return False
        return True

    # ── execution helpers ─────────────────────────────────────────────────
    def _bind(self, st, world):
        """``(skill, params)`` of a grounded step (None: a no-op); raises Unbindable. The step's object is
        already the robot's name (an ENV key / the object), so it goes in as is."""
        mapped = self.robot.bind(dict(st), world)
        if mapped is None:
            return None
        skill, p = mapped[0], dict(mapped[1] or {})
        a, o = st["action"], st["object"]
        if a in _LOC_ACTIONS or a in ("Find", "Pick"):
            p["inputs"] = o
        lang = getattr(self, "_run_lang", "en") or "en"
        if a in ("Say", "Announce") and lang[:2] != "en":
            from . import lexicon
            for k_ in ("inputs", "text"):
                if isinstance(p.get(k_), str) and p[k_].strip() and lexicon.lang_of(p[k_]) != lang[:2]:
                    p[k_] = self._in_lang(p[k_], lang)       # a Say in the user's language (F5.4)
        if a == "Find" and self.cfg["find_min_conf"] > 0 and "min_conf" in self._find_params:
            p["min_conf"] = self.cfg["find_min_conf"]
        if a == "Find" and self.cfg["find_verify"] and "verify" in self._find_params:
            p["verify"] = True
        return skill, p

    def _params(self, st, world):
        """The params of a grounded step (legacy entry point; see ``_bind``)."""
        mapped = self._bind(st, world)
        return dict(mapped[1]) if mapped else {}

    def _holding(self, node, default: bool) -> bool:
        """Is something in the hand? The robot's holding sensor (the one that verifies its Pick);
        ``default`` when it has none or it cannot be read."""
        skill = self.robot.holding_sensor()
        if skill is None:
            return default
        try:
            return bool(self.sr.execute(skill, {}, node).get("isdone"))
        except Exception:
            return default

    def _base_pose(self, node):
        """[x, y, yaw_deg] from the robot's pose sensor, or None."""
        skill = self.robot.pose_sensor()
        if skill is None:
            return None
        try:
            return list(self.sr.execute(skill, {}, node)["pose"])[:3]
        except Exception:
            return None

    def _dist_to_place(self, key, node, places):
        """Base distance (m) from the ENV loc of `key`, or None when unknown."""
        l = (places.env.get(key) or {}).get("loc") or {}
        if "x" not in l or "y" not in l:
            return None
        pose = self._base_pose(node)
        if pose is None:
            return None
        return math.hypot(pose[0] - l["x"], pose[1] - l["y"])

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

    @staticmethod
    def _found_point(result, obj):
        """Base-frame [x, y, z] of what Find detected (``ins[obj].pose_3d``), or None."""
        ins = (result or {}).get("ins") if isinstance(result, dict) else None
        if not isinstance(ins, dict) or not ins:
            return None
        e = ins.get(obj) or next(iter(ins.values()))
        p = e.get("pose_3d") or e.get("loc_3d") if isinstance(e, dict) else None
        try:
            return [float(v) for v in p[:3]]
        except Exception:
            return None

    def _place_of_far_point(self, p3, node, places, world):
        """The ENV place to stand at for a point seen beyond reach (None when it is within reach / unknown)."""
        if math.hypot(p3[0], p3[1]) <= self.cfg["reach_far"]:
            return None
        pose = self._base_pose(node)
        if pose is None or len(pose) < 3:
            return None
        x, y, rz = pose
        c, s_ = math.cos(math.radians(rz)), math.sin(math.radians(rz))
        wx, wy = x + c * p3[0] - s_ * p3[1], y + s_ * p3[0] + c * p3[1]
        best = None
        for k in places.keys:
            l = places.env[k].get("loc") or {}
            if "x" in l and "y" in l and not places.openable(k) and not persons.is_person(places, k):
                d = math.hypot(l["x"] - wx, l["y"] - wy)
                if best is None or d < best[0]:
                    best = (d, k)
        if best and best[0] < self.cfg["reach_far"] + 0.5 and best[1] != world.arrived:
            return best[1]
        return None

    # ── main: the phases of one run ───────────────────────────────────────
    def run_blocking(self, task: str, lang: str = "en", plan_only: bool = False) -> Iterator[dict]:
        r = self._open_run(task, lang, plan_only, "sage+assist")
        fixed = self._reconcile(r, "start")              # adapter.reconcile + the declared sensors
        from robot_agent.skill_configs import ENV
        r.places = Places(dict(ENV), openable=r.robot.openable)
        self._env_places = r.places
        r.verifier = self._verifier(r.node, r.places.env)
        r.memory = Memory(self.cfg["memory_path"])
        r.llm_budget, self._llm_unreachable = LLMBudget(self.cfg["llm_budget"]), False
        self._where_asked = None                      # the person "Where are you?" was asked about (this run)
        self._handed = set()                          # objects this run handed over (release actions)
        self._tr_budget = LLMBudget(self.cfg["translate_budget"])   # translation calls: their own cap (F5.3)
        self._say_not_in_lang, self._run_lang = 0, lang
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
        if say is None and status in ("failed", "refused") and msg and not msg.startswith(("internal error", "plan finished")):
            # tell the user why (dropped it, can't find it, ...), without the skill's error text or the gate's findings
            say = re.split(r": [\'\"{]|: Exception| \(replan| \(searched| \(looked|: step \d|: the plan does", msg)[0].strip()
        if say and not localized:
            say = self._humanize(say)                         # no raw ENV key reaches the user (F5.1)
        return r.ev("done", status=status, run_id=r.run_id, msg=msg,
                    say=(say if localized else self._in_lang(say, r.lang)) if say else r.announcer.announce(kind), world=r.world.to_dict(), **kw)

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

    # ── 1. understand + clarify ───────────────────────────────────────────
    def _understand_phase(self, r):
        """Goals from the task and the user's answers. True when the run ended."""
        r.dialogue, r.asked, r.confirms, r.pin_to, r.goals = [], 0, 0, None, []
        r.heard = 0                      # answers heard through the robot's I/O (a typed one cannot be misheard)
        while True:
            out = yield from self._understand_round(r)
            if out is _STOP:
                return True
            if out is _DONE:
                break
        r.run.obs = "goals: " + json.dumps(r.goals, ensure_ascii=False)
        return False

    def _understand_round(self, r):
        """One understanding of the task: _DONE (goals settled), _NEXT (asked something) or _STOP."""
        yield r.ev("status", msg="Understanding the task...")
        try:
            u = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget)
        except (LLMUnavailable, LLMBudgetExceeded) as e:
            yield self._llm_failed(r, e); return _STOP
        r.goals, q = self._ground_understood(r, u)
        yield r.ev("understand", status=u["status"], reason=u.get("reason"), goals=r.goals,
                   question=u.get("question") or q, **({"filtered": r.filtered} if r.filtered else {}))
        if u["status"] == "infeasible" and r.dialogue and r.goals:
            u["status"] = "clear"                # the user already answered: a 7B model called a plain
                                                 # move "infeasible" after "no, the shelf in the restroom"
        # a refusal only for a typed reason (understand.typed_refusal): a goal needs an action this robot has
        # no skill for, or the model's unsafe / infeasible on words that are not a misheard answer
        heard = bool(r.dialogue) and r.dialogue[-1][0] == "user" and self._answer_source == "robot"
        ref = typed_refusal(u["status"], u.get("reason"), self._cannot_do(r.goals),
                            " ".join([r.task, self.original or ""] + [t for w_, t in r.dialogue if w_ == "user"]), heard)
        if ref is not None and ref.kind == "capability" and not ref.missing and u["status"] == "infeasible":
            how = self._capability_check(r, u)
            if how == "repeat":                          # F4.2: not heard / not a request: say it again
                yield r.ev("understand_check", issues=["no task heard"], repeat=True)
                return (yield from self._ask_clarify(r, speech.LocalText("q_repeat")))
            if how == "override":                        # F4.1: the contract covers it; the person confirms
                yield r.ev("understand_check", issues=[f"capability refusal overridden: {u.get('reason')}"],
                           override=True)
                ref, r.force_readback = None, True
        over = self._over_limits(r.goals) if ref is None and r.goals else None
        if over:
            from .understand import Refusal
            ref = Refusal("capability", msg=over, say=f"I can't do that: {over}.")
        if ref is not None and ref.misheard:
            r.dialogue, q_again = misheard_question(r.dialogue)
            yield r.ev("understand_check", issues=[f"{ref.kind} refusal after a heard answer"], misheard=True)
            return (yield from self._ask_clarify(r, q_again))
        if ref is not None:
            yield self._finish(r, "refused", refusal=ref.kind, say=ref.say or r.robot.text("cannot_do", "en", missing=ref.missing),
                               msg=ref.msg if not ref.missing else f"{ref.msg}, needed for: "
                                   + "; ".join(goal_kinds.describe(g) for g in r.goals))
            return _STOP
        r.goals, conflict = goal_kinds.conflicts(r.goals)          # the goal validator
        if conflict:
            q = conflict if not q else f"{conflict} {q}"
        # the grounding decides about places; the model's "ambiguous" counts only when it found nothing
        # to do (after "no, the shelf in the restroom" a 7B model kept asking which shelf)
        if u["status"] == "ambiguous" and u.get("question") and not r.goals:
            q = u["question"] if not q else f"{u['question']} {q}"
        if not r.goals and not q:
            q = u.get("question") or ("Sorry, I did not understand. Could you say the task in other words?"
                                      if u.get("unparsed") else "What should I do exactly?")
        if not q:
            q = yield from self._goal_readback(r)
            if q is _STOP:
                return _STOP
        if not q:
            out, q = yield from self._read_back(r)
            if out is not None:
                return out
        return (yield from self._ask_clarify(r, q))

    def _ground_understood(self, r, u):
        """An understanding's goals grounded on ENV (with the read-back's pinned destination): (goals, question)."""
        if r.pin_to:
            # "no, the shelf in the restroom" after a read-back names the destination (a 7B model put
            # it in `from`): pin it instead of trusting the model's slots
            for g in u["goals"]:
                if g.get("type") in ("on", "in"):
                    if r.places.ground(g.get("from") or "", g.get("from") or "") == r.pin_to:
                        g["from"] = ""
                    g["to"] = r.pin_to
        answers = [t for w_, t in r.dialogue if w_ == "user"]
        words_ = " ".join([r.task, self.original or ""] + answers)
        on = self.cfg["goal_filter"]                                        # goals not asked for (goal_filter.py)
        raw, r.filtered = goal_filter.before_grounding(u["goals"], r.task) if on else (u["goals"], [])
        raw, empty = goal_fix.drop_empty(raw)                                # F3.4: no "put the ?" questions
        r.filtered += empty
        goals, q = self._ground_goals(raw, r.places, r.memory, words_, latest=answers[-1] if answers else "")
        goals, more = goal_filter.after_grounding(goals, " ".join([r.task] + answers), r.places) if on else (goals, [])
        r.filtered += more
        goals, _kinds = goal_fix.kind_from_destination(goals, words_, r.places, "PutIn" in self.robot.plan_actions)
        goals, invented = goal_fix.drop_invented(goals, words_, r.places)    # F4.4
        r.filtered += invented
        if not goals and empty and not q:
            q = speech.LocalText("q_what_bring")
        return goals, q

    def _issues(self, r, goals) -> list:
        """understand.find_issues of `goals` against everything the person said (task, own words, answers)."""
        if not self.cfg["goal_readback"]:
            return []
        answers = [t for w_, t in r.dialogue if w_ == "user"]
        # a place the person replaced in a read-back correction no longer counts as asked for (docs/dialog.md)
        said = without_places(" ".join([r.task, self.original or ""]), r.places, getattr(r, "replaced", ()))
        return find_issues(goals, " ".join([said] + answers), r.places,
                           english=r.task, user_words=_USER_WORDS)   # objects: the task's own words only

    def _goal_readback(self, r):
        """The goals read back against the person's words before planning: a model that dropped or changed a
        goal must not plan (and end in a success against its own goals). On an issue the understanding is
        sampled `understand_samples` more times: an issue-free goal set the samples agree on is taken
        (CLARA-style consistency). Else the person is asked an open question the first time (returned), and
        later the read-back names the doubt (r.unsure). _STOP when an LLM call failed."""
        issues = self._issues(r, r.goals)
        r.unsure = [describe(i) for i in issues]
        if issues and not getattr(r, "sampled", False) and self.cfg["understand_samples"] > 0:
            r.sampled = True
            agreed = yield from self._resample(r, r.unsure)
            if agreed is _STOP:
                return _STOP
            if agreed is not None:
                r.goals, r.unsure = agreed, []
                return None
        if not issues or getattr(r, "issue_asked", False):
            return None
        r.issue_asked = True
        return issue_question(issues, r.places) or None

    def _resample(self, r, issues):
        """`understand_samples` more understandings: the issue-free goal set they agree on, None, or _STOP."""
        samples = []
        for _k in range(self.cfg["understand_samples"]):
            try:
                u = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget, sample=_k + 1)
            except LLMBudgetExceeded:
                break
            except LLMUnavailable as e:
                yield self._llm_failed(r, e); return _STOP
            g, q = self._ground_understood(r, u)
            if u["status"] not in ("unsafe", "infeasible") and not q:
                samples.append((g, [describe(i) for i in self._issues(r, g)]))
        agreed = consensus(samples, need=min(2, len(samples)) or 1)
        yield r.ev("understand_check", issues=issues, samples=[{"goals": g, "issues": i} for g, i in samples],
                   agreed=agreed is not None)
        return agreed

    def _read_back(self, r):
        """Read the goals back when the user answered something (a misheard answer is only caught
        here) or always (`confirm_task`, a voice user whose command itself may be misheard).
        Returns (outcome or None, the question to ask next)."""
        unsure = getattr(r, "unsure", None)
        if not ((r.dialogue and r.heard and self.cfg["confirm_answers"]) or self.cfg["confirm_task"] or unsure
                or getattr(r, "force_readback", False)):
            return _DONE, None
        if r.confirms >= 2:
            yield self._finish(r, "failed", msg="the user did not confirm the task"); return _STOP, None
        r.confirms += 1
        rb = self.summary(r.goals, r.places)
        cq = speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?")
        if unsure:
            cq = speech.LocalText.join(f"I am not sure I understood ({'; '.join(unsure)}).", cq)
        yield r.ev("clarify", question=cq, round=r.confirms, kind="confirm")
        ans, ok_, rest = self._ask_yes_no(r, cq)
        stop = self._cancelled(r)
        if stop:
            yield stop; return _STOP, None
        if ok_ is True:
            return _DONE, None
        if ok_ is None:
            yield self._finish(r, "failed", msg=f"no answer to: {cq}"); return _STOP, None
        r.dialogue += [("robot", cq), ("user", ans)]
        if not rest:                     # a bare "no": ask what instead
            return None, "Sorry. What should I do?"
        r.memory.remember_answer(r.task, cq, ans)
        k = r.places.ground(rest, rest)
        if k and not re.search(r"\b(from|take|pick)\b", rest):
            r.pin_to = k                 # the destination it read back is replaced (the coverage check drops it)
            r.replaced = set(getattr(r, "replaced", ())) | {g["to"] for g in r.goals
                                                            if g.get("type") in ("on", "in") and g.get("to") not in (None, "", k)}
        return _NEXT, None

    def _ask_yes_no(self, r, q):
        """A yes / no question (``interact.dialog.ask_yes_no``): an unclear answer ("yet", noise) is asked again
        (`yes_no_reasks`, the robot's i18n `yes_or_no`); still unclear = a bare "no" (an open question next).
        Returns (answer, True | False | None, correction text)."""
        from ..run_control import ask_yes_no

        def ask(question):
            a, _n = self._ask_heard(question, r.node, self._log_fn(-1), r.lang)
            r.heard += self._answer_source == "robot"
            return a
        ans, rep, _n = ask_yes_no(q, ask, r.robot.text("yes_or_no", r.lang), reasks=self.cfg["yes_no_reasks"])
        return ans, rep.ok, ("" if rep.kind == "unclear" else rep.rest)

    def _ask_clarify(self, r, q):
        """Ask the user `q` (bounded by clarify_rounds): _NEXT with the answer in the dialogue, or _STOP."""
        if r.asked >= self.cfg["clarify_rounds"]:
            yield self._finish(r, "failed", msg=f"still unclear after {r.asked} questions: {q}")
            return _STOP
        r.asked += 1
        yield r.ev("clarify", question=q, round=r.asked)
        ans, _n = self._ask_heard(q, r.node, self._log_fn(-1), r.lang)
        r.heard += self._answer_source == "robot"
        stop = self._cancelled(r)
        if stop:
            yield stop; return _STOP
        if not ans:
            yield self._finish(r, "failed", msg=f"no answer to: {q}")
            return _STOP
        r.memory.remember_answer(r.task, q, ans)
        r.dialogue += [("robot", q), ("user", ans)]
        return _NEXT

    # ── 2. plan: the planner generates, the checker reports, the planner repairs
    def _obs(self, r) -> str:
        txt = ("Places the robot can go to (use exactly these):\n" + "\n".join(f"- {k}" for k in r.places.keys)
               + "\nGoals: " + r.goal_txt + "\nRobot state: " + r.world.as_text())
        if r.world.arrived is None and r.world.provenance_of("arrived").get("source") == sensed.UNKNOWN:
            txt += "\nRobot position: unknown (no fresh base pose): go to a place before acting there"
        if r.searched:
            txt += "\nAlready looked (object not there): " + ", ".join(r.searched)
        return txt + self._search_obs(r)

    @staticmethod
    def _goal_text(goals) -> str:
        """The goals as the planner reads them: 'on apple from desk to dining table; ...'."""
        return "; ".join(goal_kinds.describe(g) for g in goals)

    def _ground_all(self, r, seq) -> list:
        return [self._ground_step(x, r.places, r.goals, r.task) for x in seq]

    def _plan_phase(self, r):
        """The certified plan in r.steps. True when the run ended (plan only / no plan)."""
        yield r.ev("status", msg="Generating task plan...")
        r.restated = r.task + ("" if not r.dialogue else " (" + "; ".join(t for w_, t in r.dialogue if w_ == "user") + ")")
        r.searched, r.seen_far, r.uncertified = [], [], []
        yield from self._search_start(r)                 # may give a goal its source (search.py)
        r.goal_txt = self._goal_text(r.goals)
        r.planner = self._make_planner()
        self._hook_progress(r.planner)
        yield from self._generate_and_certify(r)
        skill_map = r.robot.skill_map
        plan_text = "\n".join(f"{x.get('skill') or skill_map.get(x['action'], x['action'])}::{x['object']}" for x in r.steps)
        yield r.ev("plan", steps=r.steps, plan_meta=r.run.plan_meta, warnings=list(r.gate_findings),
                   plan_text=plan_text, certified=r.certified, world=r.world.to_dict())
        if isinstance(r.plan_error, LLMBudgetExceeded) or (r.plan_error is not None and not r.steps):
            # the planner is down / the LLM-call budget is used: say so, not "no plan"
            yield self._llm_failed(r, r.plan_error, "planner"); return True
        if r.plan_only:
            yield self._finish(r, "planned", say=r.announcer.announce("plan_ready")); return True
        if not r.steps and not (r.goals and len(self._untouched(r.goals, [])) == len(r.goals)
                                and not (r.run.plan_meta or {}).get("error")):
            yield self._finish(r, "failed", msg="the planner produced no plan for the goals"); return True
        if not r.certified:
            # an uncertified plan never runs (the gate's findings are the reason)
            yield self._finish(r, "failed", msg="I could not make a plan that passes my checks: "
                                                + "; ".join(r.gate_findings[:3]))
            return True
        return False

    def _generate_and_certify(self, r):
        """The planner's plan, checked as written; critical findings go back to its sub-goal repair (if any)."""
        t_plan, r.plan_error = time.time(), None
        from ..run_control import call_cancellable, is_cancel
        r.compiled = goal_kinds.is_template(r.goals)
        try:
            if r.compiled:
                # go / say / deliver goals: a fixed sequence of the robot's own actions, no LLM
                raw, meta = goal_kinds.compile_steps(
                    r.goals, r.world.arrived, r.robot.text("delivery", r.lang),
                    can_say="Say" in r.robot.plan_actions), {"method": "compiled"}
            else:
                raw, meta = self._plan_call(r, "generate_plan", r.restated, self._obs(r), [],
                                            what="plan", subgoals=self._subgoals(r))
        except Exception as e:
            if is_cancel(e):
                raise
            raw, meta, r.plan_error = [], {"error": str(e)}, e
        r.steps, tidied = self._tidy(r, self._ground_all(r, raw))     # provable no-op steps out (plan_tidy.py)
        probs = self._check(r.steps, r.world, r.places, r.goals)
        yield r.ev("gate", round=0, problems=[p_[1] for p_ in probs], plan=[f"{x['action']} {x['object']}" for x in r.steps],
                   **({"tidied": tidied} if tidied else {}))
        edits = []
        if self._blocking(r, r.steps) and not r.compiled and r.plan_error is None:
            edits += self._gate_repair(r)                         # symbolic repair first (plan_repair.py)
            probs = self._check(r.steps, r.world, r.places, r.goals)
            yield r.ev("gate", round="symbolic", edits=list(edits), problems=[p_[1] for p_ in probs],
                       plan=[f"{x['action']} {x['object']}" for x in r.steps])
        if any(p_[2] for p_ in probs) and not r.compiled:
            # the planner's sub-goal-local repair with the checker's findings (SubgoalRepair, if it has it)
            try:
                edited, llm_edits = self._repair_plan(r.planner, r.restated, self._obs(r), lambda s: self._ground_all(r, s),
                                                      r.world, r.places, r.goals, r.goal_txt, budget=r.llm_budget)
            except LLMBudgetExceeded as e:
                edited, llm_edits, r.plan_error = None, [], e
            edits += llm_edits
            # block edits that were LLM calls (an emptied block is the core's)
            r.block_edits = sum(1 for n in llm_edits if str(n).startswith("sub-goal ") and "emptied" not in str(n))
            if edited is not None:
                r.steps, more = self._tidy(r, edited)
                edits += more
                if self._blocking(r, r.steps):
                    edits += self._gate_repair(r)                 # the LLM's edits, repaired again
            probs = self._check(r.steps, r.world, r.places, r.goals)
            yield r.ev("gate", round=1, edits=edits, problems=[p_[1] for p_ in probs],
                       plan=[f"{x['action']} {x['object']}" for x in r.steps])
        blocking = self._blocking(r, r.steps)
        if blocking and r.steps and not r.compiled and r.plan_error is None and not getattr(r, "block_edits", 0):
            # a planner whose sub-goal blocks were edited already (and repaired symbolically) gets no fresh plan
            # still critical after the planner's repair (or it has none): one fresh plan with the findings,
            # certified again
            r.steps = self._tidy(r, self._refine_plan(r, blocking))[0]
            blocking = self._blocking(r, r.steps)
            yield r.ev("gate", round=2, problems=blocking, plan=[f"{x['action']} {x['object']}" for x in r.steps])
        r.certified, r.gate_findings = not blocking, blocking
        r.run.plan_meta = dict(meta or {}, plan_sec=round(time.time() - t_plan, 2), llm_plan=self._ground_all(r, raw),
                               edits=edits, certified=r.certified, gate_findings=blocking,
                               repaired_by_op=dict(getattr(r, "repaired_by_op", {})),
                               completed_by_gate=getattr(r, "repaired_by_op", {}).get("completed_by_gate", 0))

    def _plan_call(self, r, name, *args, what="plan", subgoals=None):
        """``r.planner.<name>(*args)`` under the run's LLM-call budget, counting the planner's REAL calls: a
        planner with a budget hook (``budget=``, SAGE >= 0.2.6) takes a unit before each of its calls; any other
        takes one unit before and its reported ``llm_calls`` - 1 after. ``subgoals`` go to a planner that takes
        them (F1.2)."""
        from ..run_control import call_cancellable
        from .base import accepts
        kw = {}
        if subgoals and accepts(r.planner, name, "subgoals"):
            kw["subgoals"] = list(subgoals)
        hooked = accepts(r.planner, name, "budget")
        if hooked:
            kw["budget"] = r.llm_budget
        else:
            r.llm_budget.take(what=what)
        raw, meta = call_cancellable(getattr(r.planner, name), *args, **kw)
        if not hooked:
            extra = int((meta or {}).get("llm_calls") or 1) - 1 if isinstance(meta, dict) else 0
            if extra > 0:
                r.llm_budget.used += extra
        return raw, meta

    def _subgoals(self, r) -> Optional[list]:
        """One sub-goal phrase per understood goal, in goal order (option ``sage_subgoals=goals``), else None
        (the planner decomposes the task itself)."""
        if self.cfg.get("sage_subgoals", "goals") != "goals" or not r.goals:
            return None
        return [goal_kinds.subgoal_text(g, r.places) for g in r.goals]

    @staticmethod
    def _has_blocks(r) -> bool:
        from .base import subgoal_repair
        rep = subgoal_repair(r.planner)
        try:
            return bool(rep is not None and rep.blocks())
        except Exception:
            return False

    def _gate_repair(self, r) -> list:
        """The gate's symbolic repair of r.steps (plan_repair.py): operators proved by the checker, on the
        planner's sub-goal blocks when it has them. Returns the edit notes; counts go to r.repaired_by_op."""
        from . import plan_repair
        from .base import subgoal_repair
        rep = subgoal_repair(r.planner)
        try:
            blocks = rep.blocks() if rep is not None else None
        except Exception:
            blocks = None
        caps = self.robot.model_capabilities()
        ground = lambda s: self._ground_all(r, s)                                        # noqa: E731
        score = lambda s: len(self._blocking(r, self._tidy(r, ground(s))[0]))           # noqa: E731
        steps, notes, counts = plan_repair.repair(
            r.steps, score, r.world, r.places, r.goals, caps=caps, actions=self.robot.plan_actions,
            release=sorted(caps.release_actions), hand_goal=lambda g: bool(g.get("hand")),
            blocks=blocks or None, join=rep.join_blocks if blocks else None, ground=ground,
            complete=str(self.cfg.get("gate_complete", "0")) in ("1", "true", "True"), reached=self._reaches)
        acc = r.__dict__.setdefault("repaired_by_op", {})
        for k, v in counts.items():
            acc[k] = acc.get(k, 0) + v
        if notes:
            new = ground(rep.join_blocks(blocks)) if blocks else steps
            r.steps = self._tidy(r, new)[0]
        return notes

    def _tidy(self, r, steps) -> tuple:
        """plan_tidy.tidy of `steps` on the belief now: (steps, notes); `plan_tidy` off: unchanged."""
        if not self.cfg["plan_tidy"] or not steps:
            return steps, []
        ctx = dict(capabilities=self.robot.model_capabilities(), actions=self.robot.plan_actions,
                   openable=r.places.openable, containers=r.places.openable)
        return plan_tidy.tidy(steps, r.world, r.goals, self._reaches, ctx)

    def _blocking(self, r, steps) -> list:
        """The findings that keep a plan from running (the certified gate): the checker's critical
        ones on the plan as written, and steps the robot cannot bind."""
        unt = self._untouched(r.goals, steps)
        goals = [g for g in r.goals if g not in unt] if unt and len(unt) < len(r.goals) else r.goals
        probs = [p_[1] for p_ in self._check(steps, r.world, r.places, goals) if p_[2]] if steps else []
        return list(dict.fromkeys(probs + self._hard_findings(r, steps)))

    @staticmethod
    def _untouched(goals, steps) -> list:
        """'on' goals the plan never moves whose object's place is unknown: they may hold already
        (the planner left them out); the run looks at the destination at the end instead (D1)."""
        picked = {st.get("object") for st in steps if st.get("action") == "Pick"}
        return [g for g in goals if g.get("type") == "on" and g.get("to") and not g.get("from")
                and not g.get("from_any") and g.get("object") not in picked]

    def _hard_findings(self, r, steps, start: int = 0) -> list:
        """Steps (from index `start`) no belief can make runnable: an action the robot does not
        have, a place not in the ENV, a step its adapter cannot bind (simulated as written)."""
        out, w = [], r.world.copy()
        for i, st in enumerate(steps):
            a, o = st.get("action", ""), st.get("object") or ""
            if i >= start:
                if a not in r.robot.plan_actions and not concrete.skill_only(st):
                    out.append(f"step {i + 1} '{a} {o}': the robot has no action {a}")
                elif a in _LOC_ACTIONS and (not st.get("grounded", True) or o.startswith("@")):
                    out.append(f"step {i + 1} '{a} {o}': {o.lstrip('@')} is not a place the robot can go to")
                else:
                    try:
                        self.robot.bind(dict(st), w)
                    except Unbindable as e:
                        out.append(f"step {i + 1} '{a} {o}': {e}")
                    except Exception:
                        pass                          # an adapter error is the step's own, at run time
            action_model.apply(st, w, inplace=True)
        return out

    def _refine_plan(self, r, findings) -> list:
        """A fresh plan from the planner, told why the last one was rejected (the gate refines at most once)."""
        obs = self._obs(r) + "\nA checker rejected the previous plan:\n" + "\n".join(f"- {t}" for t in findings)
        from ..run_control import call_cancellable, is_cancel
        try:
            raw, _m = self._plan_call(r, "generate_plan", r.restated, obs, [], what="plan refinement",
                                      subgoals=self._subgoals(r))
        except LLMBudgetExceeded as e:
            r.plan_error, raw = e, []
        except Exception as e:
            if is_cancel(e):
                raise
            raw = []
        return self._ground_all(r, raw or [])

    # ── 3. execute: guard each step; new plans only from the planner ──────
    def _sage_replan(self, r, failed, reason) -> Optional[dict]:
        """The planner's suffix repair from step r.i. Returns the event to yield, or None when out of budget or
        the new suffix fails the gate (``r.uncertified``: why)."""
        r.uncertified = []
        if r.replans >= self.cfg["max_replans"] or getattr(r, "compiled", False):
            # a compiled plan has no planner to repair it: the failure ends the run
            return None
        r.replans += 1
        world = r.world
        ers = (f"E: {failed.get('action')} {failed.get('object')} failed. R: {reason}. "
               f"S: robot at {world.arrived}, holding {world.holding or 'nothing'}; goals: {r.goal_txt}")
        r.replan_error = None
        suffix, ers = self._replan_suffix(r, failed, ers)
        bad = self._hard_findings(r, suffix) if r.replan_error is None else []
        if bad and r.replans < self.cfg["max_replans"]:
            r.replans += 1                            # one refinement with the gate's findings
            suffix, ers = self._replan_suffix(r, failed, ers + "; the new plan was rejected: " + "; ".join(bad))
            bad = self._hard_findings(r, suffix) if r.replan_error is None else []
        if r.replan_error is not None:
            # the planner is down: the old rest of the plan is not run either (the caller ends the run with it)
            r.run.replans.append({"at_index": r.i, "failed": failed, "reason": ers, "suffix_len": 0, "problems": []})
            r.steps = r.steps[:r.i]
            return None
        if bad:
            # an uncertified suffix never runs: the future of the plan is dropped
            r.uncertified = bad
            r.run.replans.append({"at_index": r.i, "failed": failed, "reason": ers, "suffix_len": len(suffix),
                                  "problems": bad, "certified": False})
            r.steps = r.steps[:r.i]
            return None
        probs = [p_[1] for p_ in self._check(r.completed + suffix, r.world_at_start, r.places, r.goals) if p_[2]]
        r.run.replans.append({"at_index": r.i, "failed": failed, "reason": ers, "suffix_len": len(suffix),
                              "problems": probs})
        r.steps = r.steps[:r.i] + suffix
        return r.ev("replan", at_index=r.i, reason=ers, problems=probs, search=getattr(r, "steered", None) or None,
                    plan=[f"{x['action']} {x['object']}" for x in suffix],
                    say=r.announcer.announce("replan"), world=world.to_dict())

    def _replan_suffix(self, r, failed, ers):
        """The planner's suffix for the failure `ers`, grounded: (suffix, ers + any planner error)."""
        from ..run_control import call_cancellable, is_cancel
        try:
            suffix, _m = self._plan_call(r, "replan", r.restated, r.completed, failed, ers, self._obs(r), [],
                                         what="replan")
            return self._tidy(r, self._search_steer(r, self._ground_all(r, suffix or [])))[0], ers
        except Exception as e:
            if is_cancel(e):
                raise
            r.replan_error = e                       # kept: the run ends with it (_no_replan_end)
            return [], ers + f"; replan error: {e}"

    @staticmethod
    def _no_replan(r, fail_msg) -> str:
        """Why there is no new plan: its suffix failed the gate, else `fail_msg`."""
        bad = getattr(r, "uncertified", None)
        return f"the new plan did not pass my checks: {'; '.join(bad[:3])}" if bad else fail_msg

    def _no_replan_end(self, r, fail_msg) -> dict:
        """The `done` of a run that needed a new plan and got none: the planner's error when it failed (it is
        down: said as such, with its FailureCode), else ``_no_replan``'s reason."""
        if getattr(r, "replan_error", None) is not None:
            return self._llm_failed(r, r.replan_error, "planner")
        return self._finish(r, "failed", msg=self._no_replan(r, fail_msg))

    def _replan_or_fail(self, r, failed, reason, fail_msg):
        """A replan, else end the run with `fail_msg`: _NEXT or _STOP."""
        e_ = self._sage_replan(r, failed, reason)
        if e_ is None:
            yield self._no_replan_end(r, fail_msg); return _STOP
        yield e_
        return _NEXT

    # ── 3b. what the person says during the run (Run.pending, taken between two steps) ──
    def _apply_pending(self, r):
        """MODIFY / ADD / DROP / NEW said since the last step boundary, applied before the next step
        (proposal 3.3): None (nothing changed), _NEXT (goals changed, the plan repaired) or _STOP."""
        from ..run_control import current_run
        run = current_run()
        events = run.take_pending() if run is not None else []
        changed = False
        for e in events:
            out = yield from (self._new_command(r, run, e) if e.get("intent") == "new" else self._goal_edit(r, e))
            if out is _STOP:
                return _STOP
            changed = changed or out is _NEXT
        if not changed:
            return None
        return (yield from self._repair_goals(r, "; ".join(str(e.get("text") or "") for e in events)))

    def _goal_edit(self, r, e):
        """MODIFY (goal diff), ADD (merged into the goals), DROP (goals removed; the held object first).
        _NEXT when the goals changed; None when the change could not be applied (the person is told)."""
        intent, text = e.get("intent") or "modify", str(e.get("text") or "")
        ch = goal_change.read(text, intent, r.goals, r.places, r.world.holding,
                              reached=lambda g: self._goals_hold([g], r.world, r.placed, r.completed, r.world_at_start.arrived))
        ch = ch if ch is not None and not ch.empty else self._llm_goal_change(r, text)
        if ch is None or ch.empty:
            yield r.ev("goal_change", applied=False, intent=intent, text=text, goals=r.goals,
                       say=self._in_lang(f"Sorry, I could not apply that: {text}. I will carry on.", r.lang))
            return None
        put_back = self._held_dropped(r, ch)
        r.goals = ch.goals + ([put_back] if put_back else [])
        say = self.summary(r.goals) if r.goals else "OK, I will not do that."
        yield r.ev("goal_change", applied=True, intent=intent, text=text, how=ch.how, goals=r.goals,
                   added=ch.added, removed=ch.removed, changed=[n for _o, n in ch.changed],
                   say=self._in_lang(("OK. " if r.goals else "") + say, r.lang))
        return _NEXT

    def _llm_goal_change(self, r, text):
        """The understand LLM reads the change against the task (the cheap reading failed): GoalChange or None."""
        from ..run_control import is_cancel
        dialogue = list(r.dialogue) + [("robot", self.summary(r.goals)), ("user", text)]
        try:
            u = self._understand(r.task, r.places, r.memory, r.world, dialogue, budget=r.llm_budget)
        except Exception as e:
            if is_cancel(e):
                raise
            return None
        goals, q = self._ground_goals(u["goals"], r.places, r.memory, " ".join([r.task, text]), latest=text)
        if q or u.get("status") != "clear":
            return None
        key = lambda g: (g.get("type"), g.get("object"), g.get("to"))
        old, new = {key(g) for g in r.goals}, {key(g) for g in goals}
        return goal_change.GoalChange(goals=goals, added=[g for g in goals if key(g) not in old],
                                      removed=[g for g in r.goals if key(g) not in new], how="llm")

    def _held_dropped(self, r, ch):
        """A goal of the object in the hand was dropped: the goal that puts it back where it was taken
        from (cfg drop_held 'put_back', the default) or down here ('place_here'); else None."""
        held = r.world.holding
        if not held or any(g.get("object") == held for g in ch.goals) or not any(
                g.get("object") == held for g in ch.removed):
            return None
        src = next((g.get("from") for g in ch.removed if g.get("object") == held and g.get("from")), "")
        if not src:
            moves = [st["object"] for st in r.completed if st.get("action") == "MoveTo"]
            src = moves[-1] if moves else (r.world.arrived or "")
        to = r.world.arrived if self.cfg.get("drop_held") == "place_here" else src
        return {"type": "on", "object": held, "from": "", "to": to} if to else None

    def _new_command(self, r, run, e):
        """NEW during a run: someone who outranks the run's starter goes first; else the person is asked
        "first, or after this task?" (no answer: after). First = the current task is suspended on the
        task stack (goals, achieved goals, belief) and resumed afterwards. None / _NEXT / _STOP."""
        text = str(e.get("text") or "")
        ch = goal_change.read(text, "new", [], r.places) or self._llm_new_task(r, text)
        if ch is None or not ch.goals:
            yield r.ev("goal_change", applied=False, intent="new", text=text, goals=r.goals,
                       say=self._in_lang(f"Sorry, I did not understand: {text}. I will finish this task first.", r.lang))
            return None
        new = ch.goals
        key = lambda g: (g.get("type"), g.get("object"), g.get("to"))
        if {key(g) for g in new} <= {key(g) for g in r.goals}:
            yield r.ev("goal_change", applied=False, intent="new", text=text, goals=r.goals,
                       say=self._in_lang(f"I am already doing that. {self.summary(r.goals)}", r.lang))
            return None
        first = run is not None and run.outranked_by(e.get("role"))
        if not first:
            q = (f"Someone asked me to {self.summary(new)[len('I will '):].rstrip('.')}. Should I do that first? "
                 f"If not, I will do it after the current task.")
            yield r.ev("clarify", question=q, round=0, kind="new_task")
            ans = self._ask(q, r.node, self._log_fn(r.i), r.lang)
            stop = self._cancelled(r)
            if stop:
                yield stop; return _STOP
            first = self.yes_no(ans)[0] is True or bool(re.search(r"\b(now|first|right away)\b", ans.lower()))
        if not first:
            r.goals = r.goals + [g for g in new if g not in r.goals]
            yield r.ev("goal_change", applied=True, intent="new", text=text, how=ch.how, goals=r.goals, added=new,
                       say=self._in_lang(f"OK, after this task. {self.summary(new)}", r.lang))
            return _NEXT
        yield from self._suspend_for(r, new, text)
        return _NEXT

    def _llm_new_task(self, r, text):
        from ..run_control import is_cancel
        try:
            u = self._understand(text, r.places, r.memory, r.world, [], budget=r.llm_budget)
        except Exception as e:
            if is_cancel(e):
                raise
            return None
        goals, q = self._ground_goals(u["goals"], r.places, r.memory, text)
        return None if q or u.get("status") != "clear" else goal_change.GoalChange(goals=goals, added=goals, how="llm")

    def _suspend_for(self, r, new, text):
        """Suspend the current task on the task stack and make ``new`` the goals. An object in the hand
        is put down here first (the suspended task picks it up from here again)."""
        from ..run_control import suspend_task
        held, here = r.world.holding, r.world.arrived
        old = [dict(g) for g in r.goals]
        put_down = []
        if held and here and not r.places.openable(here):
            put_down = [{"type": "on", "object": held, "from": "", "to": here}]
            for g in old:
                if g.get("object") == held:
                    g["from"] = here
        achieved = [g for g in old if self._goals_hold([g], r.world, r.placed, r.completed, r.world_at_start.arrived)]
        frame = suspend_task(r.task, old, achieved, r.world.to_dict(), reason=f"new task first: {text}")
        r.saved_goals = getattr(r, "saved_goals", []) + [old]          # the stack is the run's; this a fallback
        r.suspended += 1
        r.goals = put_down + [dict(g) for g in new]
        yield r.ev("goal_change", applied=True, intent="new", text=text, goals=r.goals, suspended=old,
                   suspended_frame=frame.to_dict() if frame is not None else None,
                   say=self._in_lang(f"OK, first that. {self.summary(new)} Then I will go back to my task.", r.lang))

    def _resume_suspended(self, r):
        """The interrupting task is done: take the suspended one back (task stack), observe again
        (reconcile), and repair the plan from here. _NEXT, _DONE (nothing left to do) or _STOP."""
        from ..run_control import mark_resumed, resume_task
        frame = resume_task()
        old = frame.goals if frame is not None else r.saved_goals[-1]
        r.saved_goals = r.saved_goals[:-1]
        r.suspended -= 1
        fixed = self._reconcile(r, "resume_task")       # observe again (RunManager on_resume fired)
        mark_resumed()
        if fixed is not None:
            yield fixed
        r.goals = [dict(g) for g in old]
        yield r.ev("task_resumed", goals=r.goals, say=self._in_lang("Now back to my task. " + self.summary(r.goals), r.lang))
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            return _DONE
        return (yield from self._repair_goals(r, "resumed the task the new one interrupted"))

    def _repair_goals(self, r, said):
        """The goals changed: the planner's suffix repair from here. _NEXT or _STOP."""
        r.goal_txt = self._goal_text(r.goals)
        r.run.obs = "goals: " + json.dumps(r.goals, ensure_ascii=False)
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            r.steps = r.steps[:r.i]
            return _NEXT
        return (yield from self._replan_or_fail(r, {"action": "Change", "object": "goals"},
                                                f"the user changed the goals ({said})",
                                                f"could not replan for the changed goals: {r.goal_txt}"))

    def _execute_phase(self, r):
        """Run the plan step by step. True when the run ended."""
        r.placed = {}
        r.pick_tries, r.move_tries, r.helped, r.seen_fail, r.seen_at, r.find_fails = {}, {}, set(), {}, {}, {}
        r.completed, r.replans, r.i, r.replan_error = [], 0, 0, None
        r.code_tries, r.doubted, r.failures, r.goal_checks, r.health = {}, set(), [], [], None
        r.world_at_start = r.world.copy()
        r.suspended = 0                      # tasks this run put on the task stack (a NEW one went first)
        while True:
            # a paused run holds here, between two steps; after a resume (RunManager on_resume) the belief
            # is reconciled and a Pick on a Find made before the pause looks again first
            if (yield from self._pause_point(r)):
                yield from self._refind_after_pause(r)
            stop = self._cancelled(r)
            if stop:
                yield stop; return True
            out = yield from self._apply_pending(r)          # what the person said meanwhile
            if out is None:
                out = (yield from self._plan_ended(r)) if r.i >= len(r.steps) else (yield from self._run_step(r, r.steps[r.i]))
            if out is _DONE and r.suspended:
                out = yield from self._resume_suspended(r)  # back to the task the NEW one interrupted
            if out is _STOP:
                return True
            if out is _DONE:
                return False

    def _refind_after_pause(self, r):
        """After a pause the found object may have moved: forget where it was seen and look again before
        the Pick that would act on it."""
        st = r.steps[r.i] if r.i < len(r.steps) else {}
        if r.world.found and st.get("action") == "Pick":
            o = st.get("object") or r.world.found
            r.world.found = None; r.world.found_pose = None
            r.steps[r.i:r.i] = [{"action": "Find", "object": o}]
            yield r.ev("recover", at_index=r.i, kind="refind", msg=f"resumed after a pause: looking for the {o} again")

    def _plan_ended(self, r):
        """The plan ran out: _DONE when the goals hold (in the belief, then looked at), else look at goals
        the plan left out (they may hold already), else a replan (_NEXT) or _STOP."""
        unmet = []
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            unmet = yield from self._observe_goals(r)
            if unmet is None:
                return _STOP                                     # cancelled while looking
            if not unmet:
                return _DONE
        elif self._look_at_untouched(r):
            yield r.ev("status", msg="checking the goals the plan did not touch")
            return _NEXT
        why = ("; ".join(u["evidence"]["observed"] for u in unmet) if unmet else
               "; ".join(self._missing(r.completed, r.world_at_start, r.goals)))
        e_ = self._sage_replan(r, {"action": "Check", "object": "goals"},
                               "the plan ended but the goals are not reached: " + why)
        if e_ is None or r.i >= len(r.steps):
            if getattr(r, "replan_error", None) is not None:    # the planner died: say that, not "goals do not hold"
                yield self._no_replan_end(r, ""); return _STOP
            fail = dict(unmet[0]) if unmet else {}
            msg = (f"the goals do not hold: {why}" if unmet else
                   self._no_replan(r, "plan finished but the goals do not hold: " + r.goal_txt))
            yield self._finish(r, "failed", msg=msg, say=(why if unmet else None), **fail); return _STOP
        yield e_
        return _NEXT

    def _look_at_untouched(self, r) -> bool:
        """Append a MoveTo + Find at the destination of every unmet goal the run never moved (once each);
        a Find that sees the object there makes the goal hold by observation. True when steps were added."""
        looked = r.__dict__.setdefault("looked_goals", set())
        at, add = r.world.arrived, []
        for g in self._untouched(r.goals, r.completed):
            o, to = g["object"], g["to"]
            if (o, to) in looked or r.placed.get(o) == to or r.world.holding == o or to not in r.places.env:
                continue
            looked.add((o, to))
            if at != to:
                add.append({"action": "MoveTo", "object": to, "grounded": True}); at = to
            add.append({"action": "Find", "object": o, "verify_goal": to})
        r.steps[r.i:r.i] = add
        return bool(add)

    def _observe_goals(self, r):
        """Done only by observation: each on / in goal is looked at (cfg goal_check). Returns the goals seen
        NOT to hold as failures [{code, device, evidence}] (their belief is withdrawn), None when cancelled.
        A goal that cannot be looked at (inside a closed drawer, no Find skill) stays on the belief, as
        `unknown` in r.goal_checks."""
        if self.cfg["goal_check"] != "observe":
            return []
        last = r.completed[-1] if r.completed else {}
        unmet, last_placed = [], (last.get("object") if last.get("action") in ("Place", "PutIn") else None)
        yield r.ev("status", msg="Checking the goals...")
        for g in sorted(r.goals, key=lambda g: g.get("to") != r.world.arrived):   # where the robot stands first
            ok, ev_, device = self._observe_goal(r, g)
            stop = self._cancelled(r)
            if stop:
                yield stop; return None
            r.goal_checks.append({"goal": f"{g['type']} {g['object']} {g.get('to', '')}".strip(),
                                  "ok": ok, "evidence": ev_})
            yield r.ev("goal_check", goal=g, ok=ok, evidence=ev_, world=r.world.to_dict())
            if ok is False:
                r.placed.pop(g["object"], None); r.memory.see(g["object"], None, "not at its goal place")
                code = FailureCode.DROPPED if g.get("to") == last_placed else FailureCode.ABSENT
                unmet.append({"code": code.value, "device": device,
                              "evidence": {"goal": r.goal_checks[-1]["goal"], "observed": ev_}})
        return unmet

    def _observe_goal(self, r, g):
        """Look at one goal: (True | False | None = cannot tell, evidence text, device)."""
        if g.get("type") not in ("on", "in"):
            return None, f"{g.get('type')} {g.get('object')}: no sensor declared for it (belief)", None
        o, place = g["object"], g.get("to") or ""
        nice = place.replace("@", " in the ")
        if r.places.openable(place) and place not in r.world.opened:
            return None, f"the {o} is inside the closed {nice}: not visible (belief)", None
        if r.world.arrived != place:
            res, _sk = self._look(r, {"action": "MoveTo", "object": place})
            if res is None or not res.ok:
                return None, f"could not go back to the {nice} to look ({res.msg if res else 'no MoveTo skill'})", _sk
        res, skill = self._look(r, {"action": "Find", "object": o})
        if res is None:
            return None, f"no Find skill to look for the {o}", None
        if res.ok:
            r.memory.see(o, place, "seen at its goal place")
            return True, f"the {o} is seen on the {nice}", skill
        code = r.robot.failure_code(res, res.msg)
        if code is FailureCode.ABSENT:
            return False, f"the {o} is not on the {nice} (looked after the task: {res.msg or 'not found'})", skill
        return None, f"could not look for the {o} ({code.value}: {res.msg})", skill

    def _look(self, r, st):
        """Run a MoveTo / Find of the goal check (its effect applied on success): (SkillResult | None, skill)."""
        try:
            mapped = self._bind(dict(st), r.world)
        except Unbindable:
            mapped = None
        if not mapped:
            return None, None
        skill, params = mapped
        res = run_skill(self.sr, skill, params, r.node, log_fn=self._log_fn(r.i))
        if res.ok:
            action_model.apply(st, r.world, inplace=True)
        return res, skill

    def _precondition(self, r, st) -> str:
        """Why the step cannot run in the belief ('' when it can)."""
        a, o, world = st["action"], st["object"], r.world
        if a in ("Place", "PutIn", "Open", "Close") and o in r.places.env and world.arrived != o:
            return f"the robot is at {world.arrived}, not at {o}"
        if a in ("Open", "Close") and world.holding:
            return f"the robot cannot {a.lower()} {o} while holding the {world.holding}"
        ok_pre, why_ = symbolic_check(st, world, openable=r.places.openable, capabilities=r.robot.model_capabilities())
        return "" if ok_pre else why_

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

    def _guard_arrived(self, r, st):
        """A non-sensor `arrived` (operator / belief) that a fresh pose contradicts is stale: fixed, the
        ``recover kind=reconcile`` event (None when nothing changed)."""
        if not self.cfg["reconcile"]:
            return None
        fix = sensed.guard_arrived(r.world, r.robot, r.node, self._sense(r), r.places.env, self.cfg["arrive_tol"], st)
        if fix is None:
            return None
        self._save_world()
        return r.ev("recover", at_index=r.i, kind="reconcile", was=fix["old"], now=fix["new"],
                    msg=f"the robot is at {fix['new'] or 'no known place'}, not at {fix['old']} "
                        f"({fix['source']} belief; pose {fix['detail']})")

    def _guard(self, r, st):
        """Preconditions before the robot moves (violations go to the planner): None (go on), _NEXT, _STOP."""
        a, o, world = st["action"], st["object"], r.world
        fixed = self._guard_arrived(r, st)
        if fixed is not None:
            yield fixed
        why = self._precondition(r, st)
        if why and a == "Pick" and world.holding and not self._holding(r.node, default=True):
            # the belief holds something, the hand is empty (a stale belief): fix it and look again
            yield r.ev("recover", at_index=r.i, kind="reconcile", msg=f"the {world.holding} is not in the gripper")
            world.holding = world.holding_since = None
            why = self._precondition(r, st)
        if not why and a in ("Place", "PutIn") and world.holding:
            # still in the hand? (dropped on the way: `place` would otherwise go and pick something up)
            if not self._holding(r.node, default=True):
                lost = world.holding
                r.memory.see(lost, None, "dropped")
                world.holding = world.holding_since = None
                yield r.ev("recover", at_index=r.i, kind="dropped", msg=f"{lost} is no longer in the gripper")
                yield self._finish(r, "failed", msg=f"I dropped the {lost} on the way to {o.replace('@', ' in the ')}. "
                                                    f"Please pick it up; I can't pick things up from the floor.",
                                   code=FailureCode.DROPPED, device=self.robot.holding_sensor(),
                                   evidence={"step": f"{a} {o}", "observed": f"{lost} is no longer in the gripper"})
                return _STOP
        if not why:
            return None
        sig = ("pre", a, o, why)
        r.seen_fail[sig] = r.seen_fail.get(sig, 0) + 1
        e_ = self._sage_replan(r, st, f"precondition: {why}") if r.seen_fail[sig] <= 1 else None
        if e_ is None:
            yield self._no_replan_end(r, f"{a} {o}: {why}"); return _STOP
        yield e_
        return _NEXT

    def _run_step(self, r, st):
        """Guard, bind, run and check one step: _NEXT, _DONE (goals reached) or _STOP."""
        a, o, started = st["action"], st["object"], time.time()
        out = yield from self._guard(r, st)
        if out is not None:
            return out
        try:
            mapped = self._bind(st, r.world)
        except Unbindable as e:
            return (yield from self._replan_or_fail(r, st, f"no skill for this step: {e}", f"{a} {o}: {e}"))
        if mapped is None:                                   # no-op action
            r.run.steps.append(StepRecord(index=r.i, action=a, object=o, skill=None, status="success",
                                          started_at=started, ended_at=time.time(),
                                          verifies=[VerifyResult("isdone", True, "no-op")]))
            r.completed.append(st); r.i += 1
            yield r.ev("step_done", index=r.i - 1, status="success", skill=None, world=r.world.to_dict())
            return _NEXT
        skill, params = mapped
        rec = StepRecord(index=r.i, action=a, object=o, skill=skill, params=params,
                         status="running", started_at=started)
        yield r.ev("step_start", index=r.i, action=a, object=o, skill=skill,
                   say=r.announcer.announce("step_start", **self._say(st, a, o)))
        # manifest interruptible: false (a grasp, a drawer pull): a stop meanwhile waits for it to end
        # a stale camera before a Find: perception_unavailable without running it (sensor_health)
        res = find_precheck(r.robot, r.node, st, self.cfg["sensor_health"])
        if res is None:
            res = run_skill(self.sr, skill, params, r.node, log_fn=self._log_fn(r.i),
                            interruptible=r.robot.interruptible(skill))
        rec.result = res.to_legacy()
        stop = self._cancelled(r)
        if stop:
            rec.status = "aborted"; r.run.steps.append(rec)
            if res.ok:
                self._effect(st, r.world, r.memory, r.placed)   # it finished: the belief keeps what it did
            yield stop; return _STOP
        vres = self._post_check(r, st, skill, res, params, rec)
        rec.verifies = vres
        ok, reason = verdict(vres)
        rec.ended_at = time.time()
        yield r.ev("step_verify", index=r.i, verifies=_vr_list(vres))
        if ok:
            return (yield from self._step_succeeded(r, st, rec))
        return (yield from self._step_failed(r, st, skill, rec, res, reason))

    def _post_check(self, r, st, skill, res, params, rec) -> list:
        """After a reported success, the effects + the skill's VerifySpecs (manifest: Pick looked at again
        once the arm is folded, MoveTo measured against the ENV loc); a release looked at twice; a Find
        seen out of reach."""
        a, o = st["action"], st["object"]
        vres = r.verifier.verify(st, res, r.world, r.node, params=params)
        vres += arrival_check(r.robot, r.node, st, vres, self.cfg["sensor_health"])   # a stale pose: not arrived
        if (a in ("Place", "PutIn") and vres and not vres[0].ok
                and res.code is FailureCode.GRASP_FAILED and not self._holding(r.node, default=True)):
            # the release's own empty-gripper check fired right after letting go; looked once more
            vres = [VerifyResult("isdone", True, f"{skill}: second look, gripper empty"),
                    VerifyResult("recheck", True, f"first check: {vres[0].detail}")]
        if a == "Find" and vres and vres[0].ok:
            p3 = self._found_point(rec.result, o)
            r.seen_at[o] = p3
            if p3 and math.hypot(p3[0], p3[1]) > self.cfg["reach_far"]:
                near = self._place_of_far_point(p3, r.node, r.places, r.world)
                vres.append(VerifyResult("reach", False, f"the {o} was seen {math.hypot(p3[0], p3[1]):.1f} m away"
                                         + (f", near {near}" if near else ", not near any place") + ", out of reach from here"))
                if near:
                    r.seen_far.append(near); r.memory.see(o, near, "seen from afar")
                    if getattr(r, "search", None) is not None:
                        r.search.see(o, near, how="seen from afar")
        return vres

    def _step_succeeded(self, r, st, rec):
        a, o = st["action"], st["object"]
        rec.status = "success"; r.run.steps.append(rec)
        self._search_effect(r, st)                       # before the effect: where the robot did it
        self._effect(st, r.world, r.memory, r.placed)
        if a == "MoveTo":
            sensed.confirm_arrival(r.world, rec.verifies)    # measured at its target: sensor provenance
        if st.get("verify_goal"):
            r.placed[o] = st["verify_goal"]             # seen at its destination: the goal holds
        r.completed.append(st)
        yield r.ev("step_done", index=r.i, status="success", result=rec.result,
                   say=r.announcer.announce("step_success", **self._say(st, a, o)), world=r.world.to_dict())
        r.i += 1
        for f in r.failures:
            if f["sig"] == (a, o):
                f["recovered"] = True                    # the step that failed before has now succeeded
        if (r.i < len(r.steps) and self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived)
                and not plan_tidy.closes_only(r.completed, r.steps[r.i:], r.world_at_start)):
            # the user's goals are met: end the task here rather than run what is left of the plan (the
            # goals are then looked at, _plan_ended)
            yield r.ev("status", msg=f"goals reached; {len(r.steps) - r.i} remaining plan steps not run")
            del r.steps[r.i:]
        return _NEXT

    def _step_failed(self, r, st, skill, rec, res, reason):
        """A failed step: stop (policy / same failure again), recover in place, or replan."""
        a, o = st["action"], st["object"]
        rec.status = "failed"; r.run.steps.append(rec)
        yield r.ev("step_done", index=r.i, status="failed", result=rec.result, reason=reason,
                   say=r.announcer.announce("step_fail", **self._say(st, a, o), reason=reason),
                   world=r.world.to_dict())
        if st.get("verify_goal"):
            r.world.found = None; r.i += 1              # not there: the goal does not hold yet (replan at the end)
            return _NEXT
        # ── stop at once: the recovery policy does not retry it (a skill bug, an e-stop), or the same
        # failure twice ──
        code = r.robot.failure_code(res, reason)
        rule = r.robot.policy.rule_for(code)
        device = res.device or skill
        self._note_failure(r, st, code, device, reason)
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
        if r.seen_fail[sig] > 2 and a != "Find":
            yield self._finish(r, "failed", msg=f"{a} {o} failed again the same way: {reason}"); return _STOP
        # ── skill-level retry in place / ask the person (not planning) ──
        no = refuted(rec.verifies)                     # the VLM / its Find said it did not happen: replan
        recover = None if no is not None else {"Find": self._recover_find, "Pick": self._recover_pick,
                                               "MoveTo": self._recover_move, "Place": self._recover_place,
                                               "PutIn": self._recover_place}.get(a)
        out, extra = (yield from recover(r, st, reason, rule)) if recover else (None, self._refuted(r, st, no))
        if out is not None:
            return out
        # ── a new plan: the planner's suffix repair ──
        return (yield from self._replan_or_fail(r, st, f"{reason}{extra}",
                                                f"{a} {o} failed: {reason} (replan budget used)"))

    def _apply_policy(self, r, st, skill, code, rule, reason, device):
        """The recovery policy's own actions (contract.recovery, by FailureCode) before the per-action
        recovery: STOP once the rule's retries are used up, WAIT_HEALTH (wait for the robot's health, then
        the same step again in place), HEALTH_CHECK (an unhealthy robot stops the run). None: go on."""
        key = (code, st.get("action"), st.get("object"))
        r.code_tries[key] = n = r.code_tries.get(key, 0) + 1
        todo = rule.next_after(n)
        ev_ = r.failures[-1]["evidence"]
        if todo is Recovery.STOP:
            if r.health is not None:
                ev_["health"] = r.health                  # what the robot said the last time it was asked
            yield self._finish(r, "failed", code=code, device=device, evidence=ev_,
                               msg=f"{st['action']} {st['object']} failed {n} times ({code.value}): {reason}")
            return _STOP
        if todo not in (Recovery.WAIT_HEALTH, Recovery.HEALTH_CHECK):
            return None
        wait_s = self.cfg["health_wait_s"] if todo is Recovery.WAIT_HEALTH else 0.0
        r.health = health = yield from self._wait_health(r, wait_s)
        ev_["health"] = health
        if health.get("ok") is False:
            yield self._finish(r, "failed", code=code, device=device, evidence=ev_,
                               msg=f"the robot is not healthy after {skill} failed ({code.value}): "
                                   f"{health.get('error') or health}")
            return _STOP
        if todo is Recovery.HEALTH_CHECK:
            return None
        if st.get("action") == "Find":
            r.world.found = None
        yield r.ev("recover", at_index=r.i, kind="wait_health", code=code.value,
                   msg=f"{skill} could not run ({code.value}); waited for the robot, trying again")
        return _NEXT                                      # the same step again, in place (r.i unchanged)

    def _wait_health(self, r, wait_s):
        """The adapter's health(), polled up to `wait_s` until it reads ok (a cancel ends the wait)."""
        from ..run_control import cancel_requested
        t_end = time.time() + max(0.0, wait_s)
        while True:
            h = r.robot.health(r.node)
            if h.get("ok") is True or time.time() >= t_end or cancel_requested():
                yield r.ev("health", health=h)
                return h
            time.sleep(min(0.1, max(0.0, t_end - time.time())))

    def _recover_find(self, r, st, reason, rule):
        """Not seen here: mark the place searched (absence only, the policy says), search on through
        the planner, ask the person where it is after search_max. Returns (outcome or None, replan hint)."""
        o, world = st["object"], r.world
        world.found = None
        here, code = world.arrived, r.failures[-1]["code"] if r.failures else ""
        if rule.mark_searched and here and here in world.opened and r.places.openable(here) and here not in r.doubted:
            # not seen inside a container believed open: the belief may be stale (closed meanwhile); doubt it
            # once instead of counting the place as searched (the repair opens it and looks again)
            r.doubted.add(here); world.opened.discard(here)
            return None, (f"; precondition: {here} is not open (it was believed open, but the {o} is not visible "
                          f"inside): Open {here}, then Find the {o} again")
        # absent: the place counts as searched; could not look / out of reach: it does not (policy)
        if world.arrived and world.arrived not in r.searched and rule.mark_searched:
            r.searched.append(world.arrived)
        leg = yield from self._search_missed(r, o, rule)
        # the belief has nowhere left to look (searched places are never searched again): ask / give up now
        nowhere = leg is None and rule.mark_searched and getattr(r, "search", None) is not None
        r.find_fails[o] = r.find_fails.get(o, 0) + 1
        if (r.find_fails[o] > self.cfg["search_max"] or nowhere) and self.cfg["ask_help"] and ("where", o) not in r.helped:
            r.helped.add(("where", o))
            q = speech.LocalText("q_where_is", {"object": self._said_of(r, o)}, text=f"I couldn't find the {o}. Where is it?")
            yield r.ev("clarify", question=q, round=0)
            ans = self._ask(q, r.node, self._log_fn(r.i), r.lang)
            k = r.places.ground(ans, ans) if ans else None
            if not k and ans:
                k = yield from self._confirm_near(r, ans)        # a noisy answer: "Did you mean the X?" (F3.7)
            if not k and self.cfg.get("search_legs"):
                return (yield from self._search_on(r, o))       # "I don't know": search the likely rest (F6.2)
            if not k:
                yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})")
                return _STOP, ""
            r.memory.remember_answer(r.task, q, ans); r.memory.see(o, k, "user said")
            self._search_told(r, o, k)
            if k in r.searched:
                r.searched.remove(k)
            return None, f"; the user says the {o} is on {k}"
        if r.find_fails[o] > self.cfg["search_max"] + 1 or nowhere or (
                self.cfg.get("search_legs") and len(r.searched) >= int(self.cfg.get("search_total", 8))):
            yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})")
            return _STOP, ""
        near = f" (it was seen near {r.seen_far[-1]})" if r.seen_far else ""
        if code == FailureCode.UNREACHABLE.value:
            return None, f"; it is out of reach from {here}{near}"
        if not rule.mark_searched:
            # could not look: no evidence that it is not here (the replan reason is worded by code)
            return None, f"; the {o} could not be checked at {here} ({code}): it may still be there{near}"
        if leg is not None and self.cfg.get("search_legs") and self._splice_leg(r, leg):
            # the belief's next look replaces the failed Find in place: no replan call (SAGE-v2 F6.1)
            yield r.ev("recover", at_index=r.i, kind="search_leg", msg=f"looking for the {o} at {leg.text()}")
            return _NEXT, ""
        nxt = (f"Look next at {leg.text()}" if leg is not None else
               "Choose where to look next among the places not yet searched")
        return None, (f"; it is not at {world.arrived}. {nxt}"
                      + (f" (it was seen near {r.seen_far[-1]})" if r.seen_far else ""))

    def _splice_leg(self, r, leg) -> bool:
        """Put the search leg's steps (MoveTo, Open when closed, Find) in place of the failed Find at r.i, when
        the rest of the plan still runs from there by the action model; False (the planner replans) else."""
        new = self._ground_all(r, leg.steps(r.world.arrived) + [dict(x) for x in r.steps[r.i + 1:]])
        w, caps = r.world.copy(), self.robot.model_capabilities()
        w.found = None
        for st in new:
            errs = [v for v in action_model.check(st, w, capabilities=caps, actions=self.robot.plan_actions,
                                                  openable=r.places.openable) if v.is_error]
            if errs:
                return False
            action_model.apply(st, w, inplace=True, capabilities=caps)
        r.steps[r.i:] = new
        return True

    @staticmethod
    def _said_of(r, obj) -> str:
        """The person's own words for ``obj`` (the understood goal's ``said``, F4.5), else the object name."""
        return next((g.get("said") for g in getattr(r, "goals", []) or [] if g.get("object") == obj and g.get("said")),
                    obj)

    def _confirm_near(self, r, ans):
        """The one place an answer with ASR noise nearly names (understand.fuzzy_place), confirmed with a yes /
        no question; None."""
        from .understand import fuzzy_place
        k, _n = fuzzy_place(r.places, ans)
        if not k:
            return None
        q = speech.LocalText("q_did_you_mean", {"object": self._say_name(r.places, k), "place": k})
        yield r.ev("clarify", question=q, round=0, kind="did_you_mean")
        _a, ok_, _rest = self._ask_yes_no(r, q)
        return k if ok_ is True else None

    def _search_on(self, r, o):
        """No place from the person: the search goes on over the likely places not searched yet (up to
        ``search_total`` looks in all), else the run ends naming the places looked at (F6.2)."""
        leg = r.search.next_leg(o, r.world.arrived, r.world.opened) if getattr(r, "search", None) is not None else None
        looks = len(r.searched)
        if leg is None or looks >= int(self.cfg.get("search_total", 8)):
            named = ", ".join(self._say_name(r.places, k) for k in r.searched) or "nowhere"
            yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})",
                               say=f"I couldn't find the {o}. I looked at: {named}.")
            return _STOP, ""
        r.find_fails[o] = 0                                     # the belief's next legs, without the person
        return None, f"; it is not at {r.world.arrived}. Look next at {leg.text()}"

    def _recover_pick(self, r, st, reason, rule):
        """Look + grasp again in place (pick_retries, not when out of lateral reach), then ask the person
        to move it closer. Returns (outcome or None, replan hint)."""
        o, world = st["object"], r.world
        if self._holding(r.node, default=False):
            # failed, yet something is in the gripper (a neighbour?): never carry on with an unknown object
            world.holding = f"unknown object (picking {o})"
            yield self._finish(r, "failed", msg=f"the pick of the {o} failed but something is in my gripper; "
                                                f"please take it from me: {reason}")
            return _STOP, ""
        r.pick_tries[o] = r.pick_tries.get(o, 0) + 1
        p3 = r.seen_at.get(o)
        out_of_reach = bool(p3) and abs(p3[1]) > self.cfg["reach_lateral"]
        if r.pick_tries[o] <= self.cfg["pick_retries"] and not out_of_reach:
            # retry the same skill pair in place: look again, grasp again
            r.steps[r.i:r.i] = [{"action": "Find", "object": o}]
            world.found = None
            yield r.ev("recover", at_index=r.i, kind="retry", msg=f"pick {o} failed; looking again")
            return _NEXT, ""
        extra = ""
        if self.cfg["ask_help"] and ("pick", o) not in r.helped:
            r.helped.add(("pick", o))
            q = (f"The {o} is too far for my arm. Could you move it closer to the edge, then say yes?"
                 if out_of_reach else f"I can't pick up the {o}. Could you move it closer to the edge, then say yes?")
            yield r.ev("clarify", question=q, round=0)
            ans = self._ask(q, r.node, self._log_fn(r.i), r.lang)
            k = r.places.ground(ans, ans) if ans else None
            if ans and self.yes_no(ans)[0] is True:
                r.steps[r.i:r.i] = [{"action": "Find", "object": o}]
                world.found = None
                yield r.ev("recover", at_index=r.i, kind="asked", msg="user helped; trying again")
                return _NEXT, ""
            if k and k != world.arrived:
                r.memory.see(o, k, "user said"); world.found = None
                self._search_told(r, o, k)
                extra = f"; the user says the {o} is on {k}"
        if not extra:
            yield self._finish(r, "failed", msg=f"could not pick up the {o}: {reason}")
            return _STOP, ""
        return None, extra

    def _recover_move(self, r, st, reason, rule):
        """Move again once, then ask the person to clear the way; else stop."""
        o = st["object"]
        r.move_tries[o] = r.move_tries.get(o, 0) + 1
        if r.move_tries[o] <= 1:
            yield r.ev("recover", at_index=r.i, kind="retry", msg=f"move to {o} again")
            return _NEXT, ""
        if self.cfg["ask_help"] and ("move", o) not in r.helped:
            r.helped.add(("move", o))
            q = (f"I can't get to the {o.replace('@', ' in the ')}. Is something in the way? "
                 f"Please clear the way and say yes.")
            yield r.ev("clarify", question=q, round=0)
            ans, _n = self._ask_heard(q, r.node, self._log_fn(r.i), r.lang)
            if self.yes_no(ans)[0] is True:
                yield r.ev("recover", at_index=r.i, kind="asked", msg="user cleared the way; trying again")
                return _NEXT, ""
        yield self._finish(r, "failed", msg=f"could not get to {o}: {reason}")
        return _STOP, ""

    def _refuted(self, r, st, no) -> str:
        """A step the VLM post-check refuted: its effects stay out of the belief; a release that
        emptied the hand leaves the object's place unknown. The replan hint."""
        if no is None:
            return ""
        world = r.world
        if st.get("action") in ("Place", "PutIn") and world.holding and not self._holding(r.node, default=True):
            r.memory.see(world.holding, None, "not seen where it was put")
            world.holding = world.holding_since = None
        world.found = None
        return f"; the check after {st.get('action')} {st.get('object')} says it did not happen ({no.detail})"

    def _recover_place(self, r, st, reason, rule):
        """A failed release with an empty hand: the object is gone, stop; else the planner replans."""
        world = r.world
        if not self._holding(r.node, default=True):
            if world.holding:
                r.memory.see(world.holding, None, "dropped")
            world.holding = world.holding_since = None
            yield self._finish(r, "failed", msg=f"place failed and the object is no longer in the hand: {reason}")
            return _STOP, ""
        return None, ""

    # ── 4. done only when the goals hold ──────────────────────────────────
    def _final_check(self, r) -> dict:
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            if not getattr(r, "compiled", False):            # the planner's memory learns its own plans only
                # ... and only when the goals were seen to hold (planning/episode_memory.py)
                episode_memory.record_if_verified(r.planner, r.restated, r.completed, r.goals,
                                                  getattr(r, "goal_checks", []),
                                                  mode=self.cfg.get("memory_record"), run_id=r.run_id)
            return self._finish(r, "success")
        return self._finish(r, "failed", msg="plan finished but the goals do not hold: "
                            + json.dumps(r.goals, ensure_ascii=False))
