"""Option ``hazard``: the robot's own safety rules refuse a goal, or drop it and say so at the end (SAGE-v2 F4.3, docs/safety.md).

The rules are the robot's SAFETY PROFILE (manifest ``safety.profile``; ``core/safety`` is the engine that reads it). This module
is the option around that engine: the gate after the understand step, the question "does the safety layer forbid this goal"
that other steps ask, the partial-refusal sentence at the end and the ``refusals`` of the ``done`` event.

Keys: ``rules`` (legacy ``hazard_rules``; ``core`` | ``pack`` | ``off``, default ``core``; run option and environment only, never
the manifest), ``receivers`` (legacy ``hazard_receiver``; turns on the profile rules with ``enabled_by: hazard.receivers``), ``ask``
(legacy ``hazard_ask``, default off): an entry of ``safety.hazards`` with ``ask`` (a phrase key) asks first (docs/metal_microwave.md).
Hooks: ``on_goals`` (the gate), ``on_goals_met`` (the partial-refusal sentence), ``on_finish`` (the ``refusals`` field).
Services: ``forbids`` (the sentence that forbids a goal), ``refused_goals``, ``gate_off``, ``goal_dropped`` (refused after an answer).
Lab flag ``hazard``: ``{rules, refused, goals}`` each time the gate refuses or drops goals (the rule ids that forbid them); ``{asked, answer}``.
Switch off: ``rules=off`` (no rule at all), ``ask`` off (the default: an ``ask`` entry neither asks nor refuses).
"""

from __future__ import annotations

import re

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import langdata, speech
from robot_agent.core.planning.dialog_converge import goal_sig
from robot_agent.core.run_control import yes_no
from robot_agent.core.safety import Verdict, find_rule, profile_of
from robot_agent.core.safety.hazards import NOT_SAFE

SPEC = OptionSpec(
    name="hazard",
    keys=(Key("rules", "str", "core", aliases=("hazard_rules",), sources=("run", "env"), choices=("core", "profile", "pack", "off")),
          Key("receivers", "bool", False, aliases=("hazard_receiver",)), Key("ask", "bool", False, aliases=("hazard_ask",))),
    hooks=("on_goals", "on_goals_met", "on_finish"),
    services=("forbids", "refused_goals", "gate_off", "goal_dropped"),
    order=10,
    doc="Safety rules of the robot's profile refuse a goal (or drop it and say so); product x place and product x receiver rules.",
)


def support_goals(goals, refused) -> list:
    """The open / close goals that only served the refused goals (the container a refused put goes into or
    comes from) and that no kept goal uses: refused with them. Else the person would hear "I will open the
    fridge" for a request that was a put, say no, and never reach a refusal."""
    kept = [g for g in goals if all(g is not b for b in refused)]
    uses = lambda k: any(h.get("type") in ("on", "in") and k in (h.get("to"), h.get("from")) for h in kept)   # noqa: E731
    return [g for g in kept if g.get("type") in ("open", "close") and not uses(g.get("object"))
            and any(g.get("object") in (b.get("to"), b.get("from")) for b in refused)]


def answer_says(answer: str, metal: bool, yes, unsure: bool) -> str:
    """What the person's answer to an ``ask`` entry's question decides: ``unanswered`` (silence), ``refuse`` (a yes, an answer with a
    word a refusing rule holds, or "I don't know": the robot cannot rule it out), else ``proceed`` (a no, "plastic", "it is wooden")."""
    if not str(answer or "").strip():
        return "unanswered"
    return "refuse" if metal or yes is True or unsure else "proceed"


def unless_hit(entry: dict, goal: dict) -> bool:
    """Does the goal's object or ``said`` hold one of the entry's ``unless`` words (a declared material: no question)?"""
    text = " ".join(str(goal.get(k) or "") for k in ("object", "said")).lower()
    return any(re.search(p, text) for p in entry.get("unless") or ())


def refusal_record(goal, verdict) -> dict:
    """One entry of the ``done`` event's ``refusals`` (docs/safety.md): the rule, the reason and the goal refused."""
    return {"kind": "safety", "rule": verdict.rule, "why": verdict.say,
            "goal": {k: str(goal.get(k) or "") for k in ("type", "object", "said", "from", "to", "near_person")}}


