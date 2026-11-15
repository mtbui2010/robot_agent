"""Option ``fault_ladder`` (default off): the end of a run that a device fault stops, once the wait and the tries are used up.

What: hook ``on_fault_stop`` (the recovery policy is about to stop the run on a failed step). For a fault of the kinds ``hw_fault`` and
``perception_unavailable`` only (e-stop, cancel and software faults are never retried and never reach the hook) it (1) asks the person for
help ONCE per run, naming the device: the device the skill result reported (its declared name when the pack names it), else the pack's
device of the failed skill; no device = no question (engine service ``ask``: the question counts like every other one, ``question_cap``
may veto it; an "I don't know" answer is not asked again); (2) runs the failed step ONCE per run by the pack's declared other skill for it,
only while the robot's state is known: health reads ``ok`` True, and for a hand step (its skill reads or changes ``holding`` /
``hand_empty``) the holding sensor gives a reading that fits the belief; no other skill declared = no retry; (3) else stops the run with
the language pack's sentence ``say_fault_stop`` naming the device, the attempts and the steps done. Health is read only as the contract's
``ok`` field: anything but True is unhealthy, no other field of the reading is read or said.
Why (classes hw_persistent, camera_persistent): a device that stayed broken ended the run with "something is wrong with my hardware": the
person was never asked for help and did not learn which device failed, how often the robot tried, or what was done.
The waiting and the tries are NOT here: option ``hw_wait`` (and its key ``more_tries``) owns them; this option acts only when they are used up.
How to switch it off: key ``enabled`` False (the default; legacy run option ``fault_ladder``). No labelled pack data = it does nothing.

Pack data ``planner.options.fault_ladder`` (each block labelled ``source`` or ``assumed: true``, read again at run time)::

    {"devices": {"<skill or reported device>": "<name>" | {"<lang>": "<name>"}, "source": "<label>"},
     "alternatives": {"<skill>": "<another skill of the robot that does the same step>", "assumed": true}}

Lab flag ``fault_ladder``: ``{part: ask | alternative | stop, ...}``.
"""

from __future__ import annotations

from robot_agent.contract import FailureCode
from robot_agent.core.options import NEXT, STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import speech
from robot_agent.core.planning.pack_data import fresh, labelled, planner_of

ASK, SAY = "q_fault_help", "say_fault_stop"
KINDS = (FailureCode.HW_FAULT, FailureCode.PERCEPTION_UNAVAILABLE)
HAND = ("hand_empty", "holding(")            # the contract's hand predicates: a skill that reads or changes them is a hand step
_LABELS = ("source", "assumed", "note")

SPEC = OptionSpec(
    name="fault_ladder",
    keys=(Key("enabled", "bool", False, aliases=("fault_ladder",)), Key("devices", "dict", {}, sources=("manifest",)),
          Key("alternatives", "dict", {}, sources=("manifest",))),
    hooks=("on_start", "on_fault_stop"),
    order=91,
    enabled="enabled",
    doc="A device fault that stops the run (after hw_wait's tries): one help question naming the device, one retry by a declared other skill "
        "while the state is known, then a stop that names the device, the attempts and the steps done.",
)


def entries(block) -> dict:
    """The entries of a labelled data block (its label fields dropped); {} when it is no mapping or has no label."""
    if not labelled(block):
        return {}
    return {k: v for k, v in block.items() if k not in _LABELS}


def name_in(value, lang: str):
    """A device's name in ``lang``: a plain string for every language, else the language's entry, else the English one; None."""
    v = value.get(lang) or value.get("en") if isinstance(value, dict) else value
    return v.strip() if isinstance(v, str) and v.strip() else None


def phrase_text(robot, key: str, lang: str, **slots):
    """The pack's phrase ``key`` as a LocalText with ``slots`` (English text for the log), or None when no pack has it."""
    tpl = speech.phrase(robot, key, "en") or speech.phrase(robot, key, lang)
    if not tpl:
        return None
    try:
        return speech.LocalText(key, slots, text=tpl.format(**slots))
    except (KeyError, IndexError, ValueError):
        return None


