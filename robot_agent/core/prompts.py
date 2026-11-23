"""Prompt sentences for recorded episodes, generated at random from templates the robot's config provides (``RECORDER['prompts']``).

The operator fills a few FIELDS (action, target_object, target_location, destination_object, destination_location ...); this picks a sentence
that uses all of them, so the same episode gets a different wording each time (the diversity a VLA needs). Nothing here names a task or a language:

    {'fields': ['action', 'target_object', ...],                       # the fields the UI shows, in order (``action`` is required)
     'templates': ['{action} the {target_object} on the {target_location}',      # a template is a string ...
                   {'text': '{action} the {target_location} {target_object}', 'actions': ['open', 'close']},   # ... or {text, actions?, not_actions?}
                   ...],
     'synonyms': {'pick': ['pick up', 'grab'], 'top': ['top', 'first', 'upper']}}   # a field value may be replaced by one of these (lower-case key)

A template can be used when every field it names has a value; among those, the ones that use the MOST fields win (so every field the operator
filled appears in the sentence); one of them is chosen at random.
"""
from __future__ import annotations

import random
import re

_FIELD = re.compile(r'\{([a-z_]+)\}')


def _text(t) -> str:
    return t if isinstance(t, str) else str(t.get('text') or '') if isinstance(t, dict) else ''


def template_fields(template) -> set:
    return set(_FIELD.findall(_text(template)))


def _allowed(t, action: str) -> bool:
    """A dict template may name the actions it is for (``actions``) or not for (``not_actions``), compared in lower case."""
    if not isinstance(t, dict):
        return True
    a = action.lower()
    only, never = [str(x).lower() for x in t.get('actions') or []], [str(x).lower() for x in t.get('not_actions') or []]
    return (not only or a in only) and a not in never


def _clean(v) -> str:
    return re.sub(r'\s+', ' ', str(v or '')).strip()


def usable(spec: dict, values: dict) -> list:
    """The templates every field of which has a value, keeping only those that use the most fields. [] when none fits."""
    have = {k for k, v in values.items() if _clean(v)}
    action = _clean(values.get('action'))
    ok = [t for t in (spec or {}).get('templates') or [] if template_fields(t) and template_fields(t) <= have and _allowed(t, action)]
    if not ok:
        return []
    best = max(len(template_fields(t)) for t in ok)
    return [t for t in ok if len(template_fields(t)) == best]


def generate(spec: dict, values: dict, rng=None, avoid: str = None) -> str:
    """One sentence for ``values`` ({field: text}); ValueError (a sentence the operator can read) when no template fits.
    ``avoid`` (the sentence given last time): another one is drawn when the fields allow one, so pressing Generate again changes the sentence."""
    rng = rng or random
    spec = spec or {}
    values = {k: _clean(v) for k, v in (values or {}).items()}
    if not values.get('action'):
        raise ValueError('fill the action first')
    cands = usable(spec, values)
    if not cands:
        filled = sorted(k for k, v in values.items() if v)
        raise ValueError(f'no template uses exactly these fields: {", ".join(filled) or "none"}')
    syn = {str(k).lower(): [str(x) for x in v] for k, v in (spec.get('synonyms') or {}).items() if isinstance(v, (list, tuple)) and v}

    def fill(m):
        v = values[m.group(1)]
        return rng.choice(syn[v.lower()]) if v.lower() in syn else v
    def one():
        return _clean(_FIELD.sub(fill, _text(rng.choice(cands))))
    out = one()
    for _ in range(40 if avoid else 0):                  # a few draws are enough: the combinations are few and each is likely
        if out != _clean(avoid):
            break
        out = one()
    return out
