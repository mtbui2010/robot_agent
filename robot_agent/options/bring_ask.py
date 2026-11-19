"""Option ``bring_ask`` (default ON): a request to bring something to the speaker ("bring me the fork", "bring it to me") asks ONE
combined question, "Where are you? Or where should I put it?" (pack i18n key ``q_where_bring``), also when the robot tracks the person.

The answer is read as the engine reads the answer to "Where are you?" (``persons.place_in_answer``): a place where they are ("I'm on the
sofa") puts the object on the support surface nearest to it; a place to put it ("on the table") puts it there; no place ("hand it to me",
"here") is a hand-over. Asked once per request: after it, an answer with no place is a hand-over, never the same question again.

Not asked when the words already ask for a hand-over ("hand me the fork"), when the request names a place other than the object's
source (taken as the person's place), when the destination is not the speaker, or when the goal is already a hand-over. Generic: no word
of a language and no place in the code. Service ``person_where`` (used by the grounding of a goal to a person). Key ``enabled``
(``$ROBOT_AGENT_BRING_ASK``).
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import persons

SPEC = OptionSpec(
    name="bring_ask",
    keys=(Key("enabled", "bool", True, aliases=("bring_ask",)),),
    hooks=("on_start",),
    services=("person_where",),
    order=75,
    enabled="enabled",
    doc="A request to bring something to the speaker asks one question: where are you, or where should it be put.",
)


class BringAsk(Option):
    def on_start(self, run) -> None:
        """No state: the option answers the service ``person_where``; the registry asks every option to declare a hook."""

    def person_where(self, places, g, to, task, asked, latest):
        """For a goal ``g`` to the speaker: the phrase key of the question to ask ("q_where_bring"), "hand" once it was asked and the
        answer names no place, "" (None) when the engine's own order stands."""
        if g.get("type") != "on" or g.get("hand") or not to or to != persons.speaker_key(places):
            return None
        if persons.wants_hand(task, self.svc.robot):
            return None
        if asked == to:                                    # asked already: the task now holds the answer too, which is no "named in the request"
            return "hand" if latest else "q_where_bring"
        named = persons.place_in_answer(places, task)
        if named and named != g.get("from"):
            return None                                    # the words name the person's place
        return "q_where_bring"


register(SPEC, BringAsk)
