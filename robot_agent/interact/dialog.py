"""The robot's questions to the person, owned by the interaction core (proposal 3.3).

The robot's own Ask skill only does the I/O (say the question, listen on its
microphone or the dashboard, show it on the screen). The state lives here:

  * while the robot asks, the run is registered as waiting on that question
    (:meth:`Run.set_awaiting`), so whatever anyone types or says into the run
    (``RunManager.inject``, a second client, ``POST /runs/{id}/inject``) is
    routed as ANSWER instead of being queued as a NEW command;
  * :func:`ask` returns the first answer from either channel, the robot's I/O
    or an injected ANSWER, and gives up at once on a stop;
  * :func:`ask_heard` asks again on silence; :func:`yes_no` reads a spoken
    confirmation, :func:`read_yes_no` also tells an unclear answer ("yet", noise)
    from a correction, and :func:`ask_yes_no` asks an unclear one again once
    ("yes or no?") so the question keeps its turn (docs/dialog.md).

An :class:`Answer` says where it came from: a typed answer (``source='typed'``)
cannot have been misheard, so a read-back of it is not needed.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from ..core.planning import langdata


def _words_rx(key: str):
    """``\\b(w1|w2|...)\\b`` over the words ``dialogue[key]`` of every language pack (language order), None when no pack has any."""
    ws = langdata.dialogue_all(key)
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in ws) + r")\b") if ws else None


# spoken yes / no for read-backs: the words are language-pack data (``dialogue.yes`` / ``dialogue.no``); a language with
# no pack contributes none (a reply in it is then read as a correction, as any text is)
YES = _words_rx("yes") or re.compile(r"(?!)")
NO = _words_rx("no") or re.compile(r"(?!)")

SOURCE_ROBOT = 'robot'          # the robot's own I/O (its microphone, the dashboard listen, its screen)
SOURCE_TYPED = 'typed'          # an ANSWER injected into the run by any client


@dataclass
class Answer:
    text: str = ''
    source: str = ''            # robot | typed | '' (no answer)

    def __bool__(self) -> bool:
        return bool(self.text)


def _release_listens() -> None:
    """The robot's listen is no longer needed (answered elsewhere): end it with nothing."""
    try:
        from ..utils import end_pending_listens
        end_pending_listens()
    except Exception:
        pass


def ask(run, question: str, io: Callable[[str], str], poll: float = 0.01,
        join_s: float = 2.0, release: Callable[[], None] | None = None) -> Answer:
    """Ask ``question`` through ``io`` (the robot's Ask skill: blocking, returns
    the heard text or '') with ``run`` waiting on it. The first of: io's answer,
    an ANSWER injected into the run, a stop (-> empty). With no run, just io."""
    if run is None:
        return Answer(_clean(io(question)), SOURCE_ROBOT)
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box['text'] = io(question)
        except Exception:
            box['text'] = ''
        finally:
            done.set()
    run.set_awaiting(question)
    t = threading.Thread(target=work, name='dialog-io', daemon=True)
    try:
        t.start()
        while True:
            typed = run.poll_answer(0)
            if typed is not None:
                (release or _release_listens)()
                t.join(join_s)               # the robot's listen ends before the next skill starts
                return Answer(_clean(typed), SOURCE_TYPED)
            if run.cancel_requested:
                return Answer()
            if done.wait(poll):
                typed = run.poll_answer(0)  # an answer typed in the same instant wins
                if typed is not None:
                    return Answer(_clean(typed), SOURCE_TYPED)
                return Answer(_clean(box.get('text')), SOURCE_ROBOT if _clean(box.get('text')) else '')
    finally:
        if run.awaiting == question:
            run.set_awaiting(None)


def ask_heard(run, question: str, io: Callable[[str], str], silence_retries: int = 1,
              again: str = "Sorry, I didn't hear you. {q}", cancelled: Callable[[], bool] | None = None
              ) -> tuple[Answer, int]:
    """:func:`ask`, asked again on silence (``silence_retries``). Returns (answer, times asked)."""
    n = 0
    for k in range(1 + max(0, int(silence_retries))):
        n += 1
        # a robot phrase (planning.speech.LocalText) is asked again as a phrase, so it is said in the user's language
        q = question if k == 0 else (question.again() if callable(getattr(question, "again", None))
                                     else again.format(q=question))
        ans = ask(run, q, io)
        if ans or (run is not None and run.cancel_requested) or (cancelled is not None and cancelled()):
            return ans, n
    return Answer(), n


