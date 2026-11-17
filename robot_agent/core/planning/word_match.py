"""Is a word in what was heard: the loose word match of options ``heard_check`` and ``cond_extract`` (key ``heard_match``), pure functions.

What: a token is found when a heard token starts with its first ``min_prefix`` characters, or is ``min_ratio`` similar cut to its length.
Why: two options check the person's words against what the recogniser heard; one shared rule, no copy (moved out of ``heard_check``).
How to switch it off: it does nothing by itself; ``limits`` of missing or unlabelled data is None and the callers then check nothing.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter

from .pack_data import labelled


def tokens(text) -> list:
    """The lower-cased word tokens of ``text`` (Unicode word characters)."""
    return re.findall(r"\w+", str(text or "").lower())


def main_script(text) -> str:
    """The Unicode script of most letters of ``text`` (the first word of each letter's Unicode name); '' when it has no letter."""
    c = Counter(unicodedata.name(ch, " ").split(" ")[0] for ch in str(text or "") if ch.isalpha())
    return c.most_common(1)[0][0] if c else ""


def limits(data):
    """``(min_prefix, min_ratio)`` of labelled match data, or None (missing, unlabelled or not numbers: nothing is checked)."""
    if not labelled(data):
        return None
    try:
        return int(data["min_prefix"]), float(data["min_ratio"])
    except (KeyError, TypeError, ValueError):
        return None


def token_found(q: str, heard, min_prefix: int, min_ratio: float) -> bool:
    """A heard token starts like ``q`` (its first ``min_prefix`` characters, all of a shorter ``q``), or is ``min_ratio`` similar cut to its length."""
    need = min(min_prefix, len(q))
    for h in heard:
        if need > 0 and h[:need] == q[:need]:
            return True
        if difflib.SequenceMatcher(None, q, h[:len(q)]).ratio() >= min_ratio:
            return True
    return False
