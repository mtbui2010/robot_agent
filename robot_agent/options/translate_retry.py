"""Option ``translate_retry`` (default off): a translation that lost a place name the person said is made again with the name given in English.

The input translation (a language other than the prompt's) is checked against the names the person said: when the English lacks a name
the ENV knows (a Korean word for a place came out as another thing), the sentence is untrusted and the robot asks the person to say it
again. With the option, the sentence is first translated once more with each such name already replaced by its English name from the
ENV, so the model only has to translate the rest. The retry's English counts only when it keeps every name; otherwise nothing changes.
One more model call, only when a name was lost.

Generic: the names come from the ENV aliases the language layer already looked up; no word of a language here.
Hook: ``on_translation_lost``. Keys: ``enabled`` (legacy ``translate_retry``).
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="translate_retry",
    keys=(Key("enabled", "bool", False, aliases=("translate_retry",)),),
    hooks=("on_translation_lost",),
    order=60,
    enabled="enabled",
    doc="A translation that lost a place name is made again with the name given in English, before the person is asked to repeat.",
)


class TranslateRetry(Option):
    def on_translation_lost(self, text, lost, translate):
        """``lost``: {the person's word: its English name}; ``translate(s)``: the language layer's translation of ``s``."""
        fixed = text
        for word, english in sorted(lost.items(), key=lambda kv: -len(kv[0])):
            fixed = fixed.replace(word, english)
        out = translate(fixed)
        self.svc.lab("translate_retry", lost=sorted(lost), ok=bool(out))
        return out or None


register(SPEC, TranslateRetry)
