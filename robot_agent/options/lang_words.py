"""Option ``lang_words`` (default off): the clause words of a language (conditions, negations) are read from the language pack.

The failure it closes: the words that open a condition ("if the drawer is open") or a negation ("don't touch the mug") sat in the engine as
English regexes (``understand._COND``, ``_NEG``), so a reading in another language took no clause out and a request such as "put the cup away,
and if the drawer is open, close it" made the condition's object a goal in every language but one. With the option on, ``on_start`` gives the
run's ``Places`` the compiled ``patterns.condition`` and ``patterns.negation`` of the run's language pack (and of the prompt language's, whose
reading of a translated answer is read too); ``understand._active`` (and so the object coverage, the destination repair and the negation filter)
reads them in place of its own. The recipient words and the correction markers were already pack data (``words.recipient``, ``dialogue_ext.
self_correction``); this option adds the other two kinds, so no clause word is left in code.

Pack data (``lang/<code>.json``, ``patterns``): ``condition`` and ``negation``, each a regex string or a list of them (a clause runs to the
clause's end). A language whose pack declares neither, or an option on a run with no such pack: nothing changes, the engine's own reading
stands (fail open). A pack that declares one kind takes nothing out for the other. Hook ``on_start`` (sets ``run.places.clauses``).
Keys: ``enabled`` (legacy ``lang_words``). No lab flag: the run's ``lab_log`` is cleared after ``on_start``; read ``places.clauses``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import clauses

SPEC = OptionSpec(
    name="lang_words",
    keys=(Key("enabled", "bool", False, aliases=("lang_words",)),),
    hooks=("on_start",),
    order=60,
    enabled="enabled",
    doc="Condition and negation words are read from the language pack (patterns.condition / negation) in place of the engine's English regexes; no data = unchanged.",
)


class LangWords(Option):
    spec = SPEC

    def on_start(self, run) -> None:
        """Give the run's places the clause words of its language (nothing when no pack declares them)."""
        places = getattr(run, "places", None)
        cl = clauses.build(getattr(run, "lang", None))
        if places is None or cl is None:
            return
        places.clauses = cl


register(SPEC, LangWords)
