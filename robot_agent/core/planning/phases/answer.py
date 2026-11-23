"""The say / ask plumbing of AssistLoop, as a phase mixin (the answer phase).

What: ``AnswerPhase`` is a mixin with ``_in_lang`` (the one speaking function), ``_ask`` / ``_ask_io`` / ``_ask_heard`` (the one
place a question is said, heard and counted), ``yes_no``, ``_ask_yes_no``, ``_ask_clarify`` (bounded by ``clarify_rounds``),
``_asks_where``, ``_essential``, ``_fresh_question`` and ``_restates``, moved VERBATIM out of ``assist_loop.py`` (Step S15 of the
core generalization; the only change is that the five in-function ``from ..run_control import`` lines gained a dot). No question
gets a kind and no inline sentence gets a key here: the phrase keys they name stay code, as before.
Why: the loop keeps the common procedure; the ask machinery is the seam of the question-and-answer schema later
(docs/proposals/generalize_core_design.md, "Phase pipeline", "Answers"). ``_fresh_question`` and ``yes_no`` are static methods and
stay callable through the class (``AssistLoop._fresh_question``, ``AssistLoop.yes_no``); tests and golden mutations that patch a
name on ``AssistLoop`` still win, because the class dict is searched before the mixin.
Switch off: remove the ``AnswerPhase`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put the methods back into
``assist_loop.py`` (``_in_lang`` .. ``yes_no`` before ``summary``, the rest before ``_obs``) and delete this module.
"""
from __future__ import annotations

import re

from .. import dialog_agree, dialog_converge, langdata, speech
from ...options import NEXT as _NEXT, STOP as _STOP     # the same objects as loop._NEXT / loop._STOP (no private import)


class AnswerPhase:
    def _in_lang(self, text, lang) -> str:
        """``text`` as the user hears it: the ONE speaking function (``LanguageAdapter.speak``)."""
        return self.language.speak(text, lang)

    def _ask(self, question, node, log_fn, lang) -> str:
        """Ask the user (``interact.dialog`` via run_control: the run waits on the question, so an answer
        typed / sent by any client answers it; the robot's Ask skill does the I/O). '' when there is no
        answer / no channel. Where the answer came from: ``self._answer_source`` (robot | typed | '')."""
        from ...run_control import ask_person
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
        from ...run_control import cancel_requested
        if cancel_requested():                           # stopped while the question was being written: the robot asks nothing more
            return ""
        # what the person heard, for the understanding's dialogue (F5.5: a corrected read-back the model only saw in English)
        self.__dict__.setdefault("_said_as", {})[str(question)] = said
        params = {(spec.required_params[:1] or ["inputs"])[0]: said}
        params.update({k: v for k, v in (("retries", 0), ("lang", lang)) if k in spec.param_names})    # optional params the skill may take
        try:
            r = self.sr.execute(spec.name, params, node, log_fn=log_fn)
            return str((r or {}).get("text") or (r or {}).get("answer") or "").strip() if (r or {}).get("isdone") else ""
        except Exception:
            return ""

    def _ask_heard(self, question, node, log_fn, lang):
        """Ask; on silence ask again (`silence_retries`). Returns (answer, n_asked)."""
        from ...run_control import ask_person
        if self.cfg.get("dialog_unstick"):
            question = speech.calm(question)          # an apology of its own is not stacked behind "I didn't hear you"
        ans, n = ask_person(question, lambda q: self._ask_io(q, node, log_fn, lang),
                            silence_retries=self.cfg["silence_retries"])
        self._answer_source = ans.source
        return ans.text, n

    @staticmethod
    def yes_no(text):
        """(True | False | None, correction text) from a spoken yes / no answer (``interact.dialog``)."""
        from ...run_control import yes_no
        return yes_no(text)

    def _ask_yes_no(self, r, q):
        """A yes / no question (``interact.dialog.ask_yes_no``): an unclear answer ("yet", noise) is asked again
        (`yes_no_reasks`, the robot's i18n `yes_or_no`); still unclear = a bare "no" (an open question next).
        Returns (answer, True | False | None, correction text)."""
        from ...run_control import ask_yes_no

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
        if self.cfg.get("dialog_agree"):                  # a choice between places, answered: the option not taken is settled
            gone = dialog_agree.settle_pair(q, ans, r.places, lambda u: self._texts_of(u))
            if gone:
                r.replaced = set(getattr(r, "replaced", ())) | gone
        if self._restates(r, ans) and not getattr(r, "free_round_used", False):
            r.free_round_used = True     # an answer that says the whole request again was informative: not a spent round
            r.asked -= 1
        return _NEXT

    @staticmethod
    def _asks_where(q) -> bool:
        """Does ``q`` ask where a person is ("Where are you?" / "Where is X?")?"""
        if not isinstance(q, speech.LocalText):
            return False
        keys = ("q_where_are_you", "q_where_bring", "q_where_is_person")
        return q.key in keys or any(isinstance(p, speech.LocalText) and p.key in keys for p in q.parts)

    @staticmethod
    def _essential(q) -> bool:
        """Is ``q`` a question the task cannot go on without whatever else was asked (where the speaker is, for a
        delivery to them)? It may take one round past ``clarify_rounds``."""
        if not isinstance(q, speech.LocalText):
            return False
        return q.key in ("q_where_are_you", "q_where_bring") or any(
            isinstance(p, speech.LocalText) and p.key in ("q_where_are_you", "q_where_bring") for p in q.parts)

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
        try:
            p = self._manifest_planner("restates", {}) or {}               # a priori defaults the manifest may replace
        except AttributeError:                                              # a bare helper object (no robot): the defaults
            p = {}
        frac, wlen, nmin = (langdata.number(p, "frac", 0.6), langdata.number(p, "word_len", 3), langdata.number(p, "min_words", 4))
        words = lambda t: {w for w in re.findall(r"\w+", str(t).lower()) if len(w) >= wlen}      # noqa: E731
        base = words(self.original or r.task)
        return len(base) >= nmin and len(base & words(ans)) >= frac * len(base)
