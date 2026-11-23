"""Agreement and read-back of AssistLoop, as a phase mixin.

What: ``AgreeReadBack`` holds the methods that settle whether the person agrees with the goals the robot understood. Agreement:
``_agreed_reading``, ``_agree_reask``, ``_texts_of``, ``_restated_by_person``, ``_progress``, ``_distrusted`` (options
``dialog_unstick``, ``dialog_agree``, ``dialog_agree2``). Read-back: ``_goal_readback``, ``_issues``, ``_object_source``,
``_resample``, ``_read_back``. They were moved VERBATIM out of ``assist_loop.py`` (Step S18 of the core generalization); the only
changes are the imports below (the old module-level names of ``assist_loop.py``). The hook ``on_issues`` and the services
``confirm_limit`` and ``unrefused`` are still asked through ``self.options`` by name, exactly as before.
Why: the loop keeps the common procedure; agreement and read-back are one phase with one interface, the seam where the questions
and the answers they read become data of the robot pack (docs/proposals/generalize_core_design.md, "Phase pipeline").
Stays in ``assist_loop.py``: ``_refused_reading``, ``_unsure_data`` and ``_agree_state`` (tests/test_understand_rounds.py, step S17,
reads their definitions in the loop file; ``option_services`` calls ``_unsure_data``), the grounding, the corrections and
``_complete_override`` (steps S19 and later). The moved methods reach them through ``self``.
Switch off: remove the ``AgreeReadBack`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put the methods back into
``assist_loop.py`` (after ``_agree_state``) and delete this module.
"""
from __future__ import annotations

import re
from typing import Optional

from .. import dialog_agree, dialog_converge, dialog_ext, dialog_unstick, goal_fix, langdata, speech
from .. import loop as _loop
from ..understand import (LLMBudgetExceeded, LLMUnavailable, consensus, describe, find_issues, issue_question,
                          vacuous_goal_issues, verb_lost, without_places)
from ...options import STOP as _STOP               # the same object as loop._STOP
from .ground import USER_WORDS as _USER_WORDS

_DONE, _NEXT = _loop._DONE, _loop._NEXT            # the end markers of a round: the loop's own values


