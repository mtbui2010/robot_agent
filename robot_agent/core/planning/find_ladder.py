"""The Find ladder of AssistLoop: what happens after a Find saw nothing (look again, search on, ask where it is, give up).

What: ``FindLadder`` is a mixin with ``_recover_find``, ``_relook_why``, ``_relook_here``, ``SECOND_LOOKS``, ``_relook_budget``,
``_second_look``, ``_splice_leg``, ``_said_of``, ``_confirm_near``, ``_search_on`` and ``_recover_phantom``, moved VERBATIM out
of ``assist_loop.py`` (step S13 of the core generalization). They use ``self`` only through the same calls as before: the
search hooks (``_search_missed``, ``_search_total``, ``_search_over``, ``_missed_enough``, ``_search_told``), the ask plumbing
(``_may_ask``, ``_ask_help``, ``_ask_yes_no``), ``_finish``, ``_lab``, ``_ground_all``, ``_role``, ``_say_name``,
``_manifest_planner``, ``self.options``, ``self.robot`` and ``self.cfg``.
Why: the Find ladder is the largest piece of the recover phase and reads no other part of the loop's state than the run record
``r``; the standard pack will name it ``std.find_search`` (docs/proposals/generalize_core_design.md, "Phase pipeline").
``_step_failed`` in ``assist_loop.py`` still picks ``self._recover_find`` and ``_refuted`` stays there.
Switch off: remove the ``FindLadder`` line from ``PHASE_MIXINS`` in ``phases/__init__.py`` and put the methods back.
"""
from __future__ import annotations

from robot_agent.contract import Recovery
from . import action_model, exec_integrity, speech
from ..options import NEXT as _NEXT, STOP as _STOP


