"""The engine services an option may use (``core/options/services.py`` is the interface), implemented over an AssistLoop.

This is the one place where option hooks reach loop internals; an option module itself never touches a ``_private`` name.
"""

from __future__ import annotations

from ..options.dispatch import NULL
from ..options.spec import STOP
from . import dialog_converge, speech


class LoopServices:
    def __init__(self, loop) -> None:
        self._lp = loop

    @property
    def robot(self):
        return self._lp.robot

    def now(self) -> float:
        return self._lp._now()

    def lab(self, flag: str, **info) -> None:
        self._lp._lab(flag, **info)

    def role(self, name: str):
        return self._lp._role(name)

    def hand_action(self):
        return self._lp._hand_action()

    def finish(self, run, status: str, **kw) -> dict:
        return self._lp._finish(run, status, **kw)

    def ask(self, run, question):
        """One question to the person: their answer ('' on silence). Counts the turn like every other question of the run."""
        lp = self._lp
        ans, _n = lp._ask_heard(question, run.node, lp._log_fn(-1), run.lang)
        run.heard += lp._answer_source == "robot"
        run.asked += 1
        return ans

    def emit(self, run, event: str, **fields) -> dict:
        """An event from a hook or service that cannot yield (not a flow hook): logged with the run's events (``run.ev``) and pushed through the
        host's live channel when it has one (``emit`` of the loop, the one ``plan_step`` uses). The event."""
        ev = run.ev(event, **fields)
        getattr(self._lp, "options", NULL).observe("on_event", ev)     # not yielded: the watchers (trace) see it here
        push = getattr(self._lp, "_emit", None)
        if push is not None:
            push(ev)
        return ev

    def llm_calls(self, fn):
        """Tell ``fn(call)`` of every LLM call that goes through ``observe.llm.call`` from now on, ``call["purpose"]`` set to the label of
        the latest LLM-budget take before it (``LLMBudget.take(what=)``, used up by the call; none: ""). Returns the function that stops it."""
        from robot_agent.observe import llm as observed
        from .understand import LLMBudget

        def told(call):
            call["purpose"], LLMBudget.last_what = LLMBudget.last_what, ""
            fn(call)
        return observed.listen(told)

    def look_for(self, run, obj):
        """One more Find of `obj`, made the way the end-of-run goal check makes it: (seen: bool, the skill)."""
        res, skill = self._lp._look(run, {"action": "Find", "object": obj})
        return bool(res is not None and res.ok), skill

    def run_step(self, run, step: dict):
        """One more step of the end-of-run check (MoveTo, or the pack's end action), its effect applied on success: the skill result or None."""
        return self._lp._look(run, dict(step))[0]

    def try_step(self, run, st, ladder: bool = False):
        """Flow: one step ``st`` through the engine's own step path (``on_step`` options, guard, bind, the skill, its verify, the record and
        events), appended to the run's plan as it runs, for an option that runs the goals itself (``runs_goals``). True when it succeeded,
        False when it failed, None when the run ended. ``ladder`` False: the recovery is the caller's (it answers ``on_step_failed``).
        ``ladder`` True: the engine's own recovery runs too, and the steps it puts in place (a retry, a look before a grasp, a search
        leg, a refind) run before this returns; a planner rung the caller holds back (service ``no_replan``) leaves the step failed."""
        n, k = len(run.completed), len(run.run.steps)
        run.steps.append(dict(st))
        run.i = len(run.steps) - 1
        while True:
            out = yield from self._lp._run_step(run, run.steps[run.i])
            if out == STOP or not ladder or run.i >= len(run.steps):
                break
        run.i = len(run.steps)
        if out == STOP or not ladder:
            return None if out == STOP else len(run.completed) > n
        last = run.run.steps[-1] if len(run.run.steps) > k else None   # the last record made here (none: stopped at the guard, or done by a reconcile)
        own = last is not None and last.index == len(run.steps) - 1      # of the step itself, not of a step the ladder put before it
        return (own and last.status == "success") or any(c is run.steps[-1] for c in run.completed[n:])

    def changes_waiting(self, run) -> bool:
        """Is a goal change said during the run (MODIFY / ADD / DROP / NEW) waiting for the loop's next step boundary? Not taken: the loop
        applies it there (``_apply_pending``), e.g. once the option that runs the goals itself (``runs_goals``) gives the run back."""
        from ..run_control import current_run
        return bool(getattr(current_run(), "pending", None))

    def ask_clarify(self, run, question):
        """Flow: ask ``question`` as the loop's own clarify step (the answer joins the dialogue); the step's result (go on reading, or stop)."""
        return self._lp._ask_clarify(run, question)

    def cancelled(self, run):
        return self._lp._cancelled(run)

    def safety_gate(self, run):
        """Flow: the run's goals as they are now through the safety gate once more (the ``on_goals`` of the options that serve
        ``forbids``), for an option that changed a goal after the gate ran: what the gate returns (STOP when it refused them all), else None."""
        return self._lp.options.decide_flow_of("forbids", "on_goals", run)

    def replan_or_fail(self, run, step, reason: str, msg: str):
        return self._lp._replan_or_fail(run, step, reason, msg)

    def say_name(self, places, key: str) -> str:
        return self._lp._say_name(places, key)

    def in_lang(self, text, lang: str):
        return self._lp._in_lang(text, lang)

    def humanize(self, text) -> str:
        return self._lp._humanize(text)

    def goal_phrase(self, goal, places):
        return self._lp._goal_phrase(goal, places, lambda k: speech.say_name(places, k, "en"))

    def subgoals(self, run):
        """The planner's sub-goals for the run's goals now (one per goal), or None when the planner decomposes itself."""
        return self._lp._subgoals(run)

    def readback_left(self, run) -> bool:
        """Would the loop read the goals back once more now? The loop's own rule (``dialog_converge.readback_left``, the ``confirm_limit`` service)."""
        lp = self._lp
        return dialog_converge.readback_left(run, lp._converge(), lp.options.query("confirm_limit", run))

    def replan_round(self, run) -> tuple:
        """(replans the run has recorded, planner calls left in its ``max_replans`` budget). The calls of one replan (its suffix and the one
        refine) see the same first number; a refine follows a rejected suffix only while the second is above 0."""
        return len(getattr(getattr(run, "run", None), "replans", None) or ()), int(self._lp.cfg["max_replans"]) - int(getattr(run, "replans", 0) or 0)

    def ask_model(self, run, prompt, schema):
        """ONE extra call to the planner's model (no repair call, stoppable, counted in the run's LLM budget): the JSON answer as a dict, or None
        (model unavailable, budget used up, no JSON). A stop of the run is raised as it is."""
        from ..run_control import call_cancellable, is_cancel
        from .understand import ask_json
        lp = self._lp
        try:
            return ask_json(lp._llm(), prompt, schema, budget=getattr(run, "llm_budget", None), retries=0, where="option",
                            call=call_cancellable, passthrough=is_cancel, options=lp._sampling())
        except Exception as e:
            if is_cancel(e):
                raise
            return None

    def reread_change(self, run, text):
        return self._lp._llm_goal_change(run, text)

    # ── asking where an object is (option ask_source) ──
    def remembered(self, run, obj):
        """Where the object memory puts `obj` while that sighting is still trusted, else None."""
        return self._lp._remembered(run.memory, obj)

    def said_of(self, run, obj) -> str:
        """The object in the person's own words (what the question should say)."""
        return self._lp._said_of(run, obj)

    def ground_place(self, run, text):
        """The ENV place a spoken answer names, or None."""
        return run.places.ground(text, text) if text else None

    def note_answer(self, run, question, answer, obj, place) -> None:
        """Remember that the person said `obj` is at `place` (a sighting the search may trust), and the exchange."""
        run.memory.remember_answer(run.task, question, answer)
        run.memory.see(obj, place, "user said")

    def mark_asked_where(self, run, obj) -> None:
        """The person was already asked where `obj` is: the search does not ask the same thing again after misses."""
        run.__dict__.setdefault("pre_helped", set()).add(("where", obj))      # carried into the run's `helped` when the execution starts


    def ask_again_on_miss(self, run, obj) -> None:
        """The person named a place for `obj`: the first look that does not see it asks them again (the search's "where is it?"),
        instead of searching on alone until `search_max` misses."""
        run.__dict__.setdefault("pre_fails", {})[obj] = int(self._lp.cfg["search_max"])

    # ── the hand and a lost object (option drop_search) ──
    def hand_sensor(self, run):
        """The raw result of the robot's hand sensor (the skill that verifies its Pick); None when it has none or the call raised."""
        skill = self._lp.robot.holding_sensor()
        try:
            return self._lp.sr.execute(skill, {}, run.node) if skill else None
        except Exception:
            return None

    def let_go(self, run, why: str):
        """The belief lets go of what it holds (the object memory: place unknown, ``why``): the object, or None when it held nothing."""
        held = run.world.holding
        if held:
            run.memory.see(held, None, why)
            run.world.holding = run.world.holding_since = None
        return held

    def refind_on(self, run, step, obj, floor) -> None:
        """The engine's REFIND rung (``_splice_refind``): MoveTo ``floor``, Find and Pick ``obj``, back to ``step``'s place, then ``step`` again."""
        self._lp._splice_refind(run, step, obj, floor)

    # ── the person's choice after a failed step (option ask_next, hook on_ask_help) ──
    def retry_here(self, run, step) -> None:
        """The failed ``step`` runs once more where the robot is, as the loop's own retry does: the look is made again (a Pick gets a
        Find before it); the step stays the next one."""
        run.world.found = None
        if step.get("action") == "Pick":
            run.steps[run.i:run.i] = [{"action": "Find", "object": step.get("object")}]

    def told_at(self, run, obj, place) -> str:
        """The person said ``obj`` is at ``place`` (``note_answer`` remembers it): the search trusts it as the loop does with a where answer
        (``_search_told``), the place no longer counts as searched; the replan hint that names it."""
        run.world.found = None
        self._lp._search_told(run, obj, place)
        if place in run.searched:
            run.searched.remove(place)
        return f"; the user says the {obj} is on {place}"

    def search_on(self, run, obj):
        """Flow: the search for ``obj`` goes on over the likely places not searched yet, without the person (the loop's answer to an
        "I don't know"): (outcome or None, replan hint)."""
        return self._lp._search_on(run, obj)

    # ── an answer that names no place (option ask_source: use_room, clarify_unusable, confirm_near) ──
    def room_places(self, run, text) -> tuple:
        """(room, the searchable places in it) when the answer names a room and no place in it, else ("", [])."""
        c = run.places.candidates(text, text) if text else []
        if len(c) != 1 or not c[0].startswith("@"):
            return "", []
        return c[0][1:], self.places_of_room(run, c[0][1:])

    def prefer_places(self, run, obj, keys) -> None:
        """The search looks at ``keys`` first for ``obj`` (the belief's ``prefer``; applied when the run's belief is built)."""
        run.__dict__.setdefault("pre_prefer", {})[obj] = list(keys)

    def prefer_now(self, run, obj, keys) -> None:
        """``prefer_places`` for a run whose search belief already exists (the person answered the re-asked "where is it?")."""
        self.prefer_places(run, obj, keys)
        belief = getattr(run, "search", None)
        if belief is not None:
            belief.prefer(obj, keys)

    def first_unsearched(self, run, obj, keys):
        """The best of ``keys`` the search has not looked at yet (the belief's order from where the robot stands), else the first key."""
        belief = getattr(run, "search", None)
        if belief is None:
            return keys[0] if keys else None
        done = set(run.searched or ())
        return next((p for p in belief.order(obj, run.world.arrived) if p in keys and p not in done), keys[0] if keys else None)

    def is_unsure(self, run, text) -> bool:
        """Is the answer an "I don't know" (the pack's and the language's phrases, ``dialog_unstick.unsure_answer``)?"""
        from . import dialog_unstick
        lp = self._lp
        return dialog_unstick.unsure_answer(text, run.places, lp._unsure_data(run), run.lang, lp._manifest_planner("unstick", None))

    def unsure_leftover(self, run, text):
        """The words of an "I don't know" answer once its "not sure" phrases are taken out ("" = it says nothing else); None = it is not one."""
        from . import dialog_unstick
        if not self.is_unsure(run, text):
            return None
        out = dialog_unstick._fold(text)
        for p in sorted(dialog_unstick.unsure_phrases(self._lp._unsure_data(run), run.lang), key=len, reverse=True):
            out = out.replace(p, " ")
        return " ".join(out.split())

    def request_english(self, run) -> str:
        """The request's words in the prompt language: the task itself, or the language layer's trusted reading of it (no new call); "" if none."""
        from . import langdata
        if langdata.is_prompt_lang(getattr(run, "lang", None) or "en"):
            return run.task or ""
        h = self._lp.language.hear(run.task)
        return h.english if h.trusted else ""

    def confirm_near(self, run, text):
        """Flow: the one place the answer nearly names (``understand.fuzzy_place``), confirmed with a yes / no question; None."""
        return self._lp._confirm_near(run, text, typo=True)

    def confirm_near_room(self, run, text, threshold: float = 0.25):
        """Flow: the one room the answer nearly names (``understand.fuzzy_room``), confirmed with a yes / no question; (room, its places)
        or ("", [])."""
        from .understand import fuzzy_room
        room = fuzzy_room(run.places, text, threshold, typo=True) if text else None
        if not room:
            return "", []
        said = room.replace("_", " ")
        q = speech.LocalText("q_did_you_mean", {"object": said, "place": "@" + room})
        yield run.ev("clarify", question=q, round=0, kind="did_you_mean_room")
        _a, ok_, _rest = self._lp._ask_yes_no(run, q)
        if ok_ is not True:
            return "", []
        return said, self.places_of_room(run, room)

    @staticmethod
    def places_of_room(run, room) -> list:
        """The searchable places of ``room`` (not persons, not ``search: false``)."""
        return [k for k in run.places.in_room(room) if not (e := run.places.env.get(k) or {}).get("person")
                and e.get("search", True) is not False]


