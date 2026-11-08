"""Option ``unapplied_change`` (default off, round 15): a correction the robot cannot apply is not silently dropped.

When a correction ("no, the other one") changes nothing, the understand step's own question is asked once and the correction is
read again. If it still changes nothing, the run remembers it, and at the end it does not report success: the goals it checks
are not what the person now wants. All of it is generic: no object, place or word of a language.

Hooks: ``on_goal_edit`` and ``on_goal_reread`` (observe the correction), ``on_goal_unapplied`` (ask once, record) and
``on_final`` (fail the run). Keys: ``enabled`` (legacy name ``unapplied_change``).
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="unapplied_change",
    keys=(Key("enabled", "bool", False, aliases=("unapplied_change",)),),
    hooks=("on_goal_edit", "on_goal_reread", "on_goal_unapplied", "on_final"),
    order=40,
    enabled="enabled",
    doc="A correction that changes nothing is asked about once, then fails the run instead of being dropped.",
)


class UnappliedChange(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault("unapplied_change", {})

    def on_goal_edit(self, run, text) -> None:
        self._state(run)["question"] = ""

    def on_goal_reread(self, run, text, understood, goals, q) -> None:
        """The understand LLM re-read the correction: keep the question it asked when it found nothing to apply."""
        if q or understood.get("status") != "clear" or not goals:
            self._state(run)["question"] = str(understood.get("question") or "")

    def on_goal_unapplied(self, run, turn):
        """``turn`` (``.text``, ``.change``): the correction could not be applied. Ask the understand step's question once and
        read again (``turn.change`` may become a change); else remember the correction. STOP when the run was cancelled."""
        q = self._state(run).get("question")
        if q:
            yield run.ev("clarify", question=q, round=run.asked + 1, kind="goal_change")
            ans = self.svc.ask(run, q)
            stop = self.svc.cancelled(run)
            if stop:
                yield stop
                return STOP
            if ans:
                run.dialogue += [("robot", q), ("user", ans)]
                turn.change = self.svc.reread_change(run, turn.text)
        if turn.change is None or turn.change.empty:
            self._state(run).setdefault("unapplied", []).append(turn.text)
        return None

    def on_final(self, run):
        unapplied = self._state(run).get("unapplied")
        if unapplied:
            return self.svc.finish(run, "failed", msg="I could not apply your change: " + "; ".join(unapplied))
        return None


register(SPEC, UnappliedChange)