def yes_no(text) -> tuple[Optional[bool], str]:
    """(True | False | None, correction text) from a spoken yes / no answer."""
    t = (text or "").strip()
    if not t:
        return None, ""
    low = t.lower()
    if NO.search(low):
        rest = _no_polite(NO.sub(" ", low, count=1).strip(" ,.!?"))
        return False, rest
    if YES.search(low):
        return True, ""
    return False, t                              # anything else: a correction ("the restroom one")


def _no_polite(rest: str) -> str:
    """``rest`` (what follows a "no") without the polite words of the language packs (``dialogue.polite``: a lone
    politeness word after a no is manners, not a correction). Packs with none: ``rest`` as it is."""
    ws = langdata.dialogue_all("polite")
    if not ws or not rest:
        return rest
    rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in sorted(ws, key=len, reverse=True)) + r")(?!\w)")
    return " ".join(rx.sub(" ", rest).split()).strip(" ,.!?")


# ── unclear answers to a yes / no question (ASR2) ────────────────────────────
# A yes / no question answered with noise or a near-miss of yes / no ("yet", "yea", "uh", "내" for 네) is
# not a correction: it is asked again once ("yes or no?"), so the question keeps its turn. A clear
# correction ("the one in the restroom") still is one: it has a word that is not a filler or a near-miss.
def _fillers() -> set:
    return set(langdata.dialogue_all("fillers")) | set(langdata.dialogue_all("near_yes_no"))


def _yn_words() -> tuple:
    return tuple(langdata.dialogue_all("yn_words"))


@dataclass(frozen=True)
class Reply:
    """A yes / no answer read: ``kind`` yes | no | correction | unclear | silence; ``rest`` the
    correction text (after a "no, ..." or the whole answer)."""
    kind: str
    rest: str = ''

    @property
    def ok(self) -> Optional[bool]:
        """The old :func:`yes_no` verdict: True (yes), None (silence), False (anything else)."""
        return True if self.kind == 'yes' else (None if self.kind == 'silence' else False)


def _near(a: str, b: str) -> bool:
    """Edit distance <= 1 (a mishearing of a short word)."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:k] + long_[k + 1:] == short for k in range(len(long_)))


def _vague(tok: str) -> bool:
    """A token that carries no answer: a filler, a near-miss of yes / no, a 1-2 letter fragment."""
    if tok in _fillers():
        return True
    if tok.isascii() or langdata.latin_text(tok):
        return len(tok) <= 2 or (len(tok) <= 5 and any(_near(tok, w) for w in _yn_words()))
    return False


def read_yes_no(text) -> Reply:
    """:func:`yes_no` that tells an unclear answer from a correction (see ``Reply``)."""
    t = (text or "").strip()
    if not t:
        return Reply('silence')
    ok, rest = yes_no(t)
    if ok is True:
        return Reply('yes')
    if rest != t:                                # a no word was heard ("no", "no, the sofa")
        return Reply('no', rest)
    toks = re.findall(r"[\w']+", t.lower())
    if not toks or all(_vague(w) for w in toks):
        return Reply('unclear', t)
    return Reply('correction', t)


def ask_yes_no(ask: Callable[[str], str], question: str, again: str, reasks: int = 1,
               stopped: Callable[[], bool] | None = None) -> tuple[str, Reply, int]:
    """Ask a yes / no ``question`` with ``ask`` (question -> heard text); an unclear answer is asked
    again with ``again`` ("yes or no?") up to ``reasks`` times. Returns (last answer, its Reply, times asked).
    A Reply still ``unclear`` after the re-asks is the caller's fallback."""
    ans = ask(question)
    rep, n = read_yes_no(ans), 1
    while rep.kind == 'unclear' and n <= max(0, int(reasks)) and not (stopped is not None and stopped()):
        ans = ask(again)
        rep, n = read_yes_no(ans), n + 1
    return ans, rep, n


def _clean(text) -> str:
    return str(text or '').strip()


__all__ = ['Answer', 'Reply', 'ask', 'ask_heard', 'ask_yes_no', 'read_yes_no', 'yes_no', 'YES', 'NO', 'SOURCE_ROBOT', 'SOURCE_TYPED']
