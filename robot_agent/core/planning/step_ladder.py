"""The own ladders of a failed Pick, MoveTo and release (after the recovery policy's rung), as a mixin of AssistLoop.

The rungs come from the declared table (``contract.recovery``): ``STEP_LADDER`` names the code whose rule is the
step's ladder (its action, RETRY, and its retries), ``step_rung`` picks the rung (RETRY, then ASK_HELP once, then
the failed code's REPLAN or STOP); the methods here run the rung. A robot changes a ladder through its
``recovery_overrides``; ``ask_help`` off closes the ASK_HELP rung. ``AssistLoop._step_failed`` calls
``_recover_pick`` / ``_recover_move`` / ``_recover_place``; ``_recover_find`` shares the ASK_HELP rung (``_may_ask`` /
``_ask_help``); a release whose hand is empty runs the DROPPED rule's REFIND rung (``_refind_dropped``, ``_splice_refind``).
``_recover_by_policy`` is the prepared seam of Step S14 (the policy the robot's pack names, core/pack/recovery.py); nothing
calls it yet: ``_step_failed`` keeps its dict while tests/test_find_ladder.py pins that dict's text.
"""

from __future__ import annotations

from robot_agent.contract import STEP_LADDER, Recovery, step_rung

from ..options import NEXT, STOP
from ..pack import goal_semantics, recovery as pack_recovery
from .speech import HELP_MOVE, HELP_PICK


