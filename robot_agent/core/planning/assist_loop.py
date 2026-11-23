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
import math
import os
import re
import time
import traceback
from pathlib import Path
from typing import Iterator, Optional

from robot_agent.observe.paths import data_dir
from robot_agent.contract import FailureCode
from robot_agent.state import current, host_id
from .loop import _DONE, _NEXT, _STOP, ClosedLoop, reconcile_world
from . import action_model, episode_memory
from .search_hooks import SearchHooks
from .step_ladder import StepLadder
from .which_one import WhichOne
from .phases import PHASE_MIXINS
from .loop_config import ASSIST_OPTIONS  # noqa: F401  (moved with the config resolution; re-exported)
from .phases.ground import USER_WORDS as _USER_WORDS   # moved with the grounding; the understand rounds use it too
from .places import Memory, Places, strip_article as _strip_article, words  # noqa: F401  (moved; re-exported)
from . import langdata
from . import (action_roles, dialog_agree, dialog_converge, dialog_ext, dialog_unstick, exec_integrity, goal_filter, goal_fix, language,
               role_models, speech)
from ..pack import actions as action_data, goal_semantics, prompts as pack_prompts
from ..options.dispatch import NULL as _NO_OPTIONS
from . import reconcile as sensed
from .robot import run_skill
from .verify_vlm import refuted
from .sensor_health import arrival_check
from .understand import (LLMBadOutput, LLMBudget, LLMBudgetExceeded, LLMUnavailable, consensus,
                         describe, find_issues, vacuous_goal_issues, issue_question, misheard_question, sampling, typed_refusal, verb_lost,
                         without_places, signature)

_LOC_ACTIONS = set(action_data.names(action_data.default_table(), arg=("place", "container")))   # pack: actions.yaml


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
        # current() and host_id() are called here, in this module, because tests patch both names on it (loop_config.py)
        common = Path(getattr(current(), "common_dir", "."))
        self._resolve_assist_cfg(overrides, lambda: Path(overrides.get("memory_path") or data_dir("object_memory", common, host_id())))
        self.cfg.update({"generator": self.cfg["planner"], "planner": "sage"})
        self._resolve_options_cfg(overrides)
        self._reaches = lambda steps, world, goals, loop=None: AssistLoop._reaches(steps, world, goals, self)

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