class Hazard(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault("hazard", {})

    # ── the one place that reads the options ────────────────────────────────
    def verdict(self, goal, places):
        """The safety rule (profile + the manifest's legacy list) that forbids ``goal``, as a ``Verdict``; None."""
        manifest = getattr(self.svc.robot, "manifest", None)
        pack = [h for h in self._pack() if not h.get("ask")]           # an ``ask`` entry never refuses silently
        return find_rule(goal, places, profile_of(self.svc.robot), pack, str(self.cfg["rules"]),
                         {"hazard.receivers"} if self.cfg["receivers"] else ())

    def _pack(self) -> list:
        return (getattr(getattr(self.svc.robot, "manifest", None), "safety", None) or {}).get("hazards") or []

    def ask_entry(self, goal, places):
        """The first ``ask`` entry (key ``ask`` on, rules not off) whose objects x places fire for ``goal`` and whose ``unless`` words it
        lacks; None."""
        if not self.cfg["ask"] or str(self.cfg["rules"]) == "off":
            return None
        prof = profile_of(self.svc.robot)
        for e in (h for h in self._pack() if h.get("ask") and not unless_hit(h, goal)):
            v = find_rule(goal, places, prof, [e], "pack")
            if v is not None and not v.rule:                               # the entry itself (a profile receiver rule has an id)
                return e
        return None

    # ── services ────────────────────────────────────────────────────────────
    def forbids(self, goal, places):
        v = self.verdict(goal, places)
        return v.say if v else None

    def refused_goals(self, run):
        """The goals dropped for safety (already counted as covered by the read-back's issue check)."""
        return [g for g, _w in self._state(run).get("parts", [])]

    def gate_off(self):
        return True if str(self.cfg["rules"]) == "off" else None

    def goal_dropped(self, run, goal):
        """True for a goal refused after the person's answer (a re-sampled reading does not bring it back); None otherwise."""
        return True if self._state(run).get("answers", {}).get(goal_sig([goal])) == "refuse" else None

    # ── the question of an ``ask`` entry ────────────────────────────────────
    def _asked(self, run, found):
        """Flow: ``found`` with the verdict of each goal no rule refuses but an ``ask`` entry names (asked once per goal); STOP."""
        out = []
        for g, v in found:
            e = None if v else self.ask_entry(g, run.places)
            if e is not None:
                v = yield from self._ask(run, g, e)
                if v == STOP:
                    return STOP
            out.append((g, v))
        return out

    def _ask(self, run, g, e):
        """Flow: the entry's question about ``g`` (no phrase in any pack: not asked); a Verdict (refuse), None (proceed) or STOP."""
        answers, sig = self._state(run).setdefault("answers", {}), goal_sig([g])
        if sig not in answers:
            q = self._question(run, g, e)
            if q is None:
                return None
            yield run.ev("clarify", question=q, round=0, kind=SPEC.name)
            ans = self.svc.ask(run, q) or ""
            stop = self.svc.cancelled(run)
            if stop:
                yield stop
                return STOP
            answers[sig] = answer_says(ans, self.verdict({**g, "said": ans}, run.places) is not None, yes_no(ans)[0],
                                       bool(ans) and self.svc.is_unsure(run, ans))
            self.svc.lab(SPEC.name, asked=str(g.get("object") or ""), answer=answers[sig])
            if answers[sig] == "unanswered":
                yield self.svc.finish(run, "failed", msg=f"no answer to: {q}")
                return STOP
        return Verdict(str(e.get("say") or NOT_SAFE), "") if answers[sig] == "refuse" else None

    def _question(self, run, g, e):
        """The entry's phrase (language pack / robot i18n) with the object in the person's words, in the run's language, else the
        prompt language's (said through the translator); None when no pack has it."""
        obj, robot, key = self.svc.said_of(run, str(g.get("object") or "")), self.svc.robot, str(e["ask"])
        lang = getattr(run, "lang", None) or langdata.prompt_lang()
        return speech.pack_phrase(robot, key, lang, object=obj) or speech.pack_phrase(robot, key, langdata.prompt_lang(), object=obj)

    # ── hooks ───────────────────────────────────────────────────────────────
    def on_goals(self, run):
        """Goals the safety rules forbid: all of them -> a typed safety refusal (STOP); some -> those are dropped and said at the
        end; None."""
        found = [(g, self.verdict(g, run.places)) for g in run.goals]
        if self.cfg["ask"]:
            found = yield from self._asked(run, found)
            if found == STOP:
                return STOP
        bad = [(g, v.say) for g, v in found if v]
        if not bad:
            return None
        self.svc.lab(SPEC.name, rules=[v.rule for _g, v in found if v], refused=len(bad), goals=len(run.goals))
        st = self._state(run)
        st["refusals"] = list(st.get("refusals", []))
        st["refusals"] += [x for x in (refusal_record(g, v) for g, v in found if v) if x not in st["refusals"]]
        yield run.ev("understand_check", issues=[f"unsafe: {goal_kinds.describe(g)} ({w})" for g, w in bad], hazard=True)
        support = support_goals(run.goals, [g for g, _w in bad])
        if len(bad) + len(support) == len(run.goals):
            g, why = bad[0]
            yield self.svc.finish(run, "refused", refusal="safety", msg=f"unsafe: {goal_kinds.describe(g)}: {why}",
                                  say=f"I won't do that: {why}.")
            return STOP
        st["parts"] = list(st.get("parts", [])) + bad
        run.goals = [g for g in run.goals if all(g is not b for b, _w in bad) and all(g is not x for x in support)]
        return None

    def on_goals_met(self, run):
        """The goals hold but a part was refused: finish with the sentence "I did the rest, but I won't ...: why." """
        parts = self._state(run).get("parts", [])
        if not parts:
            return None
        g, why = parts[0]
        phrase = self.svc.goal_phrase(g, run.places)
        return self.svc.finish(run, "success", say=self.svc.in_lang(
            self.svc.humanize(f"I did the rest, but I won't {str(phrase)}: {why}."), run.lang),
            localized=True, refused_parts=[goal_kinds.describe(x) for x, _w in parts])

    def on_finish(self, run, fields) -> None:
        """The safety refusals on every final status (docs/safety.md), unless the caller set the field."""
        refusals = self._state(run).get("refusals")
        if refusals and "refusals" not in fields:
            fields["refusals"] = list(refusals)


register(SPEC, Hazard)
