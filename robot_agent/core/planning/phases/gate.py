"""The plan checker of AssistLoop, as a phase mixin (the gate of generate-and-certify).

What: ``GatePhase`` is a mixin with ``_check_plan`` (the body of the old ``AssistLoop._check``), ``_check_step``, ``_word_step``,
``_word_pick`` and ``_word_place``, moved VERBATIM out of ``assist_loop.py`` (Step S11 of the core generalization; the only
change is the name ``_check`` -> ``_check_plan``). The plan is simulated as written on a copy of the belief (nothing is changed)
and the result is ``[(step index, problem, critical)]``: the rules are the shared action model's, this adds the goal / ENV
findings and their wording for the planner. The finding sentences stay code here (their move to phrase keys is deferred).
Why: the loop keeps the common procedure; the checker is a phase of its own (docs/proposals/generalize_core_design.md,
"Phase pipeline"). ``tests/test_wiring_function_size.py`` looks ``assist_loop.py:_check`` up by file name, so ``AssistLoop._check``
stays in ``assist_loop.py`` as a two-line method that calls ``_check_plan``; every caller, test and golden mutation still goes
through ``_check``. ``_missing`` and ``_reaches`` stay in the loop.
Switch off: remove the ``GatePhase`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put these methods back into
``assist_loop.py`` (``_check_plan`` as ``_check``) and delete this module.
"""
from __future__ import annotations

from .. import action_model, concrete, plan_tidy


class GatePhase:
    def _check_plan(self, steps, world, places, goals):
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