def option_dispatcher(loop, overrides=None, environ=None):
    """The dispatcher of ``loop`` for the run options ``overrides``: what ``AssistLoop.__init__`` builds. For a host or a test
    that has a loop without running ``__init__``."""
    import os
    from ..options import Dispatcher
    planner = getattr(getattr(getattr(loop, "robot", None), "manifest", None), "planner", None)
    return Dispatcher(LoopServices(loop), overrides or {}, os.environ if environ is None else environ, planner)


def refresh_options(loop, overrides=None, environ=None, registry=None):
    """Resolve the option keys of a built ``loop`` again, against its manifest NOW, and mirror them into ``loop.cfg``.

    The keys are resolved once, when the loop is built (``AssistLoop._resolve_options_cfg``). A host that merges pack data into
    the loop's robot view afterwards (a manifest overlay) calls this with the run options it built the loop with; without it an
    option whose data is only in that overlay keeps its default (empty) value and does nothing. Call it before a run: the option
    instances are made again (their per-run state lives in ``run.opt``). A manifest that did not change gives the same keys."""
    import os
    from ..options import REGISTRY, Dispatcher
    planner = getattr(getattr(getattr(loop, "robot", None), "manifest", None), "planner", None)
    loop.options = Dispatcher(LoopServices(loop), overrides or {}, os.environ if environ is None else environ, planner,
                              registry=REGISTRY if registry is None else registry)
    loop.cfg.update(loop.options.mirror())
    return loop.options
