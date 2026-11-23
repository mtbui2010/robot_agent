"""The finish block of AssistLoop: the run's `done` event, the spoken reason of a failure, and the failure notes.

What: ``FinishPhase`` is a mixin with ``_finish``, ``_say_of_msg``, ``_humanize``, ``_llm_failed``, ``_deciding_failure``,
``_note_failure``, moved VERBATIM out of ``assist_loop.py`` (Step S23b of the core generalization); the only change is the module
imports below (the old module-level names of ``assist_loop.py``).
``_finish`` is a real method here, never a delegating stub: ``tests/test_sage_v2_r3_m10_round1b.py`` reads its source for the
two phrase keys ``say_no_answer`` and ``say_not_confirmed``. The hook ``on_finish`` is observed at the same point as before.
Why: the loop file keeps the common procedure only (docs/proposals/generalize_core_design.md, "Phase pipeline"); every run ends
in ``_finish``, so this is the one place where the verdict of a run becomes the public ``done`` event.
Stays in ``assist_loop.py``: ``_run_blocking`` (the driver that creates the run state), ``_cancelled`` (two phase tests pin
``def _cancelled(`` in the loop file: ``tests/test_correct_phase.py``, ``tests/test_understand_rounds.py``) and the rest of the
loop; the moved methods reach them through ``self``. 28 methods of the other phases call ``_finish`` or ``_cancelled``.
Switch off: remove the ``FinishPhase`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put the methods back into
``assist_loop.py`` (after ``_run_blocking``, before ``_refused_reading``) and delete this module.
"""
from __future__ import annotations

import re
import time

from robot_agent.contract import FailureCode, classify_legacy_failure
from .. import speech
from ..budget import budget_end
from ..understand import LLMBudgetExceeded, LLMUnavailable


class FinishPhase:
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
