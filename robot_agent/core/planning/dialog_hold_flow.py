"""The AssistLoop side of ``dialog_hold`` (round 18; default OFF, off = the earlier behaviour byte for byte).

``HoldFlow`` is a mixin of ``AssistLoop``. Every method starts with the option check, so an off run never reaches the pure
helpers of ``dialog_hold``. The option is ``planner.dialog_hold`` ({hold, one_part, max_options}) or the run option
``dialog_hold`` ("1" both parts, "hold" the ledger only); ``noun_relabel`` is a separate flag.

* H1, the ledger (``_hold_read``): every later reading of the model is merged into the part ledger of the run.
* H2, one part at a time (``_hold_parts``): a bare "no", "not sure" or not-understood answer to a read-back does not ask "what
  should I do?" and understand the task again. The reading is kept; ONE open part is read back with the existing read-back
  template; a content-free answer to that gets ONE open question (existing templates) about its least anchored slot; a part
  that stays content-free is not executed (honest stop). The no-repeat gate (``_hold_gate``) never asks a (template, slots)
  again that got "not sure". "I do not know where X" with a Find skill drops the source of X (the robot searches).
* Wild card ``noun_relabel`` (``_hold_relabel``): her object span, verbatim in her answer, becomes the part's ``said``, and
  the English label is the translator's one cached call for it. A failed or untrusted call changes nothing.
"""

from __future__ import annotations

import re

from . import dialog_converge, dialog_ext, dialog_hold as DH, dialog_unstick, langdata, speech
from .loop import _DONE, _NEXT, _STOP