class AgreeReadBack:
    def _texts_of(self, u) -> list:
        """An utterance and, when the language layer already holds it (no translator call), its trusted English."""
        out = [u]
        h = getattr(getattr(self, "language", None), "heard", {}).get(str(u).strip())
        if h is not None and getattr(h, "english", None) and getattr(h, "trusted", False):
            out.append(h.english)
        return out

    def _agreed_reading(self, r):
        """Option ``dialog_agree``, where the run would stop: the refused reading came back after the which-part question.
        When the person's answers agree with it (``dialog_agree.decide``) it counts as confirmed: said as a statement,
        _DONE. Once per run. None = today's stop."""
        st = self._agree_state(r)
        st["tried"] = True
        sig = dialog_converge.goal_sig(r.goals)
        guard = (dialog_ext.guard_for(r.lang, r.places, r.goals, getattr(self.robot.manifest, "planner", None), r.places.ext)
                 if self.cfg.get("dialog_agree2") else None)
        res = dialog_agree.decide(r.goals, r.places, r.dialogue, st["first"].get(sig),
                                  [x for x in st["refusals"] if x["sig"] == sig], st["asked"], self._issues(r, r.goals),
                                  describe, self._texts_of, bool(getattr(r, "part_asked", False)), getattr(r, "replaced", ()),
                                  guard)
        self._lab("dialog_agree", fire=res["fire"], last=res["last"], unsuperseded=len(res["unsuperseded"]),
                  bad_refusals=len(res["bad_refusals"]), open_doubts=len(res["open_doubts"]),
                  **({"reask": res["reask"], "guard": res["guard"], "garbled": res["garbled"]} if res.get("reask") else {}))
        if res.get("reask"):
            return (yield from self._agree_reask(r))
        if not res["fire"]:
            return None
        said = speech.LocalText.join(speech.LocalText("rb_restated"), self.summary(r.goals, r.places))
        yield r.ev("understand_check", issues=["the person's answers agree with a reading they refused"], correction="agreed",
                   say=said, goals=r.goals)
        return _DONE

    def _agree_reask(self, r):
        """Option ``dialog_agree2``: the answers agree with a refused reading, but only through garbled names, an object alone or
        words out of the language's script (``dialog_ext.Guard``). The reading is not taken as confirmed on that: the person is
        asked once more, as the same yes / no read-back. A yes counts as confirmed, anything else ends the run as before."""
        rb = self.summary(r.goals, r.places)
        cq = speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?")
        yield r.ev("clarify", question=cq, round=r.confirms, kind="confirm")
        ans, ok_, _rest = self._ask_yes_no(r, cq)
        stop = self._cancelled(r)
        if stop:
            yield stop; return _STOP
        r.dialogue += [("robot", cq), ("user", ans)]
        if ok_ is True:
            return _DONE
        return None

    def _restated_by_person(self, r) -> bool:
        """Did the person's latest answer say the request again (``dialog_unstick.restates``) AND add an ENV entity the
        request did not name (``adds_entity``)? A bare restatement is not read back again: it only re-draws the reading."""
        said = [t for w_, t in r.dialogue if w_ == "user"]
        orig = self.original or r.task
        return bool(said) and dialog_unstick.restates(orig, said[-1], self._manifest_planner("unstick", None)) and dialog_unstick.adds_entity(orig, said[-1], r.places)

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
        if getattr(r, "pivot_asked", False) or langdata.is_prompt_lang(r.lang or "en") or self.language.pivot == "none":
            return None
        last = [t for w_, t in r.dialogue if w_ == "user"]
        return self.language.untrusted(last[-1] if last else r.task)

    def _issues(self, r, goals) -> list:
        """understand.find_issues of `goals` against everything the person said (task, own words, answers)."""
        if not self.cfg["goal_readback"]:
            return []
        answers = [t for w_, t in r.dialogue if w_ == "user"]
        # a place the person replaced in a read-back correction no longer counts as asked for (docs/dialog.md)
        said = without_places(" ".join([r.task, self.original or ""]), r.places, getattr(r, "replaced", ()))
        goals = list(goals) + self.options.collect("refused_goals", r)   # refused for safety or by a condition: covered
        issues = find_issues(goals, " ".join([said] + answers), r.places,
                             english=self._object_source(r, answers), user_words=_USER_WORDS)   # objects: own words only
        if not answers and verb_lost(r.task):             # the request's verb was lost: the goals are a guess
            issues = [{"kind": "noverb", "what": ""}] + issues
        extra = goal_fix.ko_object_issues(goals, r.task)                                          # F4.5
        if not langdata.is_prompt_lang(getattr(r, "lang", None) or "en"):         # a goal set that only goes somewhere: the English reading's objects
            h = self.language.hear(r.task)
            extra += vacuous_goal_issues(goals, h.english if h.trusted else "", r.places, _USER_WORDS)
        return self.options.transform("on_issues", issues + [i for i in extra if i not in issues], r, goals)     # option ask_focus

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
        r.unsure = [describe(i) for i in issues] + list(getattr(r, "kind_doubts", None) or [])
        r.kind_doubts = []                                  # a doubt is named once, in the first read-back
        if self._unverified_objects(r):
            r.force_readback = True                                                    # F4.8: read it back
        if issues and not getattr(r, "sampled", False) and self.cfg["understand_samples"] > 0:
            r.sampled = True
            agreed = yield from self._resample(r, r.unsure)
            if agreed is _STOP:
                return _STOP
            if agreed is not None:
                keep = [g for g in agreed if not self._forbids(g, r.places) and not self.options.query("goal_dropped", r, g)]
                r.goals, r.unsure = (keep or r.goals), []          # a re-sample never brings back a refused or condition-dropped goal
                return None
        if not issues or getattr(r, "issue_asked", False):
            return None
        r.issue_asked = True
        iq = issue_question(issues, r.places) or None
        if iq and self.cfg.get("dialog_agree"):
            self._agree_state(r)["asked"] += [str(x) for x in r.unsure]      # these doubts were asked as their own question
        return iq

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
        if not dialog_converge.readback_left(r, self._converge(), self.options.query("confirm_limit", r)):     # confirm_limit: option ask_focus
            yield self._finish(r, "failed", msg="the user did not confirm the task"); return _STOP, None
        r.confirms += 1
        sig = dialog_converge.goal_sig(r.goals)
        if self.cfg.get("dialog_agree"):
            self._agree_state(r)["first"].setdefault(sig, len(r.dialogue))
        r.readings = list(readings) + [sig]
        rb = self.summary(r.goals, r.places)
        cq = speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?")
        if getattr(r, "restate_readback", False):
            r.restate_readback = False
            cq = speech.restated_readback(rb)
        elif unsure:
            cq = speech.LocalText.join(
                speech.LocalText("rb_unsure", {"issues": speech.LocalText.seq(unsure, "sep_semi")},
                                 text=f"I am not sure I understood ({'; '.join(unsure)})."), cq)
        yield r.ev("clarify", question=cq, round=r.confirms, kind="confirm")
        ans, ok_, rest = self._ask_yes_no(r, cq)
        stop = self._cancelled(r)
        if stop:
            yield stop; return _STOP, None
        if ok_ is True or (ok_ is False and self.options.query("unrefused", r, ans)):       # option ask_focus: "I don't know" is no refusal
            return _DONE, None
        if ok_ is None:
            yield self._finish(r, "failed", msg=f"no answer to: {cq}"); return _STOP, None
        r.dialogue += [("robot", cq), ("user", ans)]
        if self.cfg.get("dialog_agree"):                  # what the refusal was: a bare no, a not-sure, or words
            self._agree_state(r)["refusals"].append({
                "sig": sig, "ans": ans, "rest": rest,
                "unsure": dialog_unstick.unsure_answer(ans, r.places, self._unsure_data(r),
                                                       r.lang, self._manifest_planner("unstick", None),
                                                       fallback_too=bool(self.cfg.get("dialog_agree2")))})
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