class StepLadder:
    """Mixin of :class:`~robot_agent.core.planning.assist_loop.AssistLoop` (needs ``cfg``, ``options``, ``_holding``,
    ``_effect``, ``_finish``, ``_ask_heard``, ``_log_fn``, ``_search_told``, ``_recover_phantom``, ``_refuted``, ``yes_no``)."""

    def _recover_by_policy(self, r, st, reason, rule, no):
        """The recovery of a failed step after the policy rung: the policy the robot's pack names for it (by goal kind, by
        action: core/pack/recovery.py). A refuted step (``no``) and the builtins planner / tree give the planner's hint;
        ``stop`` ends the run. Returns (outcome or None, replan hint). Equal to the dict of ``_step_failed`` for the
        standard pack (tests/test_pack_recovery.py); not called yet."""
        pack = pack_recovery.recovering_pack(goal_semantics.pack_of(self))
        kinds = [g.get("type") for g in getattr(r, "goals", None) or () if isinstance(g, dict)]
        name = pack_recovery.PLANNER if no is not None else pack_recovery.policy_name(pack, st, kinds)
        fn, entry = pack_recovery.policy(pack, name)
        if fn is not None:
            return (yield from fn(r, st, reason, rule, pack_recovery.Ctx(self, r, entry)))
        if name == pack_recovery.STOP:
            yield self._finish(r, "failed", msg=f"{st.get('action')} {st.get('object')} failed: {reason}")
            return STOP, ""
        return None, self._refuted(r, st, no)

    def _recover_pick(self, r, st, reason, rule):
        """Look + grasp again in place (pick_retries, not when out of lateral reach), then ask the person to move it closer. Returns (outcome or None, replan hint)."""
        o, world = st["object"], r.world
        if (rule is not None and rule.action is Recovery.CONTINUE_SEARCH and getattr(r, "search", None) is not None
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
            return NEXT, ""
        if votes is None and self._holding(r.node, default=False):
            # failed, yet something is in the gripper (a neighbour?): never carry on with an unknown object
            world.holding = f"unknown object (picking {o})"
            yield self._finish(r, "failed", msg=f"the pick of the {o} failed but something is in my gripper; "
                                                f"please take it from me: {reason}")
            return STOP, ""
        r.pick_tries[o] = r.pick_tries.get(o, 0) + 1
        r.found_at[o] = world.arrived; r.relooked.discard((o, world.arrived))   # it is still here: a miss is weak evidence
        p3 = r.seen_at.get(o)
        out_of_reach = bool(p3) and abs(p3[1]) > self.cfg["reach_lateral"]
        ladder, replan = self._ladder(r, "Pick")
        todo = step_rung(ladder, rule, r.pick_tries[o], not self._may_ask(r, "pick", o), retries=self.cfg["pick_retries"],
                         can_retry=not out_of_reach)
        if todo is Recovery.RETRY:                       # the same skill pair in place: look again, grasp again
            yield self._look_again(r, o, "retry", f"pick {o} failed; looking again")
            return NEXT, ""
        extra = ""
        if todo is Recovery.ASK_HELP:
            q = f"The {o} is too far for my arm. {HELP_PICK}" if out_of_reach else f"I can't pick up the {o}. {HELP_PICK}"
            out, ans = yield from self._ask_help(r, st, "pick", q)
            if out is not None: return out
            k = r.places.ground(ans, ans) if ans else None
            if ans and self.yes_no(ans)[0] is True:
                yield self._look_again(r, o, "asked", "user helped; trying again")
                return NEXT, ""
            if k and k != world.arrived:
                r.memory.see(o, k, "user said"); world.found = None
                self._search_told(r, o, k)
                extra = f"; the user says the {o} is on {k}"
        if not extra and step_rung(ladder, rule, r.pick_tries[o], True, can_retry=False,
                                   replan_after=replan and r.replans < self.cfg["max_replans"] and not out_of_reach) is Recovery.REPLAN:
            # F3.9: no help: the failed code's then=REPLAN (GRASP_FAILED), a new plan that grasps again, bounded by max_replans
            extra = f"; the grasp of the {o} failed {r.pick_tries[o]} times and nobody could help: try it again"
        if not extra:
            yield self._finish(r, "failed", msg=f"could not pick up the {o}: {reason}")
            return STOP, ""
        return None, extra

    def _recover_move(self, r, st, reason, rule):
        """The MoveTo's ladder (contract STEP_LADDER): move again, then ask the person to clear the way; else stop."""
        o = st["object"]
        # a move that ended away from its target (the pose check) is another failure than a blocked path: it
        # has its own retry, after the path retry and the person's help were used
        key = (o, "arrival") if "localization" in getattr(r, "failed_layers", ()) else o
        r.move_tries[key] = r.move_tries.get(key, 0) + 1
        ladder, replan = self._ladder(r, "MoveTo")
        todo = step_rung(ladder, rule, r.move_tries[key], not self._may_ask(r, "move", o), replan_after=replan)
        if todo is Recovery.RETRY:
            yield r.ev("recover", at_index=r.i, kind="retry", msg=f"move to {o} again")
            return NEXT, ""
        if todo is Recovery.ASK_HELP:
            q = f"I can't get to the {o.replace('@', ' in the ')}. {HELP_MOVE}"
            out, ans = yield from self._ask_help(r, st, "move", q)
            if out is not None: return out
            if self.yes_no(ans)[0] is True:
                yield r.ev("recover", at_index=r.i, kind="asked", msg="user cleared the way; trying again")
                return NEXT, ""
        yield self._finish(r, "failed", msg=f"could not get to {o}: {reason}")
        return STOP, ""

    @staticmethod
    def _ladder(r, action):
        """The rule of the step's own ladder and whether it may end in a replan (contract STEP_LADDER)."""
        code, replan = STEP_LADDER[action]
        return r.robot.policy.rule_for(code), replan

    def _may_ask(self, r, kind, o) -> bool:
        """The ASK_HELP rung is open: the loop may ask (``ask_help``) and has not asked this ``kind`` about ``o``."""
        return bool(self.cfg["ask_help"]) and (kind, o) not in r.helped

    def _ask_help(self, r, st, kind, q):
        """The ASK_HELP rung (once per kind and object): an option (``on_ask_help``, e.g. ask_next: a menu) may ask in its
        place, else ``q`` is asked (silence: asked again, F3.8). (outcome, None) when the option ended it, else (None, answer)."""
        r.helped.add((kind, st["object"]))
        out = yield from self.options.decide_flow("on_ask_help", r, st, kind)
        if out is not None:
            return out, None
        yield r.ev("clarify", question=q, round=0)
        return None, self._ask_heard(q, r.node, self._log_fn(r.i), r.lang)[0]

    @staticmethod
    def _look_again(r, o, kind, msg):
        """The RETRY rung of a Pick: a Find of ``o`` first, in place (the ``recover`` event)."""
        r.steps[r.i:r.i] = [{"action": "Find", "object": o}]
        r.world.found = None
        return r.ev("recover", at_index=r.i, kind=kind, msg=msg)

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
                return NEXT, ""
            yield self._finish(r, "failed", msg=f"place failed and the object is no longer in the hand: {reason}")
            return STOP, ""
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
        self._splice_refind(r, st, held, floor)
        return True

    @staticmethod
    def _splice_refind(r, st, held, floor) -> None:
        """The REFIND rung: look for ``held`` on ``floor``, pick it up, go back to the failed release's place (it runs again)."""
        redo = [{"action": "MoveTo", "object": floor}, {"action": "Find", "object": held}, {"action": "Pick", "object": held}]
        if st.get("action") in ("Place", "PutIn", "Handover") and st.get("object"):
            redo.append({"action": "MoveTo", "object": st["object"]})
        r.steps[r.i:r.i] = redo
        r.world.found = None
        r.found_at[held] = floor or r.world.arrived      # it fell here: a miss here is weak evidence
        r.relooked.discard((held, floor or r.world.arrived))