class HoldFlow:
    # ── option ────────────────────────────────────────────────────────────────────────────────────────────────────────
    def _hold_on(self) -> bool:
        return bool(self.cfg.get("dialog_hold"))

    def _hold_one(self) -> bool:
        o = self.cfg.get("dialog_hold")
        return bool(o and o.get("one_part"))

    def _hold_max(self) -> int:
        return int((self.cfg.get("dialog_hold") or {}).get("max_options") or DH.DEFAULT_MAX_OPTIONS)

    # ── her words ─────────────────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _her_turns(r) -> list:
        return [t for w_, t in r.dialogue if w_ == "user"]

    def _her_texts(self, turns) -> list:
        out = []
        for u in turns:
            out += [x for x in self._texts_of(u) if x]
            out += self._answer_english(u)
        return out

    def _answer_english(self, u) -> list:
        """The trusted English of what she said after her yes / no word (the key `_correction_texts` looked up: the correction
        as `yes_no` splits it), with a leading yes / no word of the packs taken off. [] when the language layer holds none."""
        heard = getattr(getattr(self, "language", None), "heard", None)
        if not heard or not u:
            return []
        _ok, rest = self.yes_no(u)
        out = []
        for key in dict.fromkeys(x for x in (str(rest or "").strip(), str(u).strip()) if x):
            h = heard.get(key)
            en = DH.strip_lead_word(getattr(h, "english", "")) if h is not None and getattr(h, "trusted", False) else ""
            if en and en not in out:
                out.append(en)
        return out

    def _task_texts(self, r) -> list:
        return [x for t in (r.task, self.original or "") if t for x in self._texts_of(t) if x]

    def _all_texts(self, r) -> list:
        return self._task_texts(r) + self._her_texts(self._her_turns(r))

    def _hold_led(self, r):
        led = r.__dict__.get("hold")
        if led is None and r.goals:
            led = r.hold = DH.Ledger(r.goals, seen=len(self._her_turns(r)))
        elif led is not None and r.goals and not led.active and [g.get("type") for g in r.goals] == [
                p["goal"].get("type") for p in led.parts]:
            led.sync(r.goals)
        return led

    def _hold_guard(self, r):
        if not self.cfg.get("dialog_agree2"):
            return None
        return dialog_ext.guard_for(r.lang, r.places, r.goals, getattr(self.robot.manifest, "planner", None), r.places.ext)

    # ── H1: merge a later reading ─────────────────────────────────────────────────────────────────────────────────────
    def _hold_read(self, r, q):
        """After the grounding of a reading: merge it into the ledger. Returns the question (a conflict question when two of
        her statements conflict and neither is superseded)."""
        if not self._hold_on() or not r.goals:
            return q
        led = r.__dict__.get("hold")
        turns = self._her_turns(r)
        if led is None or not led.parts:
            r.hold = DH.Ledger(r.goals, seen=len(turns))
            return q
        new = self._her_texts(turns[led.seen:])
        earlier = self._task_texts(r) + self._her_texts(turns[:led.seen])
        asked = self._questions_since(r, led.seen)
        led.seen = len(turns)
        sup, led.supersede = set(led.supersede), set()
        for x in asked:                                    # she answered a question about these slots: her answer supersedes
            for s_ in DH.asked_slots(x):                   # ("*" itself is kept for a refusal and restatement)
                sup |= {(i, t) for i in range(len(led.parts)) for t in (DH.GROUPS if s_ == "*" else (s_,))}
        _g, info = DH.merge(led, [dict(g) for g in r.goals], new, earlier, r.places, r.lang, sup)
        rm = info.get("remap", {})
        for i in [i for i, _s in info["changed"]] + list(info.get("added_idx", ())):
            if rm.get(i) is not None:                      # a part her yes did not cover in this form: read it back again
                led.parts[rm[i]]["changed_since_yes"] = True
        yield from self._hold_answered(r, led, new, rm, {(rm.get(i), s_) for i, s_ in info["changed"]})
        r.goals = led.goals()
        if led.active:
            r.allow_repeat = True                      # the refused-reading machinery does not apply to a held reading
        self._lab("dialog_hold", part="merge", changed=len(info["changed"]), kept=len(info["kept"]),
                  dropped=len(info["dropped"]), added=len(info["added"]), conflicts=len(info["conflicts"]))
        if info["conflicts"] and not q:
            c = info["conflicts"][0]
            led.conflict_asked = True
            return DH.conflict_question(r.places, c["slot"], c["old"], c["new"], c["goal_old"], self._hold_max())
        return q

    @staticmethod
    def _questions_since(r, seen) -> list:
        """The robot's questions asked after her ``seen``-th turn (the ones her new words answer)."""
        out, n = [], 0
        for who, t in r.dialogue:
            if who == "user":
                n += 1
            elif n >= seen:
                out.append(t)
        return out

    def _hold_answered(self, r, led, new, remap, changed=frozenset()):
        """Her answer to the open question about (part, slot). A value she names that the part does not have is applied as a
        content correction (`DH.content_edit`, the asked slot only); a value of the part's own that the merge just changed is not
        settled; the part stays OPEN and is read back (a one-part read-back follows). Only a not-sure, a garbled answer or no
        named value marks the part ``empty``. Else a value of the part's own that she names settles the slot."""
        ask, led.asked_slot = led.asked_slot, None
        j = remap.get(ask[0]) if ask else None
        if j is None:
            return
        p, slot, ans = led.parts[j], ask[1], (self._her_turns(r) or [""])[-1]
        guard = self._hold_guard(r)
        if not new or (guard is not None and guard.garbled(new)) or self._hold_unsure(r, ans):
            p["empty"] = True                              # nothing said, a garble or a not-sure: settles nothing
            return
        g = p["goal"]
        if DH.slot_score(g, slot, new, r.places, r.lang) > 0:
            if (j, slot) in changed:                       # the merge changed this slot with her words: read it back first
                p["opened"] = True
            else:
                led.settle(j, [slot], "answer")
            return
        own = {g.get(k) for k in ("to", "from", "object")}
        foreign = DH.named_keys(new, r.places) - {"@speaker"} - own
        span = DH.object_span(ans, r.places, r.lang) if slot == "object" else None
        if foreign or (span and DH.object_score(g, [span], r.lang) == 0):
            edit = DH.content_edit(g, new, r.places, r.lang, [q["goal"] for k, q in enumerate(led.parts) if k != j])
            edit = {s_: v for s_, v in edit.items() if s_ == slot}
            if edit and (yield from self._hold_apply(r, ans, [(j, s_, v) for s_, v in edit.items()])):
                p["edits"] += 1
            p["opened"] = True                             # open: its one-part read-back follows
        elif not any(DH.slot_score(g, s_, new, r.places, r.lang) > 0 for s_ in DH.slots_of(g)):
            p["empty"] = True                              # names no slot of the part: content-free

    # ── H2: one part at a time ────────────────────────────────────────────────────────────────────────────────────────
    def _hold_repair(self, r):
        """Where `_read_back` met a bare "no" / "not sure" / not-understood answer. (outcome, question) like `_read_back`."""
        led = self._hold_led(r)
        if led is None:
            return None, speech.LocalText("q_what_do")
        led = self._hold_rebuild(r, led)
        led.active = True
        return (yield from self._hold_parts(r))

    def _hold_rebuild(self, r, led):
        """The ledger of exactly the parts that were just read back (``r.goals``). A part the read-back had keeps what the old
        ledger knew of it (settled slots, provenance, counters); a part it did not have is dropped: the robot never reads back
        one part of a reading it did not read back."""
        if not r.goals:
            return led
        new = DH.Ledger(r.goals, seen=led.seen)
        for k in ("conflict_asked", "failed", "asked_slot", "supersede"):
            setattr(new, k, getattr(led, k))
        for j, i in DH._match(led.parts, r.goals).items():
            old, p = led.parts[i], new.parts[j]
            p.update({k: old[k] for k in ("first", "edits", "opens", "opened", "changed_since_yes", "empty") if k in old})
            p["settled"], p["prov"] = set(old["settled"]), dict(old["prov"])
            new._status(p)
            if old["status"] == DH.REFUSED:
                p["status"] = DH.REFUSED
        r.hold = new
        return new

    def _hold_unverified(self, r) -> tuple:
        try:
            objs = set(self._unverified_objects(r))
        except Exception:                                              # noqa: BLE001 - no check = no ordering hint
            return ()
        return tuple(i for i, p in enumerate(r.hold.parts) if p["goal"].get("object") in objs)

    def _hold_stop(self, r, i=None):
        if i is not None:
            r.hold.refuse(i)
        self._lab("dialog_hold", part="stop", refused=i)
        if self.cfg.get("dialog_agree") and not self._agree_state(r)["tried"]:
            r.goals = r.hold.goals()                       # the stop is where s8 tries its agreed reading
            r.part_asked = True                            # (the parts were asked about one by one: its which-part question)
            out = yield from self._agreed_reading(r)
            if out is not None:
                return out, None
        yield self._finish(r, "failed", msg="the user did not confirm the task")
        return _STOP, None

    def _hold_parts(self, r):
        led = r.hold
        while True:
            texts = self._all_texts(r)
            i = led.next_open(texts, r.places, r.lang, self._hold_unverified(r))
            if i is None:
                if not led.all_settled():
                    return (yield from self._hold_stop(r))
                r.goals = led.goals()
                if not self._hold_all_yes(led):               # a slot settled other than by a yes to a read-back: read it back
                    return (yield from self._hold_whole(r))
                said = speech.LocalText.join(speech.LocalText("rb_restated"), self.summary(r.goals, r.places))
                self._lab("dialog_hold", part="all_settled")
                yield r.ev("understand_check", issues=["every part of the reading is settled"], correction="held", say=said,
                           goals=r.goals)
                return _DONE, None
            p = led.parts[i]
            if p["stage"] == 2:
                if p["empty"]:                                # the open question was answered without a value
                    return (yield from self._hold_stop(r, i))
                p["stage"], p["opened"] = 0, True             # answered: the part is read back once more
            if p["stage"] == 0 and (len(led.parts) > 1 or p.get("opened")):
                res = yield from self._hold_part_readback(r, i)
                if res == "next":
                    continue
                if res is not None:
                    return res
            if p.get("opens", 0) >= 2:                        # content-free to the read-back and to the open question
                return (yield from self._hold_stop(r, i))
            p["opens"] = p.get("opens", 0) + 1
            slot = DH.least_anchored_slot(p["goal"], texts, r.places, r.lang, p["settled"])
            q = DH.open_question(p["goal"], slot, r.places)
            p["stage"], led.asked_slot = 2, (i, slot)
            led.supersede.add((i, slot))
            self._lab("dialog_hold", part="open_question", slot=slot)
            r.goals = led.goals()
            return None, q

    def _hold_part_readback(self, r, i):
        """Read back part ``i`` alone (the existing template). "next": settled or edited, go on; a (outcome, question) pair:
        end of this call; None: content-free, ask the open question."""
        led = r.hold
        p = led.parts[i]
        p["stage"] = 1
        rb = self.summary([p["goal"]], r.places)
        cq = speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?")
        if DH.question_key(cq) in r.__dict__.get("hold_failed", ()):
            return None                                     # this read-back got a No with no edit: not asked again
        self._lab("dialog_hold", part="one_part_readback")
        yield r.ev("clarify", question=cq, round=r.confirms, kind="confirm", part=i)
        ans, ok_, rest = self._ask_yes_no(r, cq)
        stop = self._cancelled(r)
        if stop:
            yield stop
            return _STOP, None
        if ok_ is True:
            led.settle(i, None, "yes")
            return "next"
        if ok_ is None:
            yield self._finish(r, "failed", msg=f"no answer to: {cq}")
            return _STOP, None
        r.dialogue += [("robot", cq), ("user", ans)]
        if not rest or self._hold_unsure(r, ans):
            self._hold_fail(r, cq)
            return None
        r.corrected = getattr(r, "corrected", 0) + 1
        r.memory.remember_answer(r.task, cq, ans)
        texts = self._correction_texts(r, rest)
        if p["edits"] < 2:
            others = [q["goal"] for k, q in enumerate(led.parts) if k != i]
            edit = DH.content_edit(p["goal"], texts, r.places, r.lang, others)
            if edit and (yield from self._hold_apply(r, rest, [(i, s, v) for s, v in edit.items()])):
                p["edits"] += 1
                p["stage"] = 0
                return "next"
            if self.cfg.get("noun_relabel") and self._hold_relabel(r, i, rest):
                p["edits"] += 1
                p["stage"] = 0
                return "next"
            if not edit:
                named = DH.other_parts_named(p["goal"], texts, r.lang, others)
                if named:                                   # her words are about another part: this one is not refused
                    p["stage"] = 0
                    k = named[0] + (named[0] >= i)               # position among the other parts -> ledger index
                    res = yield from self._hold_named_part(r, i, k, rest, texts)
                    if res == "next":                           # her No brought an edit (of the other part)
                        return res
                    self._hold_fail(r, cq)
                    if res is not None:
                        return res
                    p["stage"] = 1
        self._hold_fail(r, cq)
        led.supersede |= {(i, s) for s in DH.slots_of(p["goal"])}
        return None

    def _hold_named_part(self, r, i, k, rest, texts):
        """Her No to the read-back of part ``i`` names part ``k``. A settled part her words agree with: the whole held reading
        is read back once (not the open question). Else her words correct part ``k`` (`DH.content_edit`). "next", a pair, or
        None (no edit: the earlier path)."""
        led = r.hold
        pk = led.parts[k]
        if pk["status"] == DH.SETTLED and DH.agrees_with(pk["goal"], texts, r.places, r.lang):
            return (yield from self._hold_whole(r))
        if pk["edits"] < 2:
            edit = DH.content_edit(pk["goal"], texts, r.places, r.lang, [q["goal"] for m, q in enumerate(led.parts) if m != k])
            if edit and (yield from self._hold_apply(r, rest, [(k, s, v) for s, v in edit.items()])):
                pk["edits"] += 1
                pk["stage"] = 0
                return "next"
        return None

    def _hold_fail(self, r, q) -> None:
        """A read-back that got a No with no edit is never asked again (the gate and `_hold_part_readback` read this)."""
        r.__dict__.setdefault("hold_failed", set()).add(DH.question_key(q))

    @staticmethod
    def _hold_all_yes(led) -> bool:
        """Is every slot of every part settled by a yes to a read-back, with no change since?"""
        return all(not p.get("changed_since_yes") and all(p["prov"].get(s) == "yes" for s in DH.slots_of(p["goal"]))
                   for p in led.parts)

    def _hold_whole(self, r):
        """One read-back of the whole held reading (the existing template). Yes: every part settles by a yes, _DONE. A content
        No: `_hold_whole_edit`, then the edited reading is read back. Anything else: the stop."""
        led = r.hold
        n = r.__dict__.get("hold_wholes", 0)
        r.goals = led.goals()
        rb = self.summary(r.goals, r.places)
        cq = speech.LocalText("q_confirm", {"summary": rb}, text=f"{rb} Is that right?")
        if n >= 3 or DH.question_key(cq) in r.__dict__.get("hold_failed", ()):
            return (yield from self._hold_stop(r))
        r.hold_wholes = n + 1
        self._lab("dialog_hold", part="whole_readback")
        yield r.ev("clarify", question=cq, round=r.confirms, kind="confirm")
        ans, ok_, rest = self._ask_yes_no(r, cq)
        stop = self._cancelled(r)
        if stop:
            yield stop
            return _STOP, None
        if ok_ is True:
            led.settle_all("yes")
            return _DONE, None
        if ok_ is None:
            yield self._finish(r, "failed", msg=f"no answer to: {cq}")
            return _STOP, None
        r.dialogue += [("robot", cq), ("user", ans)]
        self._hold_fail(r, cq)
        if rest and not self._hold_unsure(r, ans):
            r.corrected = getattr(r, "corrected", 0) + 1
            r.memory.remember_answer(r.task, cq, ans)
            if (yield from self._hold_whole_edit(r, rest)):
                return _NEXT, None
        return (yield from self._hold_stop(r))

    def _hold_apply(self, r, rest, edits):
        """Apply ``[(part, slot, value)]`` to the reading: a destination or recipient through `_patch_goals` (grounded as any
        goal), a container of an open / close goal directly. True when applied."""
        led = r.hold
        goals = led.goals()
        r.goals = goals
        to_edits = [(i, v) for i, s, v in edits if s == "to"]
        if to_edits:
            patched = yield from self._patch_goals(r, rest, to_edits)
            if patched is None:
                return False
            goals = patched
        for i, s, v in edits:
            if s == "object":
                goals[i] = DH.apply_edit(goals[i], {"object": v})
        led.sync(goals)
        for i, _s, _v in edits:
            led.parts[i]["changed_since_yes"] = True        # an edit is a change no yes has covered
        r.goals = led.goals()
        self._lab("dialog_hold", part="edit", slots=[s for _i, s, _v in edits])
        return True

    # ── a content "no" to the whole read-back ─────────────────────────────────────────────────────────────────────────
    def _hold_whole_edit(self, r, rest):
        """Correction of the whole read-back, beyond `dialog_converge.correction_edit`: recipient, open / close. True when the
        edited goals were set as the next read-back. Else her words supersede what she said before (the re-reading is merged)."""
        led = self._hold_led(r)
        if led is None:
            return False
        edits = DH.extended_edit(r.goals, self._correction_texts(r, rest), r.places, r.lang)
        if edits and (yield from self._hold_apply(r, rest, edits)):
            r.patched = led.goals()
            return True
        led.supersede.add("*")
        return False

    # ── no repeat ─────────────────────────────────────────────────────────────────────────────────────────────────────
    def _hold_gate(self, r, q):
        """(question, stop): a question already answered with "not sure" is not asked again. The other form of the same
        question (`q_unclear_task`) once; then the honest stop."""
        failed = r.__dict__.get("hold_failed")
        if not self._hold_one() or not failed or DH.question_key(q) not in failed:
            return q, False
        self._lab("dialog_hold", part="no_repeat")
        if not getattr(r, "reworded", False):
            r.reworded = True
            alt = speech.LocalText("q_unclear_task")
            if DH.question_key(alt) not in failed:
                return alt, False
        return q, True

    def _hold_unsure(self, r, ans) -> bool:
        """Is ``ans`` a "not sure" (the packs' phrases only)? Then it is a content-free answer, whatever else it says."""
        return dialog_unstick.unsure_answer(ans, r.places, self._unsure_data(r), r.lang, None, fallback_too=False)

    def _hold_note_answer(self, r, q, ans) -> None:
        """A "not sure" answer (the packs' phrases only) to ``q``: its (template, slots) is never asked again."""
        if self._hold_one() and self._hold_unsure(r, ans):            # part of H2 (one part at a time), not of the ledger
            r.__dict__.setdefault("hold_failed", set()).add(DH.question_key(q))

    # ── "I do not know where X": the robot searches ───────────────────────────────────────────────────────────────────
    def _hold_sense(self, r, u):
        """Replaces `_search_when_unsure` (option on, ``one_part``): after a not-sure answer to a where-question about X, with a
        Find skill and X a part of the held reading, the source of X is dropped and the reading goes on (no model call, no
        more where-questions about X). None: not applicable, the earlier path runs."""
        from . import goal_fix
        finds = [a for a in self.robot.plan_actions if a.lower() == "find"]
        led = r.__dict__.get("hold")
        objs = goal_fix.abstains_on_location(u) if finds and led is not None and r.dialogue and r.dialogue[-1][0] == "user" else None
        if not objs or not dialog_unstick.unsure_answer(r.dialogue[-1][1], r.places, self._unsure_data(r), r.lang, None):
            return None
        hit = [i for i, p in enumerate(led.parts) if str(p["goal"].get("object") or "").lower() in {str(o).lower() for o in objs}
               or str(p["goal"].get("said") or "").lower() in {str(o).lower() for o in objs}]
        if not hit:
            return None
        for i in hit:
            for k in ("from", "from_any"):
                led.parts[i]["goal"].pop(k, None)
            led.parts[i]["goal"]["from"] = ""
        r.unsure_reasked = True
        self._lab("dialog_hold", part="sense", objects=len(hit))
        return {"status": "clear", "reason": "not sure where it is: the robot searches", "goals": led.goals(), "question": None}

    # ── wild card: her noun names the object ──────────────────────────────────────────────────────────────────────────
    def _hold_relabel(self, r, i, rest) -> bool:
        """Option ``noun_relabel``: the object span of her answer (verbatim) is the part's ``said``; its English label is the
        translator's one cached call. False (nothing changed) when there is no single span, no trusted translation or the
        language has no script data."""
        lg = (r.lang or "en")[:2]
        if langdata.is_prompt_lang(lg):
            return False
        span = DH.object_span(rest, r.places, lg)
        led = r.hold
        g = led.parts[i]["goal"]
        if not span or g.get("type") not in ("on", "in") or DH.object_score({"said": span}, [g.get("said") or g.get("name_local") or ""], lg):
            return False
        try:
            h = self.language.hear(span, lg, pivot="translate")
        except Exception:                                              # noqa: BLE001 - fail open
            return False
        en = re.sub(r"^(the|a|an)\s+", "", str(getattr(h, "english", "") or "").strip().lower().rstrip("."))
        if not en or not getattr(h, "trusted", False) or not re.fullmatch(r"[^\W\d_]+(?: [^\W\d_]+){0,2}", en):
            return False
        g["said"], g["object"] = span, en
        g.pop("name_local", None)
        r.goals = led.goals()
        self._lab("dialog_hold", part="relabel")
        return True
