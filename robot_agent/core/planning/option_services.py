"""The engine services an option may use (``core/options/services.py`` is the interface), implemented over an AssistLoop.

This is the one place where option hooks reach loop internals; an option module itself never touches a ``_private`` name.
"""

from __future__ import annotations

from . import speech


class LoopServices:
    def __init__(self, loop) -> None:
        self._lp = loop

    @property
    def robot(self):
        return self._lp.robot

    def now(self) -> float:
        return self._lp._now()

    def lab(self, flag: str, **info) -> None:
        self._lp._lab(flag, **info)

    def role(self, name: str):
        return self._lp._role(name)

    def hand_action(self):
        return self._lp._hand_action()

    def finish(self, run, status: str, **kw) -> dict:
        return self._lp._finish(run, status, **kw)

    def ask(self, run, question):
        """One question to the person: their answer ('' on silence). Counts the turn like every other question of the run."""
        lp = self._lp
        ans, _n = lp._ask_heard(question, run.node, lp._log_fn(-1), run.lang)
        run.heard += lp._answer_source == "robot"
        run.asked += 1
        return ans

    def cancelled(self, run):
        return self._lp._cancelled(run)

    def replan_or_fail(self, run, step, reason: str, msg: str):
        return self._lp._replan_or_fail(run, step, reason, msg)

    def say_name(self, places, key: str) -> str:
        return self._lp._say_name(places, key)

    def in_lang(self, text, lang: str):
        return self._lp._in_lang(text, lang)

    def humanize(self, text) -> str:
        return self._lp._humanize(text)

    def goal_phrase(self, goal, places):
        return self._lp._goal_phrase(goal, places, lambda k: speech.say_name(places, k, "en"))

    def reread_change(self, run, text):
        return self._lp._llm_goal_change(run, text)


def option_dispatcher(loop, overrides=None, environ=None):
    """The dispatcher of ``loop`` for the run options ``overrides``: what ``AssistLoop.__init__`` builds. For a host or a test
    that has a loop without running ``__init__``."""
    import os
    from ..options import Dispatcher
    planner = getattr(getattr(getattr(loop, "robot", None), "manifest", None), "planner", None)
    return Dispatcher(LoopServices(loop), overrides or {}, os.environ if environ is None else environ, planner)
