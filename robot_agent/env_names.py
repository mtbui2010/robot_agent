"""Location names in the ``ENV`` global config: canonical keys + aliases.

An ``ENV`` entry is keyed by its canonical name (``'dressroom@main room'``) and
may list other names for the same spot::

    'dressroom@main room': {'aliases': ['옷장', 'tủ áo', 'closet'], 'loc': {...}, ...}

``resolve_env_name`` turns whatever a plan or a person said into the canonical
key, so every skill works with one name per place; ``validate_env`` is run on
save, so a name that would point at two places is refused while configuring,
not discovered while the robot is driving.
"""
import logging
import unicodedata

log = logging.getLogger(__name__)

# Names `move` handles itself before any ENV lookup; an alias must not take them.
RESERVED_NAMES = ('home', 'base')


class AmbiguousLocation(ValueError):
    """*name* matches more than one ENV location."""

    def __init__(self, name, candidates):
        self.name, self.candidates = name, list(candidates)
        super().__init__(f'"{name}" matches {len(self.candidates)} locations: '
                         f'{", ".join(self.candidates)}')


def normalize(name) -> str:
    """Case / Unicode-form / whitespace-insensitive form of a name."""
    s = unicodedata.normalize('NFC', str(name)).strip().lower()
    return '@'.join(' '.join(part.split()) for part in s.split('@'))


def _fold(name: str) -> str:
    """*name* with Latin diacritics removed ('tủ áo' -> 'tu ao'). Hangul
    decomposes into jamo, not combining marks, so it comes back unchanged."""
    s = unicodedata.normalize('NFD', name.replace('đ', 'd'))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return unicodedata.normalize('NFC', s)


def aliases_of(spec) -> list:
    """The normalized, non-empty aliases of one ENV entry."""
    al = spec.get('aliases') if isinstance(spec, dict) else None
    if isinstance(al, str):
        al = [al]
    if not isinstance(al, (list, tuple)):
        return []
    return [a for a in (normalize(x) for x in al if isinstance(x, str)) if a]


def _names(key, spec) -> list:
    """Every name that points at this entry: its key, its aliases, and each
    single-word alias with the key's room part ('옷장' + 'main room' ->
    '옷장@main room')."""
    k = normalize(key)
    names = [k] + aliases_of(spec)
    rest = k.split('@', 1)[1] if '@' in k else ''
    if rest:
        names += [f'{a}@{rest}' for a in aliases_of(spec) if '@' not in a]
    return names


def _pick(name, hits, strict):
    hits = list(dict.fromkeys(hits))
    if len(hits) <= 1:
        return hits[0] if hits else None
    if strict:
        raise AmbiguousLocation(name, hits)
    log.warning('location "%s" is ambiguous (%s); using %s', name, ', '.join(hits), hits[0])
    return hits[0]


def resolve_env_name(name, env, strict=False):
    """Canonical ENV key for *name*, or ``None`` when it names no location.

    Tried in order, the first step with a match wins:
      1. a key, exactly;
      2. an alias, exactly;
      3. a whole ``@`` segment run of a key or alias (``'table'`` finds
         ``'table@kitchen'``) — the matching ENV has always done;
      4. a key or alias ignoring diacritics (``'tu ao'``), only when unique.

    A name matching several locations in step 2 or 3 raises
    :class:`AmbiguousLocation` when *strict*, else logs a warning and returns
    the first in ENV order (what the lookup did before aliases existed).
    Object-qualified names (``'cup@table@kitchen'``) match nothing here; callers
    strip the object part and retry, as before.
    """
    if not isinstance(name, str):
        return None
    n = normalize(name)
    if not n:
        return None
    entries = [(k, v) for k, v in env.items()]
    for k, _ in entries:
        if normalize(k) == n:
            return k
    hit = _pick(name, [k for k, v in entries if n in aliases_of(v)], strict)
    if hit is not None:
        return hit
    seg = f'@{n}@'
    hit = _pick(name, [k for k, v in entries
                       if any(seg in f'@{x}@' for x in _names(k, v))], strict)
    if hit is not None:
        return hit
    f = _fold(n)
    hits = list(dict.fromkeys(k for k, v in entries
                              if any(_fold(x) == f for x in _names(k, v))))
    return hits[0] if len(hits) == 1 else None


def validate_env(env) -> str:
    """``''`` when *env* is a valid ENV config, else what is wrong with it."""
    if not isinstance(env, dict):
        return 'ENV must be an object of locations'
    owner = {}                              # normalized name -> key that owns it
    for key in env:
        n = normalize(key)
        if n in owner:
            return f'locations "{owner[n]}" and "{key}" have the same name'
        owner[n] = key
    for key, spec in env.items():
        if not isinstance(spec, dict):
            continue                # legacy non-object entries (etri has one) are left alone
        al = spec.get('aliases')
        if al is None:
            continue
        if not isinstance(al, list) or not all(isinstance(a, str) for a in al):
            return f'aliases of "{key}" must be a list of names'
        for raw in al:
            a = normalize(raw)
            if not a:
                return f'"{key}" has an empty alias'
            if a in RESERVED_NAMES:
                return f'alias "{raw}" of "{key}" is reserved (move::{a} has its own meaning)'
            other = owner.get(a)
            if other is not None and other != key:
                return (f'alias "{raw}" of "{key}" is already '
                        + (f'the location "{other}"' if normalize(other) == a
                           else f'an alias of "{other}"'))
            owner[a] = key
    return ''


def describe_env(env) -> str:
    """One line per location for a planner prompt:
    ``- dressroom@main room (also: 옷장, tủ áo)``."""
    lines = []
    for key, spec in env.items():
        al = spec.get('aliases') if isinstance(spec, dict) else None
        al = [a for a in al if isinstance(a, str) and a.strip()] if isinstance(al, list) else []
        lines.append(f'- {key}' + (f' (also: {", ".join(al)})' if al else ''))
    return '\n'.join(lines)
