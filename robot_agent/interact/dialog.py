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
    confirmation.

An :class:`Answer` says where it came from: a typed answer (``source='typed'``)
cannot have been misheard, so a read-back of it is not needed.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Callable, Optional

# spoken yes / no (ko / vi / en) for read-backs
YES = re.compile(r"\b(yes|yeah|yep|ok|okay|right|correct|sure|go ahead|네|예|응|맞아|맞아요|좋아|có|đúng|vâng|ừ)\b")
NO = re.compile(r"\b(no|nope|not|wrong|아니|아니요|아뇨|không|sai)\b")

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
        ans = ask(run, question if k == 0 else again.format(q=question), io)
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
        rest = NO.sub(" ", low, count=1).strip(" ,.!?")
        return False, rest
    if YES.search(low):
        return True, ""
    return False, t                              # anything else: a correction ("the restroom one")


def _clean(text) -> str:
    return str(text or '').strip()


__all__ = ['Answer', 'ask', 'ask_heard', 'yes_no', 'YES', 'NO', 'SOURCE_ROBOT', 'SOURCE_TYPED']