class FindLadder:
    def _recover_find(self, r, st, reason, rule):
        """Not seen here: mark the place searched (absence only, the policy says), search on through
        the planner, ask the person where it is after search_max. Returns (outcome or None, replan hint)."""
        o, world = st["object"], r.world
        world.found = None
        here, code = world.arrived, r.failures[-1]["code"] if r.failures else ""
        if rule.action is Recovery.CONTINUE_SEARCH and here and r.found_at.get(o) == here and (o, here) not in r.relooked:
            # the object was lost right here earlier in this run (a failed grasp, or it fell): one miss here is weak
            # evidence that it is gone, so look once more before the place counts as searched
            r.relooked.add((o, here))
            yield r.ev("recover", at_index=r.i, kind="relook", msg=f"the {o} was seen here: looking again")
            return _NEXT, ""
        if rule.mark_searched and here and here in world.opened and r.places.openable(here) and here not in r.doubted:
            # not seen inside a container believed open: the belief may be stale (closed meanwhile); doubt it
            # once instead of counting the place as searched (the repair opens it and looks again)
            r.doubted.add(here); world.opened.discard(here)
            return None, (f"; precondition: {here} is not open (it was believed open, but the {o} is not visible "
                          f"inside): Open {here}, then Find the {o} again")
        if self.cfg.get("relook") and rule.action is Recovery.CONTINUE_SEARCH and rule.mark_searched and here:
            out = yield from self._relook_here(r, o, here)       # round 14: one more look, by another sense
            if out is not None:
                return out
        # absent: the place counts as searched; could not look / out of reach: it does not (policy)
        if world.arrived and world.arrived not in r.searched and rule.mark_searched:
            r.searched.append(world.arrived)
        leg = yield from self._search_missed(r, o, rule)
        # the belief has nowhere left to look (searched places are never searched again): ask / give up now
        nowhere = leg is None and rule.mark_searched and getattr(r, "search", None) is not None
        r.find_fails[o] = r.find_fails.get(o, 0) + 1
        if (self._missed_enough(r, o) or nowhere or self._search_over(r, o, True)) and self._may_ask(r, "where", o):
            q = speech.LocalText("q_where_is", {"object": self._said_of(r, o)}, text=f"I couldn't find the {o}. Where is it?")
            out, ans = yield from self._ask_help(r, st, "where", q)
            if out is not None: return out
            k = r.places.ground(ans, ans) if ans else None
            if not k and ans:
                k = (yield from self.options.decide_flow("on_where_answer", r, o, ans)) or (yield from self._confirm_near(r, ans))   # a room / unusable answer (ask_source); a noisy one: "Did you mean the X?" (F3.7)
            if not k and self.cfg.get("search_legs"):
                return (yield from self._search_on(r, o))       # "I don't know": search the likely rest (F6.2)
            if not k:
                yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})")
                return _STOP, ""
            r.memory.remember_answer(r.task, q, ans); r.memory.see(o, k, "user said")
            self._search_told(r, o, k)
            if k in r.searched:
                r.searched.remove(k)
            return None, f"; the user says the {o} is on {k}"
        looks = self.options.query("looks_made", r, o, default=len(r.searched)) + len((getattr(r, "second_looks", {}) or {}).get(o, []))
        total = self._search_total(r, o)
        if self.cfg.get("search_legs") and not nowhere and (
                self._second_look(r, o, only_if_beats=True) if looks < total - self._relook_budget(r) else
                self._second_look(r, o)):
            # the last looks of the search go to the most likely places again, before the unlikely rest (F6.3)
            yield r.ev("recover", at_index=r.i, kind="second_look", msg=f"looking again for the {o}")
            return _NEXT, ""
        # with search legs the search ends when the places run out or `search_total` looks were made, not after
        # a fixed count of misses while likely places remain (the miss count is the pre-v2 stop)
        stop = nowhere or looks >= total or self._search_over(r, o) if self.cfg.get("search_legs") else (
            r.find_fails[o] > self.cfg["search_max"] + 1 or nowhere)
        if stop:
            if nowhere and self._second_look(r, o):
                return _NEXT, ""
            yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})")
            return _STOP, ""
        near = f" (it was seen near {r.seen_far[-1]})" if r.seen_far else ""
        if rule.action is Recovery.REAPPROACH:
            return None, f"; it is out of reach from {here}{near}"
        if not rule.mark_searched:
            # could not look: no evidence that it is not here (the replan reason is worded by code)
            return None, f"; the {o} could not be checked at {here} ({code}): it may still be there{near}"
        if leg is not None and self.cfg.get("search_legs") and self._splice_leg(r, leg):
            # the belief's next look replaces the failed Find in place: no replan call (SAGE-v2 F6.1)
            yield r.ev("recover", at_index=r.i, kind="search_leg", msg=f"looking for the {o} at {leg.text()}")
            return _NEXT, ""
        nxt = (f"Look next at {leg.text()}" if leg is not None else
               "Choose where to look next among the places not yet searched")
        return None, (f"; it is not at {world.arrived}. {nxt}"
                      + (f" (it was seen near {r.seen_far[-1]})" if r.seen_far else ""))

    def _relook_why(self, r, o, here) -> str:
        """Why ``here`` deserves one more look before it counts as searched (option ``relook``), else "": the person named it
        (a goal's source, an answer to a where-question), the object memory holds the object here, or it is the last place
        the belief has left (marking it searched would end the search)."""
        for g in r.goals or []:
            if g.get("object") == o and (g.get("from") == here or here in (g.get("from_any") or [])):
                return "named"
        if (o, here) in getattr(r, "told", ()):
            return "named"
        if r.memory.where(o) == here:
            return "memory"
        bel = getattr(r, "search", None)
        if bel is not None:
            op = set(r.world.opened)
            if not any(p != here and not bel.is_searched(o, p, op) and bel.leg_for(o, p, op) is not None for p in bel.places):
                return "last"
        return ""

    def _relook_here(self, r, o, here):
        """Option ``relook`` (default off): a Find that saw nothing at a place the person named, that memory says holds the
        object, or that is the last likely one gets ONE more look by another sense before the place counts as searched: a
        container believed open is closed and opened again, else the pack's declared wait action runs first, else the look is
        simply made again. Once per object and place; the looks count against ``search_total``; the skills are the pack's own
        (Close / Open, planner.recovery.wait), none declared = a plain second look. Returns (_NEXT, "") or None."""
        done_ = r.__dict__.setdefault("relook_done", set())
        if (o, here) in done_:
            return None
        why = self._relook_why(r, o, here)
        if not why:
            return None
        looks = self.options.query("looks_made", r, o, default=len(r.searched)) + len((getattr(r, "second_looks", {}) or {}).get(o, [])) + len(done_)
        if looks + 1 >= self._search_total(r, o):
            return None
        acts = {a.lower() for a in r.robot.plan_actions}
        close_, open_ = self._role("close"), self._role("open")      # the pack's actions; none declared = no reopen
        sense, pre = "again", []
        if (r.places.openable(here) and here in r.world.opened and not r.world.holding
                and close_ is not None and open_ is not None):
            sense, pre = "reopen", [{"action": close_, "object": here, "grounded": True},
                                    {"action": open_, "object": here, "grounded": True}]
        else:
            act, sched = exec_integrity.wait_schedule((self._manifest_planner("recovery", None) or {}).get("wait"))
            if act and act.lower() in acts:
                sense, pre = "wait", [{"action": act, "object": f"{sched[0]:g}"}]
        done_.add((o, here))
        if pre:
            r.steps[r.i:r.i] = pre
        self._lab("relook", object=o, place=here, why=why, sense=sense)
        yield r.ev("recover", at_index=r.i, kind="relook", msg=f"the {o} may still be at {here}: looking once more ({sense})")
        return _NEXT, ""

    SECOND_LOOKS = 2       # without a belief: the two most likely places, once each (the default rate 0.1)

    def _relook_budget(self, r) -> int:
        """Places that may get a second look: from the detector's false-negative rate (SearchBelief.relook_budget)."""
        bel = getattr(r, "search", None)
        return bel.relook_budget() if bel is not None else self.SECOND_LOOKS

    def _second_look(self, r, o, only_if_beats: bool = False) -> bool:
        """F6.3: one look is not proof of absence (an object detector misses a share of its looks, the rate is the
        robot's own: ``planner.detector_miss_rate``). Look once more at the place the belief's posterior (prior x
        miss rate per miss) makes most probable among those already searched, up to the rate's budget, never the
        place the robot is at, spliced in place of the failed Find. ``only_if_beats``: only while that place is more
        probable than every place not looked at yet (a strong sighting against a flat rest), before the search
        is used up. False when done."""
        bel = getattr(r, "search", None)
        if bel is None or not self.cfg.get("search_legs"):
            return False
        again = r.__dict__.setdefault("second_looks", {}).setdefault(o, [])
        if len(again) >= self._relook_budget(r):
            return False
        opened = set(r.world.opened)
        if only_if_beats:
            beat = bel.relook_beats_next(o, r.world.arrived, opened)
            cands = [beat.place] if beat is not None and beat.place in r.searched else []
        else:
            cands = [p for p in bel.relook_order(o, exclude=[r.world.arrived]) if p in r.searched]
            cands += [p for p in r.searched if p not in cands]
        for p in cands:
            if p in again or p == r.world.arrived:
                continue
            leg = bel.leg_for(o, p, opened)
            if leg is None:
                continue
            bel.unsearch(o, p)
            if self._splice_leg(r, leg):
                again.append(p)
                r.searched.remove(p)
                return True
            bel.missed(o, p, opened, count=False)
            return False
        return False

    def _splice_leg(self, r, leg) -> bool:
        """Put the search leg's steps (MoveTo, Open when closed, Find) in place of the failed Find at r.i, when
        the rest of the plan still runs from there by the action model; False (the planner replans) else."""
        new = self._ground_all(r, leg.steps(r.world.arrived) + [dict(x) for x in r.steps[r.i + 1:]])
        w, caps = r.world.copy(), self.robot.model_capabilities()
        w.found = None
        for st in new:
            errs = [v for v in action_model.check(st, w, capabilities=caps, actions=self.robot.plan_actions,
                                                  openable=r.places.openable) if v.is_error]
            if errs:
                return False
            action_model.apply(st, w, inplace=True, capabilities=caps)
        r.steps[r.i:] = new
        return True

    @staticmethod
    def _said_of(r, obj) -> str:
        """The person's own words for ``obj`` (the understood goal's ``said``, F4.5), else the object name."""
        return next((g.get("said") for g in getattr(r, "goals", []) or [] if g.get("object") == obj and g.get("said")),
                    obj)

    def _confirm_near(self, r, ans, typo=False):
        """The one place an answer with ASR noise nearly names (understand.fuzzy_place), confirmed with a yes /
        no question; None."""
        from .understand import fuzzy_place
        k, _n = fuzzy_place(r.places, ans, typo=typo)
        if not k:
            return None
        q = speech.LocalText("q_did_you_mean", {"object": self._say_name(r.places, k), "place": k})
        yield r.ev("clarify", question=q, round=0, kind="did_you_mean")
        _a, ok_, _rest = self._ask_yes_no(r, q)
        return k if ok_ is True else None

    def _search_on(self, r, o):
        """No place from the person: the search goes on over the likely places not searched yet (up to
        ``search_total`` looks in all), else the run ends naming the places looked at (F6.2)."""
        leg = r.search.next_leg(o, r.world.arrived, r.world.opened) if getattr(r, "search", None) is not None else None
        looks = self.options.query("looks_made", r, o, default=len(r.searched))
        looks += len((getattr(r, "second_looks", {}) or {}).get(o, []))
        if (leg is None or looks >= self._search_total(r, o) - self._relook_budget(r)) and self._second_look(r, o):
            yield r.ev("recover", at_index=r.i, kind="second_look", msg=f"looking again for the {o}")
            return _NEXT, ""
        if leg is None or looks >= self._search_total(r, o) or self._search_over(r, o):
            named = ", ".join(self._say_name(r.places, k) for k in r.searched) or "nowhere"
            yield self._finish(r, "failed", msg=f"could not find the {o} (looked at {', '.join(r.searched)})",
                               say=f"I couldn't find the {o}. I looked at: {named}.")
            return _STOP, ""
        r.find_fails[o] = 0                                     # the belief's next legs, without the person
        if self._splice_leg(r, leg):
            yield r.ev("recover", at_index=r.i, kind="search_leg", msg=f"looking for the {o} at {leg.text()}")
            return _NEXT, ""
        return None, f"; it is not at {r.world.arrived}. Look next at {leg.text()}"

    def _recover_phantom(self, r, st, reason, rule):
        """A Pick found nothing at the place where a Find had just seen the object (failure code ABSENT). The
        detector's false-positive rate says this happens, so it is not a grasp failure. The first time at a place
        the robot looks once more in place (a detection at the pick can miss a real object, the false-negative
        rate); the second time the Find counts as a false positive: the search state from before it is back
        and the place is a miss, so the search goes on over the other places, asks where it is, or gives up as
        for any Find that saw nothing."""
        o, world = st["object"], r.world
        here = world.arrived
        n = r.__dict__.setdefault("phantoms", {})
        n[(o, here)] = n.get((o, here), 0) + 1
        world.found = None; r.seen_at.pop(o, None)
        find = {"action": "Find", "object": o}
        r.steps[r.i:r.i] = [find]
        if n[(o, here)] == 1:
            yield r.ev("recover", at_index=r.i, kind="relook", msg=f"the {o} was not there to pick up; looking again")
            return _NEXT, ""
        r.search.restore_search(o)
        yield r.ev("recover", at_index=r.i, kind="phantom", msg=f"the {o} is not at {here}: it was a false detection")
        return (yield from self._recover_find(r, find, reason, rule))