class FaultLadder(Option):
    spec = SPEC

    def on_start(self, run) -> None:
        """Per run: no question asked, no other skill tried."""
        run.__dict__.setdefault("opt", {})[SPEC.name] = {"asked": False, "alt": False}

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {"asked": False, "alt": False})

    def _block(self, name) -> dict:
        return entries(fresh(SPEC, self.cfg, planner_of(self.svc), name))

    def on_fault_stop(self, run, st, skill, code, device, tries):
        """Flow: NEXT (the other skill runs the step), STOP (the run ended with the sentence), None (the engine's own stop)."""
        devices, alts, code = self._block("devices"), self._block("alternatives"), FailureCode.try_parse(code)
        if code not in KINDS or not (devices or alts):
            return None
        s = self._state(run)
        name = s["named"] = self._device(run, skill, device, devices) or (s.get("named") if st is s.get("alt_step") else None)
        if name and not s["asked"]:
            s["asked"] = True
            out = yield from self._ask(run, name)
            if out is not None:
                return out
        if self._retry(run, st, skill, alts.get(skill)):
            yield run.ev("recover", at_index=run.i, kind=SPEC.name, msg=f"{skill} failed ({code.value}); one more try by {alts[skill]}")
            return NEXT
        return (yield from self._stop(run, code, device, name, tries)) if name else None

    def _device(self, run, skill, device, devices):
        """The device to name: the reported one (its declared name, else as reported), else the declared device of ``skill``; None. (A step
        run by the other skill keeps the device named for it.)"""
        lang = (getattr(run, "lang", None) or "en")[:2]
        if device and device != skill:
            return name_in(devices.get(device), lang) or str(device).replace("_", " ")
        return name_in(devices.get(skill), lang)

    def _ask(self, run, name):
        """Flow: the one help question (STOP when the run was cancelled meanwhile, else None)."""
        q = phrase_text(self.svc.robot, ASK, getattr(run, "lang", None) or "en", device=name)
        if q is None:
            return None
        before = getattr(run, "asked", 0)
        ans = self.svc.ask(run, q) or ""
        if getattr(run, "asked", 0) == before:                  # vetoed (question_cap): nothing was asked
            self.svc.lab(SPEC.name, part="ask", device=name, asked=False)
            return None
        yield run.ev("clarify", question=q, round=run.asked, kind=SPEC.name)
        unsure = bool(ans) and self.svc.unsure_leftover(run, ans) is not None
        self.svc.lab(SPEC.name, part="ask", device=name, asked=True, answer="unsure" if unsure else "heard" if ans else "silent")
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        return None

    def _retry(self, run, st, skill, alt) -> bool:
        """Put the declared other skill in place of the failed step, once per run, when the robot's state is known. True when it did."""
        s, knows = self._state(run), getattr(self.svc.robot, "knows_skill", None)
        if not isinstance(alt, str) or s["alt"] or not callable(knows) or not knows(alt) or not self._known(run, st):
            return False
        if not (run.i < len(run.steps) and run.steps[run.i] is st):
            return False
        s["alt"] = True
        run.steps[run.i] = s["alt_step"] = {**st, "skill": alt, "params": dict(st.get("params") or {"inputs": st.get("object")})}
        self.svc.lab(SPEC.name, part="alternative", skill=skill, other=alt)
        return True

    def _known(self, run, st) -> bool:
        """Health reads ok True; for a hand step the holding sensor also gives a reading that agrees with the belief."""
        try:
            healthy = self.svc.robot.health(getattr(run, "node", None)).get("ok") is True
        except Exception:                                       # noqa: BLE001 - an unreadable health is no known state
            return False
        if not healthy or not self._hand_step(st.get("action")):
            return healthy
        now = self.svc.hand_holds(run)
        return now is not None and now == bool(getattr(run.world, "holding", None))

    def _hand_step(self, action) -> bool:
        """Does the step's skill read or change the hand (its declared pre / effects)? An undeclared skill counts as one."""
        spec = getattr(self.svc.robot, "spec", None)
        sk = spec(action) if callable(spec) else None
        if sk is None:
            return True
        return any(h in str(w).replace(" ", "") for w in (*(sk.pre or ()), *(sk.effects or ())) for h in HAND)

    def _stop(self, run, code, device, name, tries):
        """Flow: the run ends ``failed`` with the sentence naming the device, the attempts and the steps done (None: no sentence)."""
        done = len(getattr(run, "completed", None) or ())
        say = phrase_text(self.svc.robot, SAY, getattr(run, "lang", None) or "en", device=name, tries=tries, done=done)
        if say is None:
            return None
        self.svc.lab(SPEC.name, part="stop", device=name, tries=tries, done=done)
        yield self.svc.finish(run, "failed", code=code, device=device, say=say,
                              msg=f"{SPEC.name}: {code.value} of {name}, {tries} tries, {done} steps done")
        return STOP


register(SPEC, FaultLadder)
