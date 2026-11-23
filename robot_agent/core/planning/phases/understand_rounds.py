"""The understand rounds of AssistLoop, as a phase mixin.

What: ``UnderstandRounds`` is a mixin with the understand phase and its rounds: ``_understand_phase`` (the run's start state and the
loop over rounds), ``_take_given_goals`` (option ``accept_goals``), ``_understand_round`` (one understanding: the refusal checks,
the grounding, the question or the read-back), ``_two_places`` (the goal validator), ``_take_understanding`` (the edited goals or
the model's, grounded), the two-readings comparison (``_ask_views``, ``_compare_views``) and the two re-asks (``_reask_abstention``,
``_search_when_unsure``) with their prompt notes ``_FIND_NOTE`` and ``_UNSURE_NOTE``. They were moved VERBATIM out of
``assist_loop.py`` (Step S17 of the core generalization); the only changes are the imports below (the old module-level names of
``assist_loop.py``) and the relative import of ``Refusal`` that gained a dot.
Why: the loop keeps the common procedure; the understand rounds are one phase with one interface, the seam where the refusal rules,
the questions and the re-reading policies become data of the robot pack (docs/proposals/generalize_core_design.md, "Phase
pipeline"). The agreement and read-back methods (``_refused_reading``, ``_unsure_data``, ``_agree_state`` ...) and the corrections
stay in ``assist_loop.py`` until steps S18 and S19; the rounds call them through ``self``, so a name patched on ``AssistLoop`` still
wins (the class dict is searched before the mixin).
Switch off: remove the ``UnderstandRounds`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put the methods back into
``assist_loop.py`` (after ``_cancelled``, before ``_refused_reading``) and delete this module.
"""
from __future__ import annotations

import json

from .. import dialog_unstick, goal_fix, language, langdata, speech
from .. import goals as goal_kinds
from .. import loop as _loop
from ..understand import LLMBudgetExceeded, LLMUnavailable, misheard_question, signature, typed_refusal
from ...options import STOP as _STOP               # the same object as loop._STOP
from ...pack import goal_semantics

_DONE = _loop._DONE                                # the end marker of a round: the loop's own value (not an option marker)


