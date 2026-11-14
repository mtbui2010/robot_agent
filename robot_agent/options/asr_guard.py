"""Option ``asr_guard`` (default off): a request the recogniser lost is asked again, the reader is not called on nothing.

The failure it closes: the speech recogniser hands over an EMPTY request (silence, a cut) or one with a HOLE (an article or a possessive
directly followed by a function word or by the end: "bring me the to the table"; the noun was lost). The reader then invents a request, or an
either-or question about nothing. With the option, hook ``on_heard`` (before the reader) asks the person to say it again, once, with the pack's
own repeat phrase (``q_repeat``):

* ``empty``: the request and the person's answers hold no word at all;
* ``hole``: the words match the language pack's ``asr.hole`` patterns (a regex or a list of them, per language, with a ``source`` label or
  ``assumed``), for the run's language and the prompt language.

Asked once per run (a second silence goes on to the reader as before). A language with no ``asr.hole`` data gets only the empty check;
no request text at all and no data: the same. Hook ``on_heard`` (decide, flow; service ``ask_clarify``). Keys: ``enabled`` (legacy ``asr_guard``), ``empty`` (true),
``hole`` (true). Lab flag ``asr_guard``: ``{what}``. Module ``robot_agent/options/asr_guard.py``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import langdata, names, speech

SPEC = OptionSpec(
    name="asr_guard",
    keys=(Key("enabled", "bool", False, aliases=("asr_guard",)), Key("empty", "bool", True), Key("hole", "bool", True)),
    hooks=("on_heard",),
    order=40,
    enabled="enabled",
    doc="An empty or holed request (an article directly followed by a function word, from the language pack) is asked again, once, without calling the reader.",
)


def hole_patterns(langs) -> list:
    """The compiled ``asr.hole`` regexes of the packs of ``langs`` (a bad regex is skipped)."""
    out = []
    for lg in langs:
        v = (langdata.value(lg, "asr", {}) or {}).get("hole") or []
        for p in [v] if isinstance(v, str) else v:
            try:
                out.append(re.compile(str(p), re.I))
            except re.error:
                continue
    return out


def has_hole(text: str, langs) -> bool:
    t = " ".join(str(text or "").lower().split())
    return bool(t) and any(p.search(t) for p in hole_patterns(langs))


class AsrGuard(Option):
    def on_heard(self, run):
        """Ask to repeat when the request is empty or holed and nothing was asked again yet (the loop reads the next round); else None."""
        q = self._question(run)
        if q is None:
            return None
        run.repeat_asked = True
        yield run.ev("understand_check", issues=["request not heard in full"], repeat=True)
        return (yield from self.svc.ask_clarify(run, q))

    def _question(self, run):
        if getattr(run, "repeat_asked", False):
            return None
        said = [str(getattr(run, "task", "") or "")] + [str(t) for w, t in getattr(run, "dialogue", None) or [] if w == "user"]
        if self.cfg["empty"] and not any(re.search(r"\w", s) for s in said):
            self.svc.lab(SPEC.name, what="empty")
            return speech.LocalText("q_repeat")
        if self.cfg["hole"] and not getattr(run, "dialogue", None) and has_hole(said[0], names.langs_of(run)):
            self.svc.lab(SPEC.name, what="hole")
            return speech.LocalText("q_repeat")
        return None


register(SPEC, AsrGuard)
