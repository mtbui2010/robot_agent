"""Option ``question_cap`` (default off): a cap on the questions of one task, and no question asked twice.

What: the engine asks the service ``ask_veto(run, question)`` before it puts a question that an option asks through the engine services
``ask`` (the options' one way to ask) and ``ask_clarify`` (an option's question as the loop's clarify step). This option vetoes the question
("cap") when the run has already put ``max_questions`` questions (the engine's one count of every question it put to the person: clarify
rounds, read-backs, where-is and search help, which-one, did-you-mean, every option question; service ``questions_put``), and ("repeat") when the same question, normalised (case, punctuation, spacing), was already put
in this run (through this service, or as a clarify question in the run's dialogue). A vetoed question is not asked: ``ask`` gives no answer
(''), so the option acts on what it has; ``ask_clarify`` goes on reading. The engine's own questions are not vetoed (they keep their own caps),
but they count.
Why: the failure class "the robot keeps asking": every option bounds only its own questions, so a task could get one question from each
of several options and the same question again (the user's rule: at most 3 questions per task, never the same question twice).
Switch off: key ``enabled`` False (the default; legacy run option ``question_cap``). Data: ``max_questions`` (int, per robot, manifest
``planner.options.question_cap.max_questions`` beside ``source`` or ``assumed: true``, read again at run time; 0, none declared or no label = no cap) and ``no_repeat`` (bool, legacy run
option ``question_cap_no_repeat``). Nothing declared = the option vetoes nothing. Hook ``on_start`` (the task's record starts empty). Lab flag ``question_cap``: ``{why: cap | repeat, asked}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import pack_data

SPEC = OptionSpec(
    name="question_cap",
    keys=(Key("enabled", "bool", False, aliases=("question_cap",)), Key("max_questions", "int", 0),
          Key("no_repeat", "bool", False, aliases=("question_cap_no_repeat",)), Key("source", "str", ""), Key("assumed", "bool", False)),
    hooks=("on_start",),
    order=96,
    enabled="enabled",
    services=("ask_veto",),
    doc="At most max_questions questions per task (every question the engine put counts) and never the same normalised question twice.",
)


def normalised(question) -> str:
    """``question`` as one comparable text: case folded, punctuation dropped, spaces collapsed (no word of any language)."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(question or "").casefold()).split())


def asked_count(run, svc=None) -> int:
    """The questions the run has put so far: the engine's one count of every question (service ``questions_put``), or, on a host without
    it, the run's own counters (clarify rounds and option questions ``asked``, read-backs ``confirms``)."""
    put = getattr(svc, "questions_put", None)
    if callable(put):
        return int(put(run) or 0)
    return int(getattr(run, "asked", 0) or 0) + int(getattr(run, "confirms", 0) or 0)


class QuestionCap(Option):
    def on_start(self, run) -> None:
        """The task's record of questions starts empty."""
        run.__dict__.setdefault("opt", {})[SPEC.name] = {"asked": set()}

    def _cap(self) -> int:
        """The declared cap as it is now (read again: a manifest overlay merged after the loop was built is seen); 0 = no cap. Used only when
        labelled (``source`` / ``assumed`` beside it, or as run options): an unlabelled cap is no cap."""
        try:
            return max(0, int(pack_data.labelled_keys(SPEC, self.cfg, pack_data.planner_of(self.svc), ("max_questions",))["max_questions"] or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _seen(run) -> set:
        """The normalised questions this option let through in ``run`` (per-run state of the option)."""
        return run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {}).setdefault("asked", set())

    def ask_veto(self, run, question):
        """Service: "cap" or "repeat" when ``question`` must not be asked now; None (and the question is remembered as asked) otherwise."""
        cap, text = self._cap(), normalised(question)
        said = {normalised(t) for w, t in getattr(run, "dialogue", None) or () if w == "robot"}
        n = asked_count(run, self.svc)
        why = "cap" if cap and n >= cap else \
            "repeat" if self.cfg.get("no_repeat") and text and (text in self._seen(run) or text in said) else None
        if why:
            self.svc.lab(SPEC.name, why=why, asked=n)
            return why
        self._seen(run).add(text)
        return None


register(SPEC, QuestionCap)