class UnderstandRounds:
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
        if bad := self._distrusted(r):    # the language layer: a translation that cannot be trusted is asked again
            r.pivot_asked = True
            yield r.ev("understand_check", issues=[f"translation not trusted: {bad}"], repeat=True)
            r.repeat_asked = True
            return (yield from self._ask_clarify(r, speech.LocalText("q_repeat")))
        if (said := (yield from self.options.decide_flow("on_heard", r))) is not None: return said     # asr_guard: empty or holed words, asked again
        took = yield from self._take_understanding(r)
        if took is _STOP:
            return _STOP
        u, q = took
        if getattr(r, "view_question", None) is not None:        # understand_views: the readings differ, the person chooses
            return (yield from self._ask_views(r))
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
                            " ".join([r.task, self.original or ""] + [t for w_, t in r.dialogue if w_ == "user"]), heard,
                            marks=self._safety_profile())
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
            from ..understand import Refusal
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
        out = yield from self.options.decide_flow("on_goals", r)       # F4.3: the robot's own safety rules (option hazard)
        if out is _STOP:
            return _STOP
        r.goals, q = self._two_places(r, q)
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

    def _two_places(self, r, q):
        """The goal validator (``goal_kinds.conflicts``): (goals, the question with its two-destination question first). With
        ``dialog_agree`` the question declares its place options and keeps them when another question follows."""
        on = bool(self.cfg.get("dialog_agree"))
        r.goals, conflict = goal_kinds.conflicts(r.goals, lambda k: speech.say_name(r.places, k, "en"), on, goal_semantics.pack_of(self))
        if not conflict:
            return r.goals, q
        if on and getattr(conflict, "ask", None) and q:
            conflict = speech.LocalText("_two_places_q", text=f"{conflict} {q}", ask=conflict.ask)
            return r.goals, conflict
        return r.goals, conflict if not q else f"{conflict} {q}"

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
        if (self.cfg.get("reask_abstain") or self.cfg.get("reask_anchor")) and not getattr(r, "reasked", False):
            u = yield from self._reask_abstention(r, u)
            if u is _STOP:
                return _STOP
        if self.cfg.get("dialog_unstick") and not getattr(r, "unsure_reasked", False):
            u = yield from self._search_when_unsure(r, u)
            if u is _STOP:
                return _STOP
        if self.cfg.get("understand_views") and not r.dialogue and not getattr(r, "views_done", False):
            r.view_question = yield from self._compare_views(r, u)
            if r.view_question is _STOP:
                r.view_question = None
                return _STOP
        r.goals, q = self._ground_understood(r, u)
        return u, q

    def _ask_views(self, r):
        """The "which one?" question of ``understand_views`` (set by ``_compare_views``): asked once."""
        vq, r.view_question = r.view_question, None
        yield r.ev("understand_check", issues=["the original and the translation were read differently"], views=True)
        return (yield from self._ask_clarify(r, vq))

    def _compare_views(self, r, u):
        """Option ``understand_views``: a request in another language than the core's is read twice, from the person's
        own words and from the translator's English (the run's ``input_pivot`` is one view, the other pivot setting is
        the second). Both readings are grounded; when both have goals and the goal sets differ, and neither holds a goal
        the robot's safety rules refuse, the question "which one?" with both readings is returned (the run asks it
        instead of acting). Agreement, a reading without goals, no trusted translation, a spent budget or a failed second
        call: None, today's path. Once per run. _STOP when the LLM failed."""
        r.views_done = True
        lg = (r.lang or "en")[:2]
        if langdata.is_prompt_lang(lg) or lg not in language.LANGS or not u.get("goals") or u.get("status") not in ("clear", "ambiguous"):
            return None
        h = self.language.hear(r.task, pivot="translate")
        if not h.english or not h.trusted:
            return None
        other = "translate" if self.language.pivot == "none" else "none"
        try:
            u2 = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget, pivot=other)
        except LLMBudgetExceeded:
            return None
        except LLMUnavailable as e:
            yield self._llm_failed(r, e); return _STOP
        if not u2["goals"] or u2["status"] not in ("clear", "ambiguous"):
            return None
        saved = (list(getattr(r, "filtered", None) or []), list(getattr(r, "repaired", [])))
        try:
            g1, q1 = self._ground_understood(r, {**u, "goals": [dict(g) for g in u["goals"]]})
            g2, q2 = self._ground_understood(r, {**u2, "goals": [dict(g) for g in u2["goals"]]})
        finally:
            r.filtered, r.repaired = saved
        if not g1 or not g2 or q1 or q2 or signature(g1) == signature(g2):
            self._lab("understand_views", agreed=bool(g1 and g2 and signature(g1) == signature(g2)))
            return None
        if any(self._forbids(g, r.places) for g in g1 + g2):
            return None
        nice = lambda k: speech.say_name(r.places, k, "en")                  # noqa: E731
        a, b = (speech.LocalText.seq([self._goal_phrase(g, r.places, nice) for g in gs]) for gs in (g1, g2))
        self._lab("understand_views", agreed=False, asked=True)
        return speech.LocalText("q_which_reading", {"first": a, "second": b},
                                text=f"I can read that two ways: I will {a}, or I will {b}. Which one do you mean?",
                                ask={"intent": "choose", "options": [str(a), str(b)], "about": ""})

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
        anchor = bool(self.cfg.get("reask_anchor"))
        if anchor and u.get("status") != "ambiguous" and self.options.query("gate_off"):
            objs = None                # no hazard gate after the re-reading: a first-reading refusal stands
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
        if took and anchor:            # every place of the new reading is in the person's own words
            said = " ".join([r.task, self.original or ""] + [t for w_, t in r.dialogue if w_ == "user"])
            took = goal_fix.anchored(u2["goals"], said, r.places)
        self._lab("reask_anchor" if anchor else "reask_abstain", taken=took)
        yield r.ev("understand_check", issues=["abstained on where an object is: " + "; ".join(objs or ["?"])],
                   reask=True, reask_taken=took)
        return u2 if took else u

    _UNSURE_NOTE = ("Note: the person does not know where the object is. This robot finds objects itself ({find}). Do "
                    "not ask where it is: give the goals for ALL of the task and leave `from` empty for an object whose "
                    "place nobody named.")

    def _search_when_unsure(self, r, u):
        """Option ``dialog_unstick``: the person answered a where-is question with "I don't know" and the model abstains
        on location again. The robot searches, so the task is understood once more with that stated; the new reading is
        taken when it has goals, covers the named objects and is not ``unsafe``. Once per run. _STOP when the LLM failed."""
        finds = sorted(a for a in self.robot.plan_actions if a.lower() == "find")
        objs = goal_fix.abstains_on_location(u) if finds and r.dialogue and r.dialogue[-1][0] == "user" else None
        if objs is None or not dialog_unstick.unsure_answer(
                r.dialogue[-1][1], r.places, self._unsure_data(r), r.lang,
                self._manifest_planner("unstick", None), fallback_too=bool(self.cfg.get("dialog_agree2"))):
            return u
        r.unsure_reasked = True
        try:
            u2 = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget,
                                  note=self._UNSURE_NOTE.format(find=", ".join(finds)))
        except LLMBudgetExceeded:
            return u
        except LLMUnavailable as e:
            yield self._llm_failed(r, e); return _STOP
        took = bool(u2["goals"]) and u2["status"] != "unsafe" and goal_fix.covers_objects(u2["goals"], objs)
        self._lab("dialog_unstick", part="unsure_search", taken=took)
        yield r.ev("understand_check", issues=["not sure where an object is: the robot searches: " + "; ".join(objs or ["?"])],
                   reask=True, reask_taken=took)
        return u2 if took else u
