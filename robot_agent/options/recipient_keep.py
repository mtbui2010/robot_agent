"""Option ``recipient_keep`` (default off): a delivery's recipient is moved to the speaker only on evidence, never because a person is named once.

The engine's listener rule in ``goal_fix`` moves the recipient of a delivery to the speaker when that person is also the listener of a
message and the words name them once ("tell X to come, and bring me the cup"). The reader's recipient is often right: "bring him the cup, and
let him know" names the person once, the pronoun is not a mention, and the rule hands the object to the speaker (a false success).

With the option the hand-over goes to the speaker only when the words hold SPEAKER EVIDENCE AND the person is named once. Evidence is a match of
the language packs' recipient words (``lexicon.RECIPIENT``, plus the robot's ``recipient_words`` phrase) that is itself a first-person word
(``lexicon.FIRST_PERSON``: "me", "get me", a first-person pronoun with its case particle); a delivery verb or "here" alone is not evidence,
it says nothing about WHO gets the object. Otherwise
the reader's recipient stays. When the words hold such a word and also name the person twice, the two readings disagree: the person is asked
once ("I will hand it to you, or I will hand it to X") if the pack has the phrase data and no answer was given yet (key ``ask_when_unsure``).
No speaker-recipient data (an empty pack): the recipient always stays as the reader gave it, nothing is asked.

``on_start`` gives the run's places the rule (``places.listener_rule``, which that ``goal_fix`` rule runs instead of its own when it is set); the question
of the two readings is asked in ``on_goals``, before the read-back; the answer decides by the same evidence. Hooks ``on_start``, ``on_goals``. Keys:
``enabled`` (legacy ``recipient_keep``), ``ask_when_unsure`` (true). Lab flag ``recipient_keep``: ``{rule, person}``. No pack data of its own.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import lexicon

SPEC = OptionSpec(
    name="recipient_keep",
    keys=(Key("enabled", "bool", False, aliases=("recipient_keep",)), Key("ask_when_unsure", "bool", True)),
    hooks=("on_start", "on_goals"),
    order=88,
    enabled="enabled",
    doc="A delivery's recipient moves to the speaker only when the words hold a speaker-recipient word of the language pack; else the reader's recipient stays.",
)


def candidates(goals, places) -> list:
    """[(goal, person key)] of the deliveries to a person who is also a say goal's listener (not the speaker, not a frozen shared object)."""
    from robot_agent.core.planning.persons import speaker_key
    sk, guard = speaker_key(places), getattr(places, "guard", None)
    if not sk:
        return []
    listeners = {g.get("to") or g.get("person") for g in goals if g.get("type") == "say"} - {None, ""}
    return [(g, g["to"]) for g in goals if g.get("type") in ("on", "in") and g.get("to") in listeners and g["to"] != sk
            and (places.env.get(g["to"]) or {}).get("person") and not (guard and guard.frozen(g))]


def speaker_evidence(text: str, robot=None) -> bool:
    """Does the text hold a recipient word of the packs that is a first-person word (the speaker as the recipient)?"""
    import re
    t = " ".join(str(text or "").lower().split())
    first = lexicon.patterns(lexicon.FIRST_PERSON, robot)
    return any(re.search(f, m.group()) for p in lexicon.patterns(lexicon.RECIPIENT, robot, "recipient_words") for m in re.finditer(p, t) for f in first)


def mentions(text: str, person: str, places) -> int:
    """How many times the words name ``person`` (place mentions of their ENV entry; a pronoun is not one)."""
    from robot_agent.core.planning.understand import place_mentions
    return sum(1 for _s, _e, _p, keys, _src in place_mentions(text, places) if person in keys)


class RecipientKeep(Option):
    def on_start(self, run) -> None:
        """Give the run's places the evidence rule (it replaces the engine's own rule for a delivery to a message's listener)."""
        places = getattr(run, "places", None)
        if places is not None:
            run.recipient_conflicts = []
            places.listener_rule = lambda goals, text: self.decide(run, goals, text)

    def decide(self, run, goals, words):
        """(goals, notes): the evidence rule; the cases with two readings are kept for ``on_goals`` to ask."""
        from robot_agent.core.planning.persons import speaker_key
        places = run.places
        sk, speaker_word = speaker_key(places), speaker_evidence(words, getattr(self.svc, "robot", None))
        out, notes = list(goals), []
        for g, person in candidates(goals, places):
            n = mentions(words, person, places)
            move = speaker_word and n < 2
            self.svc.lab(SPEC.name, rule="evidence" if move else "keep", person=person)
            if move:
                out[next(j for j, x in enumerate(out) if x is g)] = {k: v for k, v in {**g, "to": sk}.items() if k != "near_person"}
                notes.append(f"recipient {person} -> {sk}: the words name {person} once, as the listener of a message, and a speaker-recipient word")
            elif speaker_word:
                run.recipient_conflicts.append((g.get("object"), person))
        return out, notes

    def _question(self, run, obj, person, sk):
        """The one question of two readings (the speaker, or the named person); None without the phrase machinery."""
        if not hasattr(self.svc, "goal_phrase"):
            return None
        from robot_agent.core.planning import speech
        a, b = (self.svc.goal_phrase({"type": "on", "object": obj, "from": "", "to": k}, run.places) for k in (sk, person))
        return speech.LocalText("q_which_reading", {"first": a, "second": b}, text=f"I can read that two ways: I will {a}, or I will {b}. Which one do you mean?",
                                ask={"intent": "choose", "options": [str(a), str(b)], "about": ""})

    def on_goals(self, run):
        """Ask once when the words say both a speaker-recipient word and the person twice (the readings disagree); the answer decides by evidence."""
        from robot_agent.core.planning.persons import speaker_key
        pending, run.recipient_conflicts = list(getattr(run, "recipient_conflicts", [])), []
        sk = speaker_key(run.places)
        if not pending or not sk or not self.cfg["ask_when_unsure"] or getattr(run, "dialogue", None):
            return None
        obj, person = pending[0]
        q = self._question(run, obj, person, sk)
        if q is None:
            return None
        yield run.ev("clarify", question=q, round=run.asked + 1, kind="recipient_keep")
        ans = self.svc.ask(run, q)
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        to_speaker = speaker_evidence(ans, getattr(self.svc, "robot", None))
        self.svc.lab(SPEC.name, rule="asked", person=person, speaker=to_speaker)
        if to_speaker:
            run.goals = [{**g, "to": sk} if g.get("type") in ("on", "in") and g.get("object") == obj and g.get("to") == person else g for g in run.goals]
        return None


register(SPEC, RecipientKeep)
