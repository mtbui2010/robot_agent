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
from .which_one import WhichOne
from . import goals as goal_kinds
from . import dialog_converge, exec_integrity, goal_filter, goal_fix, language, persons, plan_tidy, speech
from . import reconcile as sensed
from .robot import run_skill
from .verify import symbolic_check, verdict
from .verify_vlm import refuted
from .budget import DEFAULT_LLM_BUDGET, budget_end
from .sensor_health import arrival_check, find_precheck
from .understand import (LLMBadOutput, LLMBudget, LLMBudgetExceeded, LLMUnavailable, ask_json, consensus,
                         describe, find_issues, vacuous_goal_issues, issue_question, misheard_question, sampling, typed_refusal, verb_lost,
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
                "search_legs", "object_check", "detector_miss_rate", "person_goals", "accept_goals",
                "go_steps", "kind_anchor", "name_canon", "reask_abstain",
                "exec_integrity", "gate_strict", "endstate_check", "reset_container", "wait_on_robot", "inventory_ask"}
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
        phrase = lambda h: bool(h) and re.search(r"(?<!\w)" + re.escape(h) + r"(?!\w)", rest)      # noqa: E731
        hd = lambda k: words(self.head(k))                                                     # noqa: E731
        hits = [k for k in self.keys if rest and (self.head(k) == rest or rest in self.head(k)
                                                     or phrase(hd(k)))]
        # the longest name said wins: "in the wine fridge, please" is not the fridge (fixer rerun of m5)
        said_heads = [hd(k) for k in hits if phrase(hd(k))]
        hits = [k for k in hits if not any(h != hd(k) and phrase(hd(k))
                                           and re.search(r"(?<!\w)" + re.escape(hd(k)) + r"(?!\w)", h)
                                           for h in said_heads)]
        if not hits and rest:                         # 'table' -> 'dining table', 'test table'
            hits = [k for k in self.keys if any(t == x for t in rest.split() for x in self.head(k).split())]
        if not hits:
            hits = self._alias_hits(w)
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

    def _alias_hits(self, w: str) -> list:
        """The keys an alias of which the words ``w`` say inside a sentence ("식탁 위에 있어요": a Hangul alias
        takes particles, so it counts as a substring there; other scripts as a whole word). The longest alias said
        wins; [] when the sentence names none."""
        found = []
        for k in self.keys:
            for al in self.env[k].get("aliases") or []:
                n = words(al)
                if len(n) < 2 or n == w:
                    continue
                hit = (n in w) if re.search(r"[가-힣]", n) else bool(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", w))
                if hit:
                    found.append((len(n), k))
        if not found:
            return []
        top = max(n for n, _k in found)
        return list(dict.fromkeys(k for n, k in found if n == top))

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
            # the person's own words for the object (SAGE-v2 F4.5) and, for a message, only what to pass on (F4.6)
            "said": {"type": "string"},
            "content": {"type": "string"},
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
  Object names: lower case English ("tennis ball", "red cup"); "said": the user's own words for it, in their
  language ("빨간 컵"); for "say", "content": only the message to pass on, in the user's words.

Task: {task}{original}
{dialogue}"""


# ─────────────────────────────────────────────────────────────────────────────
# the loop
# ─────────────────────────────────────────────────────────────────────────────
class AssistLoop(WhichOne, SearchHooks, ClosedLoop):
    """See the module docstring."""

    _answer_source = "robot"          # where the last answer came from (interact.dialog: robot | typed | '')
    _verify_with_body = True          # F6.1: a successful Handover empties the hand in the post-step verify

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
            "search": search_belief.SearchConfig.from_env(overrides, miss_default=self._manifest_planner("detector_miss_rate", 0.1)),
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
            # the core safety layer (core/safety): core | pack (the robot pack's manifest rules only) | off
            "hazard_rules": str(overrides.get("hazard_rules", env("ROBOT_AGENT_HAZARD_RULES", "core"))),
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
        }

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
        log = self.__dict__.setdefault("lab_log", [])
        if len(log) < 50:
            log.append({"flag": flag, **info})

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

    def _understand(self, task, places, memory, world, dialogue, budget=None, sample: int = 0, note: str = "") -> dict:
        """The understand call (understand.ask_json: one repair retry on a non-JSON answer). Raises
        LLMUnavailable / LLMBudgetExceeded; a model that still answers no JSON gives no goals (``unparsed``)."""
        # the language layer (input_pivot): the task and the user's answers as the model reads them
        task_text, pivot_orig, dlines = self.language.understand_view(
            task, dialogue, lambda t, en: f"Robot: {t if en else self._heard_as(t)}")
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
                      + "\n".join(dlines))
            if dialogue else "", task=task_text,
            # the user's own words: a translation loses names (식탁 'dining table' came out as 'table')
            original=(f"\n(the user's own words: {self.original})" if self.original and self.original != task else "")
            + pivot_orig + (f"\n{note}" if note else ""))
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

    # "doors" are room doors: an appliance's / cabinet's door is the place itself, which Open covers (a model
    # refused "open the microwave door" as "cannot open doors")
    def _heard_as(self, text) -> str:
        """A robot line of the dialogue as the person heard it (``_ask_io`` records it; the English text when
        the run is in English or the line was not said through the robot's Ask skill)."""
        return str((getattr(self, "_said_as", None) or {}).get(str(text), text))

    _CANNOT = ("It cannot: throw, pour, cook, wash, climb, open windows or room doors, use tools, handle knives near "
               "people, or leave the house.")
    _CAN_OPEN = " (Opening a place listed below, or its door, is fine.)"

    def _cannot_line(self) -> str:
        """The prompt's "It cannot" line: the robot's i18n ``cannot_list`` (else the core's), with the manifest's
        payload / size limits when it declares them (SAGE-v2 F4.1)."""
        robot = self.robot
        line = robot.text("cannot_list") if robot.has_text("cannot_list") else self._CANNOT
        if "Open" in (robot.plan_actions or ()) and not robot.has_text("cannot_list"):
            line += self._CAN_OPEN
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
            if u.get("unparsed") or len(content) < 3 or nothing:
                return "repeat"
            # F4.2b: a goal-less refusal that comes with the model's own question ("Do you want me to check the
            # microwave?") is a guess about a request it did not get (a misheard word read as an impossible
            # action): the person says it again once; refused again after that, the refusal stands
            unsure = bool(str(u.get("question") or "").strip())
            return "repeat" if unsure and not r.dialogue and not getattr(r, "repeat_asked", False) else None
        if self._cannot_do(r.goals) or self._over_limits(r.goals):
            return None
        return "override" if rt in ("", "missing_skill", "other") else None

    def _hazard_gate(self, r):
        """Goals the robot's safety rules forbid (core/safety, option ``hazard_rules`` core | pack | off): all of
        them -> a typed safety refusal (_STOP); some -> those are dropped and said at the end
        (``r.refused_parts``); None."""
        from ..safety import check_goal
        mode = str(self.cfg.get("hazard_rules", "core"))
        pack = (getattr(self.robot.manifest, "safety", None) or {}).get("hazards") or []
        bad = [(g, why) for g in r.goals for why in [check_goal(g, r.places, pack, mode)] if why]
        if not bad:
            return None
        yield r.ev("understand_check", issues=[f"unsafe: {goal_kinds.describe(g)} ({w})" for g, w in bad], hazard=True)
        support = self._support_goals(r.goals, [g for g, _w in bad])
        if len(bad) + len(support) == len(r.goals):
            g, why = bad[0]
            yield self._finish(r, "refused", refusal="safety", msg=f"unsafe: {goal_kinds.describe(g)}: {why}",
                               say=f"I won't do that: {why}.")
            return _STOP
        r.refused_parts = list(getattr(r, "refused_parts", [])) + bad
        r.goals = [g for g in r.goals if all(g is not b for b, _w in bad) and all(g is not x for x in support)]
        return None

    @staticmethod
    def _support_goals(goals, refused) -> list:
        """The open / close goals that only served the refused goals (the container a refused put goes into or
        comes from) and that no kept goal uses: refused with them. Else the person would hear "I will open the
        fridge" for a request that was a put, say no, and never reach a refusal."""
        kept = [g for g in goals if all(g is not b for b in refused)]
        uses = lambda k: any(h.get("type") in ("on", "in") and k in (h.get("to"), h.get("from")) for h in kept)
        return [g for g in kept if g.get("type") in ("open", "close") and not uses(g.get("object"))
                and any(g.get("object") in (b.get("to"), b.get("from")) for b in refused)]

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
            g["user_at"] = to
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
        # only an answer to "Where are you?" tells where the person is (an answer to another question, the request
        # said again, names places of the task)
        at = persons.place_in_answer(places, latest) if latest and asked else None
        known = self.__dict__.setdefault("_person_at", {})
        if at is None:
            at = known.get(to)             # said earlier (as the answer to the question): a later answer, or a
            #                                different question in between, does not forget it
        else:
            known[to] = at                 # only an answer to the asked question gets here
        if at is None:
            self._where_asked = to
            if (places.env.get(to) or {}).get("speaker") or to == persons.speaker_key(places):
                return speech.LocalText("q_where_are_you")
            return speech.LocalText("q_where_is_person", {"person": to},
                                    text=f"Where is {self._say_name(places, to)}?")
        spot = persons.surface_near(places, at, float(self.cfg.get("person_surface_m", 1.2)) if hasattr(self, "cfg") else 1.2)
        g["user_at"] = at                 # where they said they are: covered by this goal (no "You mentioned ...")
        if spot:
            g["to"], g["near_person"] = spot, to
        else:
            g["hand"] = True
        return None

    @staticmethod
    def _say_name(places, key, lang="en") -> str:
        """How the robot names ``key`` to a person (F5.1): an alias in the user's script, else the key's head
        in words with its room; a person by their name."""
        from . import language, speech
        return speech.say_name(places, key, lang)

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

    def _in_lang(self, text, lang) -> str:
        """``text`` as the user hears it: the ONE speaking function (``LanguageAdapter.speak``)."""
        return self.language.speak(text, lang)

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
        # a question in the model's own words may name raw ENV keys ("... in fridge@kitchen?"): said as names (F5.1)
        said = self._in_lang(self._humanize(question), lang)
        # what the person heard, for the understanding's dialogue (F5.5: a Korean user corrected a read-back the
        # model only saw in English, and the model kept the object the person had just rejected)
        self.__dict__.setdefault("_said_as", {})[str(question)] = said
        params = {(spec.required_params[:1] or ["inputs"])[0]: said}
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
        o = g.get("said") or g.get("name_local") or obj          # name_local: the object's name in the user's
        if t == "on" and places is not None and persons.is_person(places, g.get("to")):   # language (F4.8)
            you = bool((places.env.get(g["to"]) or {}).get("speaker"))
            key = ("rb_hand_you" if you else "rb_hand_person") if g.get("hand") else ("rb_to_you" if you else "rb_to_person")
            return L(key, {"object": o, "person": g["to"]}, text=_fmt_en(key, object=obj, person=nice(g["to"])))
        if t == "on" and g.get("near_person") and places is not None:
            you = bool((places.env.get(g["near_person"]) or {}).get("speaker"))
            key = "rb_on_near_you" if you else "rb_on_near_person"
            return L(key, {"object": o, "place": g["to"], "person": g["near_person"]},
                     text=_fmt_en(key, object=obj, place=nice(g["to"]), person=nice(g["near_person"])))
        if t == "on":
            if g.get("from") and not (places is not None and persons.is_person(places, g["from"])):
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
            if who:
                return L("rb_say_at", {"place": who, "text": obj}, text=f"say \"{obj}\" in the {nice(who)}")
            if g.get("person"):
                return L("rb_say", {"person": g["person"], "text": obj}, text=f"tell {g['person']}: \"{obj}\"")
            return L("rb_say_here", {"text": obj}, text=f"say \"{obj}\"")
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
            if getattr(self, "cfg", {}).get("gate_strict"):           # round 3: a token subset of exactly one goal object (any length)
                bound = exec_integrity.bind_subset(st["object"], [g.get("object") for g in goals],
                                                   str(self._manifest_planner("label_tokens", "words")))
                if bound != st["object"]:
                    self._lab("gate_strict", part="bind_subset", frm=st["object"], to=bound)
                st["object"] = bound
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
                # the last go goal holds where the plan's steps end (not where it once passed, or started); the goal
                # checks' own looks do not count (_observe_goals goes back there)
                if not goal_kinds.reached(g, list(done), at, last=True) and world.arrived != g.get("to"):
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
            p["inputs"] = self._query_of(st, o)          # the instance the person chose (which_one), else the name
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
    def run_blocking(self, task: str, lang: str = "en", plan_only: bool = False, goals=None) -> Iterator[dict]:
        r = self._open_run(task, lang, plan_only, "sage+assist")
        r.given_goals = [dict(g) for g in goals if isinstance(g, dict)] if self.cfg.get("accept_goals") and goals else None
        fixed = self._reconcile(r, "start")              # adapter.reconcile + the declared sensors
        from robot_agent.skill_configs import ENV
        r.places = Places(dict(ENV), openable=r.robot.openable)
        self._env_places = r.places
        r.verifier = self._verifier(r.node, r.places.env)
        r.memory = Memory(self.cfg["memory_path"])
        r.llm_budget, self._llm_unreachable = LLMBudget(self.cfg["llm_budget"]), False
        self._person_at = {}                          # {person: place they said they are at} (this run)
        self._where_asked = None                      # the person "Where are you?" was asked about (this run)
        self._handed = set()                          # objects this run handed over (release actions)
        self.lab_log = []                             # what the opt-in lab options did this run (see _lab)
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

    # ── 1. understand + clarify ───────────────────────────────────────────
    def _understand_phase(self, r):
        """Goals from the task and the user's answers. True when the run ended."""
        r.dialogue, r.asked, r.confirms, r.pin_to, r.goals = [], 0, 0, None, []
        r.heard = 0                      # answers heard through the robot's I/O (a typed one cannot be misheard)
        r.readings, r.rejected, r.patched, r.seen_sigs, r.refunds, r.part_asked = [], [], None, set(), 0, False
        r.same_used = r.allow_repeat = False
        r.converge = self._converge()
        if getattr(r, "given_goals", None):
            return (yield from self._take_given_goals(r))
        while True:
            out = yield from self._understand_round(r)
            if out is _STOP:
                return True
            if out is _DONE:
                break
        r.run.obs = "goals: " + json.dumps(r.goals, ensure_ascii=False)
        return False

    def _take_given_goals(self, r):
        """Option ``accept_goals``: the caller's goals replace the understand step (no LLM call, no question, no read-back,
        no refusal). They are grounded on ENV (a place name to its key, the speaker, the remembered source) and not
        filtered. A question the grounding would have asked is only recorded in the event. False: go on to planning."""
        r.filtered = []
        r.goals, q = self._ground_goals(r.given_goals, r.places, r.memory, " ".join([r.task, self.original or ""]))
        yield r.ev("understand", status="clear", reason="goals given by the caller", goals=r.goals,
                   question=None, **({"unasked": str(q)} if q else {}))
        r.run.obs = "goals: " + json.dumps(r.goals, ensure_ascii=False)
        return False

    def _understand_round(self, r):
        """One understanding of the task: _DONE (goals settled), _NEXT (asked something) or _STOP."""
        yield r.ev("status", msg="Understanding the task...")
        bad = self._distrusted(r)
        if bad:                           # the language layer: a translation that cannot be trusted is asked again
            r.pivot_asked = True
            yield r.ev("understand_check", issues=[f"translation not trusted: {bad}"], repeat=True)
            r.repeat_asked = True
            return (yield from self._ask_clarify(r, speech.LocalText("q_repeat")))
        took = yield from self._take_understanding(r)
        if took is _STOP:
            return _STOP
        u, q = took
        self._progress(r)
        yield r.ev("understand", status=u["status"], reason=u.get("reason"), goals=r.goals,
                   question=u.get("question") or q, **({"filtered": r.filtered} if r.filtered else {}))
        if u["status"] == "infeasible" and r.dialogue and r.goals:
            u["status"] = "clear"                # the user already answered: a 7B model called a plain
                                                 # move "infeasible" after "no, the shelf in the restroom"
        # a refusal only for a typed reason (understand.typed_refusal): a goal needs an action this robot has
        # no skill for, or the model's unsafe / infeasible on words that are not a misheard answer
        heard = bool(r.dialogue) and r.dialogue[-1][0] == "user" and self._answer_source == "robot"
        if heard and getattr(r, "repeat_asked", False) and len(r.dialogue) == 2:
            heard = False            # the request said again after "say it again": a refusal of it is a refusal
        ref = typed_refusal(u["status"], u.get("reason"), self._cannot_do(r.goals),
                            " ".join([r.task, self.original or ""] + [t for w_, t in r.dialogue if w_ == "user"]), heard)
        if ref is not None and ref.kind == "capability" and not ref.missing and u["status"] == "infeasible":
            how = self._capability_check(r, u)
            if how == "repeat":                          # F4.2: not heard / not a request: say it again
                yield r.ev("understand_check", issues=["no task heard"], repeat=True)
                r.repeat_asked = True
                return (yield from self._ask_clarify(r, speech.LocalText("q_repeat")))
            if how == "override":                        # F4.1: the contract covers it; the person confirms
                yield r.ev("understand_check", issues=[f"capability refusal overridden: {u.get('reason')}"],
                           override=True)
                ref, r.force_readback = None, True
                if (yield from self._complete_override(r)) is _STOP:
                    return _STOP
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
        out = yield from self._hazard_gate(r)                      # F4.3: the robot's own safety rules
        if out is _STOP:
            return _STOP
        r.goals, conflict = goal_kinds.conflicts(r.goals, name=lambda k: speech.say_name(r.places, k, "en"))
        if conflict:
            q = conflict if not q else f"{conflict} {q}"
        # the grounding decides about places; the model's "ambiguous" counts only when it found nothing
        # to do (after "no, the shelf in the restroom" a 7B model kept asking which shelf)
        if u["status"] == "ambiguous" and u.get("question") and not r.goals:
            q = u["question"] if not q else f"{u['question']} {q}"
        if not r.goals and not q:
            q = u.get("question") or (speech.LocalText("q_unclear_task") if u.get("unparsed")
                                      else speech.LocalText("q_what_exactly"))
        if not q:
            q = yield from self._goal_readback(r)
            if q is _STOP:
                return _STOP
        if not q:
            out = yield from self._refused_reading(r)
            if out is not None:
                return out
        if not q:
            out, q = yield from self._read_back(r)
            if out is not None:
                return out
        return (yield from self._ask_clarify(r, q))

    def _take_understanding(self, r):
        """(understanding, question) of the task: the goals a correction edited (no new understanding, R4b item 2)
        or the model's, grounded. _STOP when the language model failed."""
        patched = getattr(r, "patched", None)
        r.patched = None
        if patched is not None:
            r.goals, r.filtered = patched, []
            return {"status": "clear", "reason": "goals edited by the person's correction", "goals": patched,
                    "question": None}, None
        r.allow_repeat = False
        try:
            u = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget)
        except (LLMUnavailable, LLMBudgetExceeded) as e:
            yield self._llm_failed(r, e); return _STOP
        if self.cfg.get("reask_abstain") and not getattr(r, "reasked", False):
            u = yield from self._reask_abstention(r, u)
            if u is _STOP:
                return _STOP
        r.goals, q = self._ground_understood(r, u)
        return u, q

    _FIND_NOTE = ("Note: this robot finds objects itself ({find}). Where an object is now is not a reason for "
                  "giving no goals: give the goals for ALL of the task and leave `from` empty for an object whose "
                  "place the person did not name.")

    def _reask_abstention(self, r, u):
        """Option ``reask_abstain``: a reading with no goals whose own question asks where an object is (the robot
        searches, it does not need to be told) is understood once more with that stated. The new reading replaces the
        first when it has goals, covers the objects the question named and is not ``unsafe``; else today's question
        stays. One extra call, once per run, only for a robot with a Find skill. _STOP when the LLM failed."""
        finds = sorted(a for a in self.robot.plan_actions if a.lower() == "find")
        objs = goal_fix.abstains_on_location(u) if finds else None
        if objs is None:
            return u
        r.reasked = True
        try:
            u2 = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget,
                                  note=self._FIND_NOTE.format(find=", ".join(finds)))
        except LLMBudgetExceeded:
            return u
        except LLMUnavailable as e:
            yield self._llm_failed(r, e); return _STOP
        took = bool(u2["goals"]) and u2["status"] != "unsafe" and goal_fix.covers_objects(u2["goals"], objs)
        self._lab("reask_abstain", taken=took)
        yield r.ev("understand_check", issues=["abstained on where an object is: " + "; ".join(objs or ["?"])],
                   reask=True, reask_taken=took)
        return u2 if took else u

    def _refused_reading(self, r):
        """The goals equal a reading the person refused in a read-back: reading it back again only gets the same
        "no". Ask once which part is wrong (a choice); a second time stop. None = read it back as usual."""
        repeat_ok, r.allow_repeat = getattr(r, "allow_repeat", False), False
        if repeat_ok or not self._converge() or not r.goals \
                or dialog_converge.goal_sig(r.goals) not in getattr(r, "rejected", ()):
            return None
        if not getattr(r, "part_asked", False):
            r.part_asked = True
            yield r.ev("understand_check", issues=["a refused reading again"], correction="repeat")
            return (yield from self._ask_clarify(r, speech.LocalText("q_which_part")))
        yield self._finish(r, "failed", msg="the user did not confirm the task"); return _STOP

    def _progress(self, r) -> None:
        """A question whose answer gave a goal set not seen before has gained something: its round is given
        back to the cap (twice per run at most). The cap then counts the questions that gained nothing."""
        if not self._converge():
            return
        sig = dialog_converge.goal_sig(r.goals)
        seen = getattr(r, "seen_sigs", None)
        if seen is None:
            return
        if (r.goals and sig not in seen and r.asked > 0 and getattr(r, "refunds", 0) < 2 and r.dialogue
                and r.dialogue[-1][0] == "user"):
            r.asked -= 1
            r.refunds = getattr(r, "refunds", 0) + 1
        seen.add(sig)

    def _distrusted(self, r) -> Optional[str]:
        """Why the English of the user's latest words (the instruction, else the last answer) cannot be trusted
        (``LanguageAdapter.untrusted``); asked once per run. None for an English run or a pivot of ``none``."""
        if getattr(r, "pivot_asked", False) or (r.lang or "en")[:2] == "en" or self.language.pivot == "none":
            return None
        last = [t for w_, t in r.dialogue if w_ == "user"]
        return self.language.untrusted(last[-1] if last else r.task)

    _OVERRIDE_NOTE = ("Note: this robot has a skill for every part of this task ({skills}). How an object is moved "
                      "into or onto a place (sliding, pushing, tucking it in) does not matter: it is a put. Give the "
                      "goals for ALL of the task, with status \"clear\".")

    def _complete_override(self, r):
        """F4.1b: a capability refusal the contract overrides keeps only the goals the model did not refuse
        ("cannot slide an object into the fridge" came with open(fridge) alone, and the plate was lost). When
        the person's words name something those goals leave out, the task is understood once more with the
        robot's skills stated; its goals are taken when they leave out less. This call replaces the
        consistency samples of the read-back (r.sampled). _STOP when the LLM failed."""
        issues = [i for i in self._issues(r, r.goals) if i["kind"] in ("object", "verb", "place")]
        if not issues or self.cfg["understand_samples"] <= 0:
            return None
        r.sampled = True
        skills = ", ".join(sorted(a for a in self.robot.plan_actions if a[:1].isupper()))
        try:
            u2 = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget,
                                  note=self._OVERRIDE_NOTE.format(skills=skills))
        except LLMBudgetExceeded:
            return None
        except LLMUnavailable as e:
            yield self._llm_failed(r, e); return _STOP
        g2, q2 = self._ground_understood(r, u2)
        left = [i for i in self._issues(r, g2) if i["kind"] in ("object", "verb", "place")] if g2 else issues
        took = bool(g2) and not q2 and u2["status"] != "unsafe" and len(left) < len(issues) \
            and not self._cannot_do(g2)
        if took:
            r.goals = g2
        yield r.ev("understand_check", issues=[describe(i) for i in issues], override_completed=took,
                   goals=g2 if took else r.goals)
        return None

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
        raw, _nob = goal_fix.unsaid_speaker(raw, words_, r.places, self.robot)   # F2.5: no recipient, no hand-over
        goals, q = self._ground_goals(raw, r.places, r.memory, words_, latest=answers[-1] if answers else "")
        goals, more = goal_filter.after_grounding(goals, " ".join([r.task] + answers), r.places) if on else (goals, [])
        r.filtered += more
        said_ = " ".join([without_places(" ".join([r.task, self.original or ""]), r.places, getattr(r, "replaced", ()))]
                         + answers)
        goals, fixed = goal_fix.repair_destination(goals, said_, r.places)          # F4.7
        r.repaired = list(getattr(r, "repaired", [])) + fixed
        goals, _kinds = goal_fix.kind_from_destination(goals, words_, r.places, "PutIn" in self.robot.plan_actions)
        goals, invented = goal_fix.drop_invented(goals, words_, r.places)    # F4.4
        r.filtered += invented
        goals, rq = goal_fix.relay_check(goals, r.task, r.places)           # F4.6
        goals, shared = goal_fix.shared_listener(goals, words_, r.places)    # a listener is not also the recipient
        r.filtered += shared
        goals, q = self._ground_v2(r, goals, q, words_)
        q = q or (speech.LocalText("q_what_say") if rq else None)
        if not goals and empty and not q:
            q = speech.LocalText("q_what_bring")
        return goals, q

    def _ground_v2(self, r, goals, q, words_):
        """Solution-lab round 2, grounding chain v2 (options ``go_steps``, ``kind_anchor``, ``name_canon``; each default
        OFF, all off = no change): deterministic fixes of the grounded goals, no LLM call. Returns (goals, question)."""
        c = self.cfg
        if c.get("go_steps"):
            goals, gone = goal_fix.go_steps(goals, r.task, r.places)
            if gone:
                self._lab("go_steps", dropped=len(gone))
            r.filtered += gone
        if c.get("kind_anchor"):
            goals, gone = goal_fix.kind_anchor_open(goals, words_, r.places, self.robot)
            if gone:
                self._lab("kind_anchor", dropped=len(gone))
            r.filtered += gone
        if c.get("name_canon") or c.get("inventory_ask"):
            before = [g.get("object") for g in goals]
            goals, which, _notes = self._canon(r, goals)
            after = [g.get("object") for g in goals]
            if which or before != after:
                self._lab("inventory_ask" if self.cfg.get("inventory_ask") else "name_canon",
                          renamed=sum(a != b for a, b in zip(before, after)), asked_which=bool(which))
            for g in goals:                                   # a renamed object: the sighting names where it is
                if g.get("said") and g.get("type") in ("on", "in") and not g.get("from") and not g.get("from_any"):
                    g["from"] = self._remembered(r.memory, g["object"]) or ""
                    if g["from"] and g["from"] == g.get("to"):
                        g["from"] = ""
            if which and not q:
                said, names = which
                text = " or ".join(f"the {n}" for n in names)
                q = speech.LocalText("q_which_known", {"object": said, "text": text},
                                     text=f"I know of more than one {said}: {text}. Which one do you mean?")
        return goals, q

    def _canon(self, r, goals) -> tuple:
        """name_canon (English labels, place groups) or, with ``inventory_ask``, its general form: the pack's
        ``planner.canonical_lang`` and ``plural_fold`` data, the user's words first, then one closed which-question."""
        mem = r.memory.data.get("objects")
        if not self.cfg.get("inventory_ask"):
            return goal_fix.canon_names(goals, mem, r.places)
        lang = str(self._manifest_planner("canonical_lang", "en")).lower()
        eng = ""
        if (getattr(r, "lang", None) or "en")[:2] == "en":
            eng = " ".join([r.task] + [t for w_, t in r.dialogue if w_ == "user"])
        else:                                          # only a reading the language layer already holds (no new call)
            heard = getattr(self.language, "heard", {}).get(r.task)
            eng = heard.english if heard is not None and heard.trusted else ""
        return goal_fix.canon_inventory(goals, mem, r.places, lang, goal_fix.fold_rules(self.robot, lang), eng)

    def _issues(self, r, goals) -> list:
        """understand.find_issues of `goals` against everything the person said (task, own words, answers)."""
        if not self.cfg["goal_readback"]:
            return []
        answers = [t for w_, t in r.dialogue if w_ == "user"]
        # a place the person replaced in a read-back correction no longer counts as asked for (docs/dialog.md)
        said = without_places(" ".join([r.task, self.original or ""]), r.places, getattr(r, "replaced", ()))
        goals = list(goals) + [g for g, _w in getattr(r, "refused_parts", [])]   # refused for safety: covered
        issues = find_issues(goals, " ".join([said] + answers), r.places,
                             english=self._object_source(r, answers), user_words=_USER_WORDS)   # objects: own words only
        if not answers and verb_lost(r.task):             # the request's verb was lost: the goals are a guess
            issues = [{"kind": "noverb", "what": ""}] + issues
        extra = goal_fix.ko_object_issues(goals, r.task)                                          # F4.5
        if (getattr(r, "lang", None) or "en")[:2] != "en":         # a goal set that only goes somewhere: the English reading's objects
            h = self.language.hear(r.task)
            extra += vacuous_goal_issues(goals, h.english if h.trusted else "", r.places, _USER_WORDS)
        return issues + [i for i in extra if i not in issues]

    def _object_source(self, r, answers) -> str:
        """The words the object-coverage check reads: the task's own words, unless a later answer said the whole
        request again (ASR garbled the first one): then that answer replaces it, so a garbled noun of the first
        hearing is not an open issue once the restatement was understood."""
        for a in reversed(answers):
            if self._restates(r, a):
                return a
        return r.task

    def _goal_readback(self, r):
        """The goals read back against the person's words before planning: a model that dropped or changed a
        goal must not plan (and end in a success against its own goals). On an issue the understanding is
        sampled `understand_samples` more times: an issue-free goal set the samples agree on is taken
        (CLARA-style consistency). Else the person is asked an open question the first time (returned), and
        later the read-back names the doubt (r.unsure). _STOP when an LLM call failed."""
        issues = self._issues(r, r.goals)
        r.unsure = [describe(i) for i in issues]
        if self._unverified_objects(r):
            r.force_readback = True                                                    # F4.8: read it back
        if issues and not getattr(r, "sampled", False) and self.cfg["understand_samples"] > 0:
            r.sampled = True
            agreed = yield from self._resample(r, r.unsure)
            if agreed is _STOP:
                return _STOP
            if agreed is not None:
                from ..safety import check_goal
                pack = (getattr(self.robot.manifest, "safety", None) or {}).get("hazards") or []
                mode = str(self.cfg.get("hazard_rules", "core"))
                keep = [g for g in agreed if not check_goal(g, r.places, pack, mode)]
                r.goals, r.unsure = (keep or r.goals), []          # a re-sample never brings back a refused goal
                return None
        if not issues or getattr(r, "issue_asked", False):
            return None
        r.issue_asked = True
        return issue_question(issues, r.places) or None

    _NAMES_PROMPT = ("Give the usual Korean name of each household object below, one per line, in the same order, "
                     "as a plain noun in Hangul (no numbering, nothing else):")

    def _unverified_objects(self, r) -> list:
        """F4.8: the English object names of a Korean request that nothing the person said supports. The model
        names objects in English (and leaves ``said`` empty), so a misheard noun ("회색 수이건") came out as an
        invented object ("gray sock") that no check saw. The translator gives each object's usual Korean name
        once (one call for all, cached, the translator's own budget); an object whose head noun is not in the
        person's words is read back before planning (r.unsure). No lexicon; no call for English requests."""
        if (r.lang or "en")[:2] != "ko" or str(self.cfg.get("object_check", "1")) in ("0", "false", "False"):
            return []
        hangul = re.compile(r"[\uac00-\ud7a3]")
        objs = list(dict.fromkeys(str(g.get("object")) for g in r.goals if g.get("type") in ("on", "in")
                                  and g.get("object") and not g.get("said") and not hangul.search(str(g["object"]))))
        if not objs or not hangul.search(r.task or ""):
            return []
        heard = re.sub(r"\s+", "", " ".join([r.task, self.original or ""] + [t for w_, t in r.dialogue if w_ == "user"]))
        names = self._local_names(objs)
        for g in r.goals:                    # the read-back names the object in the user's language: no call
            if names.get(g.get("object")) and not g.get("said"):
                g["name_local"] = names[g["object"]]
        out = []
        for o in objs:
            toks = re.findall(r"[\uac00-\ud7a3]+", names.get(o) or "")
            while len(toks) > 1 and len(toks[-1]) < 2:
                toks.pop()                                  # a one-syllable packaging noun ("우유 팩"): the noun before
            if toks and toks[-1] not in heard:
                out.append(o)
        return out

    def _local_names(self, objs) -> dict:
        """{object: its Korean name} from the translator (one call, cached; {} when it fails or is over budget)."""
        cache = self.__dict__.setdefault("_tr_cache", {})
        key = ("names", tuple(objs), str(self.cfg.get("translate_model") or ""))
        if key not in cache:
            out = {}
            try:
                if not self.language._take():
                    raise LLMBudgetExceeded("translate budget")
                txt = str(self._translator().chat(prompt=self._NAMES_PROMPT + "\n" + "\n".join(objs), temperature=0,
                                                  seed=0))
                lines = [re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", x).strip() for x in txt.splitlines() if x.strip()]
                lg = "ko"                                       # the Hangul names the check reads
                if len(lines) == len(objs):
                    out = {o: n for o, n in zip(objs, lines) if speech.guard(n, lg) is None}
            except Exception:
                out = {}
            cache[key] = out
        return cache[key]

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
        # two read-backs; a third only after a correction the robot has not read back yet (a misheard
        # correction made the second read-back repeat the first; fixer rerun of m5). Each reading that differs
        # from every earlier one is progress and earns one more read-back (two at most): the cap counts the
        # read-backs that gained nothing (R4b item 2).
        readings = getattr(r, "readings", [])
        cap = 2 + (min(2, max(0, len(set(readings)) - 1)) if self._converge() else 0)
        if r.confirms >= cap and not (r.confirms < 3 and getattr(r, "corrected", 0) >= r.confirms):
            yield self._finish(r, "failed", msg="the user did not confirm the task"); return _STOP, None
        r.confirms += 1
        sig = dialog_converge.goal_sig(r.goals)
        r.readings = list(readings) + [sig]
        rb = self.summary(r.goals, r.places)
        cq = speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?")
        if unsure:
            cq = speech.LocalText.join(
                speech.LocalText("rb_unsure", {"issues": speech.LocalText.seq(unsure, "sep_semi")},
                                 text=f"I am not sure I understood ({'; '.join(unsure)})."), cq)
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
        again = self._converge() and sig in getattr(r, "rejected", ())
        r.rejected = list(getattr(r, "rejected", ())) + [sig]
        if not rest:                     # a bare "no": ask what instead (as a choice when this reading was refused before)
            return None, speech.LocalText("q_which_part" if again else "q_what_do")
        r.corrected = getattr(r, "corrected", 0) + 1
        r.memory.remember_answer(r.task, cq, ans)
        edit, edits = self._correction_edit(r, rest)
        if edit == "same":               # the goal it names already has that destination
            yield r.ev("understand_check", issues=["correction restates a read-back goal"], correction="same")
            if getattr(r, "same_used", False):
                return None, speech.LocalText("q_which_part")       # twice: the fault is elsewhere, ask which part
            r.same_used = True           # once: the correction may have been misheard; read the goals back again
            r.patched, r.allow_repeat = [dict(g) for g in r.goals], True
            return _NEXT, None
        if edit == "edit":
            patched = yield from self._patch_goals(r, rest, edits)
            if patched is not None:
                r.patched = patched      # these goals are read back next; no new understanding of the task
                return _NEXT, None
        elif edit is None:
            k = r.places.ground(rest, rest)
            if k and not re.search(r"\b(from|take|pick)\b", rest):
                r.pin_to = k             # the destination it read back is replaced (the coverage check drops it)
                r.replaced = set(getattr(r, "replaced", ())) | {g["to"] for g in r.goals
                                                                if g.get("type") in ("on", "in") and g.get("to") not in (None, "", k)}
        return _NEXT, None

    def _correction_texts(self, r, rest) -> list:
        """The correction as said, and (for another language) its English."""
        out = [rest]
        if (getattr(r, "lang", None) or "en")[:2] != "en":
            h = self.language.hear(rest)
            if h.english:
                out.append(h.english)
        return out

    def _correction_edit(self, r, rest):
        """(``"edit"``, edits) | (``"same"``, []) | (None, []): what a correction after a "no" asks for
        (``dialog_converge.correction_edit``); None = the correction names no single object, the old path stands."""
        if not self._converge():
            return None, []
        kind, edits = dialog_converge.correction_edit(r.goals, self._correction_texts(r, rest), r.places)
        return (None if kind == "none" else kind), edits

    def _patch_goals(self, r, rest, edits):
        """The read-back goals with the edited ones sent to their new destination, grounded again as any goal
        (a person's hand-over, a surface beside them). None when the grounding has a question to ask. Only the
        goals the person named change; the others keep what they were understood as."""
        goals = [dict(g) for g in r.goals]
        old_to = set()
        for i, dest in edits:
            g = goals[i]
            old_to.add(g.get("to"))
            for k in ("hand", "near_person", "user_at", "from_any"):
                g.pop(k, None)
            g["to"] = "me" if dest == "@speaker" else dest
        text = " ".join([r.task] + [t for w_, t in r.dialogue if w_ == "user"])
        done, q = self._ground_goals([goals[i] for i, _d in edits], r.places, r.memory, text, latest=rest)
        if q or len(done) != len(edits):
            return None
        done, _k = goal_fix.kind_from_destination(done, text, r.places, "PutIn" in self.robot.plan_actions)
        for (i, _d), g in zip(edits, done):
            goals[i] = g
        r.replaced = set(getattr(r, "replaced", ())) | {p for p in old_to if p}
        yield r.ev("understand_check", issues=[], correction="patched", goals=[goals[i] for i, _d in edits])
        return goals

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
        q = self._fresh_question(r, q)
        if r.asked >= self.cfg["clarify_rounds"] + (1 if self._essential(q) and not getattr(r, "essential_used", False) and not self.__dict__.get("_person_at") else 0):
            # (what the run says is a plain sentence, not the internal message that holds the last question)
            yield self._finish(r, "failed", msg=f"still unclear after {r.asked} questions: {q}",
                               say=speech.LocalText("say_gave_up"))
            return _STOP
        r.asked += 1
        if not self._asks_where(q):
            self._where_asked = None     # "Where are you?" was built but another question was asked: not asked yet
        if r.asked > self.cfg["clarify_rounds"]:
            r.essential_used = True          # the one round past the cap that a delivery's "Where are you?" may take
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
        if self._restates(r, ans) and not getattr(r, "free_round_used", False):
            r.free_round_used = True     # an answer that says the whole request again was informative: not a spent round
            r.asked -= 1
        return _NEXT

    @staticmethod
    def _asks_where(q) -> bool:
        """Does ``q`` ask where a person is ("Where are you?" / "Where is X?")?"""
        if not isinstance(q, speech.LocalText):
            return False
        keys = ("q_where_are_you", "q_where_is_person")
        return q.key in keys or any(isinstance(p, speech.LocalText) and p.key in keys for p in q.parts)

    @staticmethod
    def _essential(q) -> bool:
        """Is ``q`` a question the task cannot go on without whatever else was asked (where the speaker is, for a
        delivery to them)? It may take one round past ``clarify_rounds``."""
        if not isinstance(q, speech.LocalText):
            return False
        return q.key == "q_where_are_you" or any(isinstance(p, speech.LocalText) and p.key == "q_where_are_you"
                                                 for p in q.parts)

    @staticmethod
    def _fresh_question(r, q):
        """A question already asked in this run (word for word, or the same words with other politeness) is not
        asked again: the person answered it, so the answer did not settle it. Ask them to say the task in other
        words instead (once)."""
        asked = [" ".join(str(t).lower().split()) for w_, t in r.dialogue if w_ == "robot"]
        qq = " ".join(str(q).lower().split())
        near = getattr(r, "converge", False) and any(dialog_converge.same_question(qq, a) for a in asked)
        if (qq in asked or near) and not getattr(r, "reworded", False):
            r.reworded = True
            return speech.LocalText("q_unclear_task")
        return q

    def _restates(self, r, ans) -> bool:
        """True when `ans` carries most of the words of the request the run started with (the person said it
        all again after "could you say it again?" / "what do you mean by ...?")."""
        words = lambda t: {w for w in re.findall(r"\w+", str(t).lower()) if len(w) > 2}
        base = words(self.original or r.task)
        return len(base) >= 4 and len(base & words(ans)) >= 0.6 * len(base)

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
        if self.cfg.get("person_goals"):
            r.goals = goal_kinds.dedupe(r.goals)
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
        if self.cfg.get("person_goals") and getattr(r.planner, "fixed_subgoals", False):
            return self._lab_subgoals(r)
        return [goal_kinds.subgoal_text(g, r.places) for g in r.goals]

    def _lab_subgoals(self, r) -> list:
        """``person_goals``: in a request that mixes template goals (go / say / deliver) with moves, a template goal
        is a sub-goal with its own steps (``goals.compile_steps``: MoveTo, Say with the exact sentence), so the
        planner expands the moves only and never paraphrases a sentence or invents a go. The speaker as the place
        to say it at is the speaker's ENV entry; a place the ENV does not know is not driven to. A request of
        template goals alone is compiled whole already, one of moves alone is unchanged."""
        kinds = {g.get("type") in goal_kinds.TEMPLATE_KINDS for g in r.goals}
        text = [goal_kinds.subgoal_text(g, r.places) for g in r.goals]
        if kinds != {True, False}:
            return text
        out, can_say = [], "Say" in r.robot.plan_actions
        for g, t in zip(r.goals, text):
            if g.get("type") not in goal_kinds.TEMPLATE_KINDS:
                out.append(t)
                continue
            h = dict(g)
            if goal_kinds.is_speaker(h.get("to")):
                h["to"] = persons.speaker_key(r.places) or ""
            elif h.get("to") and h["to"] not in r.places.env:
                h["to"] = ""
            steps = goal_kinds.compile_steps([h], None, r.robot.text("delivery", r.lang), can_say=can_say)
            out.append((t, steps) if steps else t)
        return out

    @staticmethod
    def _has_blocks(r) -> bool:
        from .base import subgoal_repair
        rep = subgoal_repair(r.planner)
        try:
            return bool(rep is not None and rep.blocks())
        except Exception:
            return False

    def _gate_repair(self, r) -> list:
        """The gate's symbolic repair of r.steps (plan_repair.py). Option ``gate_strict`` (round 3): when today's
        repair leaves a critical finding, repeat it from the same start with a per-goal score and keep that result
        only if it certifies the plan; otherwise today's result stands."""
        if not self.cfg.get("gate_strict"):
            return self._gate_repair_run(r)
        from .base import subgoal_repair
        rep = subgoal_repair(r.planner)
        try:
            blocks = rep.blocks() if rep is not None else None
        except Exception:
            blocks = None
        snap = lambda: (list(r.steps), [list(b.steps) for b in (blocks or [])], dict(getattr(r, "repaired_by_op", {})))  # noqa: E731
        pre = snap()
        notes = self._gate_repair_run(r)
        if not self._blocking(r, r.steps):
            return notes
        first = snap()
        self._restore_repair(r, blocks, pre)
        notes2 = self._gate_repair_run(r, per_goal=True)
        if not self._blocking(r, r.steps):
            self._lab("gate_strict", part="per_goal_repair", kept=True)
            return notes2 + ["per-goal repair certified the plan"]
        self._restore_repair(r, blocks, first)
        self._lab("gate_strict", part="per_goal_repair", kept=False)
        return notes

    @staticmethod
    def _restore_repair(r, blocks, sn) -> None:
        r.steps = list(sn[0])
        for b, st in zip(blocks or [], sn[1]):
            b.steps = list(st)
        r.repaired_by_op = dict(sn[2])

    def _gate_repair_run(self, r, per_goal: bool = False) -> list:
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
        score = lambda s: len(self._blocking(r, self._tidy(r, ground(s))[0], per_goal))  # noqa: E731
        steps, notes, counts = plan_repair.repair(
            r.steps, score, r.world, r.places, r.goals, caps=caps, actions=self.robot.plan_actions,
            release=sorted(caps.release_actions), hand_goal=lambda g: bool(g.get("hand")),
            blocks=blocks or None, join=rep.join_blocks if blocks else None, ground=ground,
            complete=str(self.cfg.get("gate_complete", "0")) in ("1", "true", "True"),
            person_goals=bool(self.cfg.get("person_goals")), reached=self._reaches)
        acc = r.__dict__.setdefault("repaired_by_op", {})
        for k, v in counts.items():
            acc[k] = acc.get(k, 0) + v
        if notes:
            new = ground(rep.join_blocks(blocks)) if blocks else steps
            r.steps = self._tidy(r, new)[0]
        return notes

    def _repair_suffix(self, r, suffix) -> list:
        """F1.3c: the gate's symbolic repair (plan_repair.py) of a replan's suffix too, from the belief now: a
        suffix that opens a container with the hand full ran as the planner wrote it (the gate only repaired the
        first plan). ``finish``: a suffix that dropped a goal still to do gets that goal's delivery back. An edit is
        kept only when the suffix's critical findings drop; no LLM call."""
        from . import plan_repair
        if not suffix:
            return suffix
        ground = lambda s: self._ground_all(r, s)                                        # noqa: E731
        score = lambda s: len(self._blocking(r, self._tidy(r, ground(s))[0]))           # noqa: E731
        if score(suffix) == 0:
            return suffix
        caps = self.robot.model_capabilities()
        steps, notes, counts = plan_repair.repair(
            suffix, score, r.world, r.places, r.goals, caps=caps, actions=self.robot.plan_actions,
            release=sorted(caps.release_actions), hand_goal=lambda g: bool(g.get("hand")),
            only=("hoist", "dedupe", "action", "unclose", "finish"), reached=self._reaches)
        if not notes:
            return suffix
        acc = r.__dict__.setdefault("repaired_by_op", {})
        for k, v in counts.items():
            acc[k] = acc.get(k, 0) + v
        return self._tidy(r, ground(steps))[0]

    def _tidy(self, r, steps) -> tuple:
        """plan_tidy.tidy of `steps` on the belief now: (steps, notes); `plan_tidy` off: unchanged."""
        if not self.cfg["plan_tidy"] or not steps:
            return steps, []
        ctx = dict(capabilities=self.robot.model_capabilities(), actions=self.robot.plan_actions,
                   openable=r.places.openable, containers=r.places.openable)
        return plan_tidy.tidy(steps, r.world, r.goals, self._reaches, ctx)

    def _blocking(self, r, steps, per_goal: bool = False) -> list:
        """The findings that keep a plan from running (the certified gate): the checker's critical
        ones on the plan as written, and steps the robot cannot bind. ``per_goal`` (round 3, the repair's score
        only): the joined 'does not achieve' finding counts once per unmet goal."""
        unt = self._untouched(r.goals, steps)
        goals = [g for g in r.goals if g not in unt] if unt and len(unt) < len(r.goals) else r.goals
        probs = [p_[1] for p_ in self._check(steps, r.world, r.places, goals) if p_[2]] if steps else []
        out = list(dict.fromkeys(probs + self._hard_findings(r, steps)))
        if per_goal and any(f.startswith(exec_integrity.MISSING_PREFIX) for f in out):
            out = exec_integrity.per_goal_findings(out, len(self._missing(steps, r.world, goals)))
        return out

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
        suffix = self._repair_suffix(r, suffix) if r.replan_error is None else suffix
        bad = self._hard_findings(r, suffix) if r.replan_error is None else []
        if bad and r.replans < self.cfg["max_replans"]:
            r.replans += 1                            # one refinement with the gate's findings
            suffix, ers = self._replan_suffix(r, failed, ers + "; the new plan was rejected: " + "; ".join(bad))
            suffix = self._repair_suffix(r, suffix) if r.replan_error is None else suffix
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
            ans = self._ask(q, r.node, self._log_fn(r.i), r.lang)          # silence: after the current task
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
        r.queries = self._run_queries = {}            # object -> the description the person chose (which_one)
        r.refinds, r.found_at, r.relooked = {}, {}, set()
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
        last = r.goals[-1] if r.goals else {}
        if last.get("type") == "go" and last.get("to") and r.world.arrived != last["to"]:
            if self.cfg.get("endstate_check"):                          # round 3: a checked step, not a hope
                unmet += self._return_to_end(r, last["to"])
            else:
                self._look(r, {"action": "MoveTo", "object": last["to"]})    # back where the task ends ("go back and charge")
        return unmet

    def _return_to_end(self, r, place, tries: int = 3) -> list:
        """Option ``endstate_check``: the check tour's return to the task's last ``go`` place, retried; if it still
        fails the goal is unmet (one failure entry, coded by the adapter) and the run does not say done."""
        res = None
        for n in range(tries):
            res, _sk = self._look(r, {"action": "MoveTo", "object": place})
            if res is not None and res.ok:
                self._lab("endstate_check", place=place, ok=True, tries=n + 1)
                return []
        if res is None:
            return []                                                   # no MoveTo skill: nothing to check with
        self._lab("endstate_check", place=place, ok=False, tries=tries)
        code = r.robot.failure_code(res, res.msg)
        return [{"code": code.value, "device": res.device,
                 "evidence": {"goal": f"go {place}", "observed": f"could not get back to {place}: {res.msg}"}}]

    def _observe_goal(self, r, g):
        """Look at one goal: (True | False | None = cannot tell, evidence text, device)."""
        if g.get("type") not in ("on", "in"):
            return None, f"{g.get('type')} {g.get('object')}: no sensor declared for it (belief)", None
        o, place = g["object"], g.get("to") or ""
        nice = place.replace("@", " in the ")
        if o in getattr(self, "_handed", set()) and r.placed.get(o) == place:
            # F6.2: handed over by a release action of the contract (a Handover) that succeeded: the person holds
            # it, so a look at their spot cannot see it there; the hand-over's own success is the evidence
            return True, f"the {o} was handed over at the {nice} (the hand-over succeeded)", None
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
        if code is FailureCode.ABSENT and r.placed.get(o) == place:
            # a step of the plan put it here and the release was verified: one miss of the camera is weak evidence
            # against that, so the check looks a second time before it says the goal does not hold
            again, skill2 = self._look(r, {"action": "Find", "object": o})
            if again is not None and again.ok:
                r.memory.see(o, place, "seen at its goal place")
                return True, f"the {o} is seen on the {nice} (second look)", skill2
            res, skill = (again, skill2) if again is not None else (res, skill)
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
                if self._refind_dropped(r, st, r.robot.policy.rule_for(FailureCode.DROPPED)):
                    return _NEXT                        # the contract's DROPPED rule: find it again, pick it up
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
        if (a in ("Place", "PutIn") or a in self.robot.model_capabilities().release_actions) and vres \
                and vres[0].ok and any(v.layer == "sensor" and not v.ok for v in vres) \
                and all(v.ok for v in vres if v.layer != "sensor") and r.world.holding \
                and not self._holding(r.node, default=True) and not self._holding(r.node, default=True):
            # the release reported success and only the gripper check read "full"; two more readings read empty:
            # two of three, a noisy sensor and not a failed release
            vres = [v for v in vres if v.layer != "sensor"] + [
                VerifyResult("sensor", True, f"{skill}: two second readings, gripper empty", unknown=False)]
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
        if a == "Find":
            yield from self._which_one(r, st, rec)      # two objects fit the words: which one?
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
        if self.cfg.get("reset_container") and a == "PutIn":
            out = yield from self._reset_container(r, st, res, reason)
            if out is not None:
                return out
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
        # only a sensor check disagrees with the skill's own success (the layers before it passed)
        r.failed_layers = {v.layer for v in rec.verifies if not v.ok}
        r.sensor_only = bool(rec.verifies) and all(v.ok for v in rec.verifies if v.layer != "sensor") \
            and any(v.layer == "sensor" and not v.ok for v in rec.verifies)
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

    def _reset_container(self, r, st, res, reason):
        """Option ``reset_container``: a PutIn that fails BLOCKED into a container the belief holds open, once per
        container (and only for a robot whose action model has Close and Open): put the held object down where the
        robot's own completed steps picked it, Close and Open the container, fetch it again, PutIn again. _NEXT or None."""
        c, held = st["object"], r.world.holding
        done = r.__dict__.setdefault("reset_done", set())
        code = r.robot.failure_code(res, reason)
        if (code is not FailureCode.BLOCKED or not held or c in done or c not in r.world.opened
                or not {"Close", "Open"} <= set(self.robot.plan_actions)):
            return None
        src = exec_integrity.pick_source(r.completed, c, r.places.openable)
        if not src:
            return None
        done.add(c)
        self._note_failure(r, st, code, res.device, reason)
        r.steps[r.i:r.i] = exec_integrity.reset_steps(c, held, src)
        self._lab("reset_container", container=c, held=held, source=src)
        yield r.ev("recover", at_index=r.i, kind="reset_container", msg=f"{c}: put {held} down at {src}, close, open, again")
        return _NEXT

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
            # the person hears why in words (no failure code, no skill name: a Dock runs the move skill)
            yield self._finish(r, "failed", code=code, device=device, evidence=ev_,
                               msg=f"the robot is not healthy after {st.get('action')} {st.get('object')} failed "
                                   f"({skill}: {code.value}): {health.get('error') or health}",
                               say="I have to stop: something is wrong with my hardware.")
            return _STOP
        if todo is Recovery.HEALTH_CHECK:
            return None
        if st.get("action") == "Find":
            r.world.found = None
        yield r.ev("recover", at_index=r.i, kind="wait_health", code=code.value,
                   msg=f"{skill} could not run ({code.value}); waited for the robot, trying again")
        return _NEXT                                      # the same step again, in place (r.i unchanged)

    def _wait_on_robot(self, r):
        """Option ``wait_on_robot``: the pack's declared wait action (manifest planner.recovery.wait) runs once per
        schedule entry, health() read after each; None when the pack declares none (today's poll then)."""
        act, sched = exec_integrity.wait_schedule((self._manifest_planner("recovery", None) or {}).get("wait"))
        if not act:
            return None
        try:
            if self._bind({"action": act, "object": f"{sched[0]:g}"}, r.world) is None:
                return None
        except Unbindable:
            return None
        h = r.robot.health(r.node)
        waited = []
        for secs in sched:
            if h.get("ok") is True:
                break
            skill, params = self._bind({"action": act, "object": f"{secs:g}"}, r.world)
            run_skill(self.sr, skill, params, r.node, log_fn=self._log_fn(r.i))
            waited.append(secs)
            h = r.robot.health(r.node)
        self._lab("wait_on_robot", action=act, schedule_s=sched, waited_s=waited, health_ok=h.get("ok"))
        return h

    def _wait_health(self, r, wait_s):
        """The adapter's health(), polled up to `wait_s` until it reads ok (a cancel ends the wait)."""
        from ..run_control import cancel_requested
        if self.cfg.get("wait_on_robot"):
            waited = self._wait_on_robot(r)
            if waited is not None:
                yield r.ev("health", health=waited)
                return waited
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
        if code == FailureCode.ABSENT.value and here and r.found_at.get(o) == here and (o, here) not in r.relooked:
            # the object was lost right here earlier in this run (a failed grasp, or it fell): one miss here is weak
            # evidence that it is gone, so look once more before the place counts as searched
            r.relooked.add((o, here))
            yield r.ev("recover", at_index=r.i, kind="relook", msg=f"the {o} was seen here: looking again")
            return _NEXT, ""
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
            ans, _n = self._ask_heard(q, r.node, self._log_fn(r.i), r.lang)   # silence: asked again (F3.8)
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
        looks = len(r.searched) + len((getattr(r, "second_looks", {}) or {}).get(o, []))
        total = int(self.cfg.get("search_total", 8))
        if self.cfg.get("search_legs") and not nowhere and (
                self._second_look(r, o, only_if_beats=True) if looks < total - self._relook_budget(r) else
                self._second_look(r, o)):
            # the last looks of the search go to the most likely places again, before the unlikely rest (F6.3)
            yield r.ev("recover", at_index=r.i, kind="second_look", msg=f"looking again for the {o}")
            return _NEXT, ""
        # with search legs the search ends when the places run out or `search_total` looks were made, not after
        # a fixed count of misses while likely places remain (the miss count is the pre-v2 stop)
        stop = nowhere or looks >= total if self.cfg.get("search_legs") else (
            r.find_fails[o] > self.cfg["search_max"] + 1 or nowhere)
        if stop:
            if nowhere and self._second_look(r, o):
                return _NEXT, ""
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

    SECOND_LOOKS = 2       # without a belief: the two most likely places, once each (the default rate 0.1)

    def _relook_budget(self, r) -> int:
        """Places that may get a second look: from the detector's false-negative rate (SearchBelief.relook_budget)."""
        bel = getattr(r, "search", None)
        return bel.relook_budget() if bel is not None else self.SECOND_LOOKS

    def _second_look(self, r, o, only_if_beats: bool = False) -> bool:
        """F6.3: one look is not proof of absence (an object detector misses a share of its looks, the rate is the
        robot's own: ``planner.detector_miss_rate``). Look once more at the place the belief's posterior (prior x
        miss rate per miss) makes most probable among those already searched, up to the rate's budget, never the
        place the robot is at, spliced in place of the failed Find. ``only_if_beats``: only while that place is more
        probable than every place not looked at yet (a strong sighting against a flat rest), before the search
        is used up. False when done."""
        bel = getattr(r, "search", None)
        if bel is None or not self.cfg.get("search_legs"):
            return False
        again = r.__dict__.setdefault("second_looks", {}).setdefault(o, [])
        if len(again) >= self._relook_budget(r):
            return False
        opened = set(r.world.opened)
        if only_if_beats:
            beat = bel.relook_beats_next(o, r.world.arrived, opened)
            cands = [beat.place] if beat is not None and beat.place in r.searched else []
        else:
            cands = [p for p in bel.relook_order(o, exclude=[r.world.arrived]) if p in r.searched]
            cands += [p for p in r.searched if p not in cands]
        for p in cands:
            if p in again or p == r.world.arrived:
                continue
            leg = bel.leg_for(o, p, opened)
            if leg is None:
                continue
            bel.unsearch(o, p)
            if self._splice_leg(r, leg):
                again.append(p)
                r.searched.remove(p)
                return True
            bel.missed(o, p, opened, count=False)
            return False
        return False

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
        looks += len((getattr(r, "second_looks", {}) or {}).get(o, []))
        if (leg is None or looks >= int(self.cfg.get("search_total", 8)) - self._relook_budget(r)) and self._second_look(r, o):
            yield r.ev("recover", at_index=r.i, kind="second_look", msg=f"looking again for the {o}")
            return _NEXT, ""
        if leg is None or looks >= int(self.cfg.get("search_total", 8)):
            named = ", ".join(self._say_name(r.places, k) for k in r.searched) or "nowhere"
            yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})",
                               say=f"I couldn't find the {o}. I looked at: {named}.")
            return _STOP, ""
        r.find_fails[o] = 0                                     # the belief's next legs, without the person
        if self._splice_leg(r, leg):
            yield r.ev("recover", at_index=r.i, kind="search_leg", msg=f"looking for the {o} at {leg.text()}")
            return _NEXT, ""
        return None, f"; it is not at {r.world.arrived}. Look next at {leg.text()}"

    def _recover_phantom(self, r, st, reason, rule):
        """A Pick found nothing at the place where a Find had just seen the object (failure code ABSENT). The
        detector's false-positive rate says this happens, so it is not a grasp failure. The first time at a place
        the robot looks once more in place (a detection at the pick can miss a real object, the false-negative
        rate); the second time the Find counts as a false positive: the search state from before it is back
        and the place is a miss, so the search goes on over the other places, asks where it is, or gives up as
        for any Find that saw nothing."""
        o, world = st["object"], r.world
        here = world.arrived
        n = r.__dict__.setdefault("phantoms", {})
        n[(o, here)] = n.get((o, here), 0) + 1
        world.found = None; r.seen_at.pop(o, None)
        find = {"action": "Find", "object": o}
        r.steps[r.i:r.i] = [find]
        if n[(o, here)] == 1:
            yield r.ev("recover", at_index=r.i, kind="relook", msg=f"the {o} was not there to pick up; looking again")
            return _NEXT, ""
        r.search.restore_search(o)
        yield r.ev("recover", at_index=r.i, kind="phantom", msg=f"the {o} is not at {here}: it was a false detection")
        return (yield from self._recover_find(r, find, reason, rule))

    def _recover_pick(self, r, st, reason, rule):
        """Look + grasp again in place (pick_retries, not when out of lateral reach), then ask the person
        to move it closer. Returns (outcome or None, replan hint)."""
        o, world = st["object"], r.world
        if (r.failures and r.failures[-1]["code"] == FailureCode.ABSENT.value and getattr(r, "search", None) is not None
                and self.cfg.get("search_legs") and not getattr(r, "sensor_only", False)):
            # nothing there to grasp: the Find that sent the robot here saw a phantom (or the detector missed at the
            # pick), which is a search miss and not a grasp failure (no grasp retries, no "move it closer")
            return (yield from self._recover_phantom(r, st, reason, rule))
        votes = None
        if getattr(r, "sensor_only", False):
            # the skill reported success and only the check after it read an empty gripper: two more readings
            # decide, two of three (a noisy sensor is not a failed grasp, and not a full gripper either)
            votes = self._holding(r.node, default=False) and self._holding(r.node, default=False)
        if votes:
            self._effect(st, world, r.memory, r.placed)
            r.completed.append(st)
            for f in r.failures:
                if f["sig"] == (st["action"], o):
                    f["recovered"] = True
            r.i += 1
            yield r.ev("recover", at_index=r.i - 1, kind="reconcile", msg=f"the {o} is in the gripper (2 of 3 readings)")
            return _NEXT, ""
        if votes is None and self._holding(r.node, default=False):
            # failed, yet something is in the gripper (a neighbour?): never carry on with an unknown object
            world.holding = f"unknown object (picking {o})"
            yield self._finish(r, "failed", msg=f"the pick of the {o} failed but something is in my gripper; "
                                                f"please take it from me: {reason}")
            return _STOP, ""
        r.pick_tries[o] = r.pick_tries.get(o, 0) + 1
        r.found_at[o] = world.arrived; r.relooked.discard((o, world.arrived))   # it is still here: a miss is weak evidence
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
            ans, _n = self._ask_heard(q, r.node, self._log_fn(r.i), r.lang)   # silence: asked again (F3.8)
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
        if not extra and rule is not None and rule.then is Recovery.REPLAN and r.replans < self.cfg["max_replans"] \
                and not out_of_reach:
            # F3.9: no help: the policy's next action (contract.recovery GRASP_FAILED then=REPLAN), a new plan
            # that grasps again, bounded by max_replans; it used to end the run although the rule says replan
            extra = f"; the grasp of the {o} failed {r.pick_tries[o]} times and nobody could help: try it again"
        if not extra:
            yield self._finish(r, "failed", msg=f"could not pick up the {o}: {reason}")
            return _STOP, ""
        return None, extra

    def _recover_move(self, r, st, reason, rule):
        """Move again once, then ask the person to clear the way; else stop."""
        o = st["object"]
        # a move that ended away from its target (the pose check) is another failure than a blocked path: it
        # has its own retry, after the path retry and the person's help were used
        key = (o, "arrival") if "localization" in getattr(r, "failed_layers", ()) else o
        r.move_tries[key] = r.move_tries.get(key, 0) + 1
        if r.move_tries[key] <= 1:
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
            held = world.holding
            world.holding = world.holding_since = None
            if self._refind_dropped(r, st, rule):
                yield r.ev("recover", at_index=r.i, kind="refind", msg=f"the {held or 'object'} fell; picking it up again")
                return _NEXT, ""
            yield self._finish(r, "failed", msg=f"place failed and the object is no longer in the hand: {reason}")
            return _STOP, ""
        return None, ""

    def _refind_dropped(self, r, st, rule) -> bool:
        """The contract's DROPPED rule is REFIND: the object fell where the robot stands, so look for it there
        on that room's floor (a place the map must have), pick it up and do the failed step again. The steps are
        spliced in place (no planner call), at most ``rule.retries`` times per object; False when it cannot."""
        held = next((x.get("object") for x in reversed(r.steps[:r.i]) if x.get("action") == "Pick"), None)
        if not held or rule is None or getattr(rule, "action", None) is not Recovery.REFIND:
            return False
        n = r.refinds[held] = r.refinds.get(held, 0) + 1
        if n > max(int(getattr(rule, "retries", 1) or 1), 1):
            return False
        here = [r.world.arrived, (r.places.env.get(st.get("object")) or {}).get("spot"), st.get("object")]
        room = next((r.places.room(k) for k in here if k and r.places.room(str(k))), "")   # a person's spot: its place
        floor = next((k for k in r.places.keys if r.places.room(k) == room and r.places.head(k).lower() == "floor"), None)
        if not floor:
            return False                  # the map has no floor to pick things up from: the person is asked instead
        redo = [{"action": "MoveTo", "object": floor}, {"action": "Find", "object": held}, {"action": "Pick", "object": held}]
        if st.get("action") in ("Place", "PutIn", "Handover") and st.get("object"):
            redo.append({"action": "MoveTo", "object": st["object"]})
        r.steps[r.i:r.i] = redo
        r.world.found = None
        r.found_at[held] = floor or r.world.arrived      # it fell here: a miss here is weak evidence
        r.relooked.discard((held, floor or r.world.arrived))
        return True

    # ── 4. done only when the goals hold ──────────────────────────────────
    def _final_check(self, r) -> dict:
        if self._goals_hold(r.goals, r.world, r.placed, r.completed, r.world_at_start.arrived):
            if not getattr(r, "compiled", False):            # the planner's memory learns its own plans only
                # ... and only when the goals were seen to hold (planning/episode_memory.py)
                episode_memory.record_if_verified(r.planner, r.restated, r.completed, r.goals,
                                                  getattr(r, "goal_checks", []),
                                                  mode=self.cfg.get("memory_record"), run_id=r.run_id)
            parts = getattr(r, "refused_parts", [])
            if parts:
                g, why = parts[0]
                return self._finish(r, "success", say=self._in_lang(
                    self._humanize(f"I did the rest, but I won't {str(self._goal_phrase(g, r.places, lambda k: speech.say_name(r.places, k, 'en')))}: {why}."), r.lang),
                    localized=True, refused_parts=[goal_kinds.describe(x) for x, _w in parts])
            return self._finish(r, "success")
        return self._finish(r, "failed", msg="plan finished but the goals do not hold: "
                            + json.dumps(r.goals, ensure_ascii=False))
