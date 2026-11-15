"""What an option may ask of the loop that hosts it: a small, named, public surface instead of ``loop._private`` calls.

The host (AssistLoop) builds one object with these methods; a test builds a stub. Methods an option does not use may be
missing on a stub: options call only what they need.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class Services(Protocol):
    robot: Any                                        # the RobotView (manifest, roles, actions)

    def now(self) -> float: ...                       # seconds on the host's clock
    def lab(self, flag: str, **info) -> None: ...     # record that the option acted (lab_log)
    def role(self, name: str) -> Optional[str]: ...   # the pack's action for a role (action_roles)
    def hand_action(self) -> Optional[str]: ...       # the robot's hand-over (release) action, or None
    def finish(self, run, status: str, **kw) -> dict: ...                       # the ``done`` event
    def ask(self, run, question) -> Optional[str]: ...                          # one question to the person, the answer
    def emit(self, run, event: str, **fields) -> dict: ...                      # an event from a non-flow hook / service: logged, pushed live if the host can
    def look_for(self, run, obj: str) -> Any: ...                              # one more Find of ``obj`` as the goal check makes it: (seen, skill)
    def prefer_now(self, run, obj: str, keys) -> None: ...                      # look at ``keys`` first for ``obj``, also in a search under way
    def first_unsearched(self, run, obj: str, keys) -> Optional[str]: ...        # the best of ``keys`` not looked at yet
    def run_step(self, run, step: dict) -> Any: ...                            # one step of the end-of-run check, effect applied on success: the result or None
    def open_items(self, run) -> list: ...                                      # the request items the options report left open at the end (service ``ledger_open``)
    def cancelled(self, run) -> Optional[dict]: ...                             # the stop event if the run was cancelled
    def safety_gate(self, run): ...                                              # generator: the goals through the safety gate again (STOP or None)
    def safety_rule(self, goal, places) -> Any: ...                             # the Verdict of an option's declared hazard rule for a goal, or None
    def replan_or_fail(self, run, step, reason: str, msg: str): ...            # generator: the replan / give-up path
    def replan_round(self, run) -> tuple: ...                                   # (replans recorded, planner calls left in max_replans)
    def readback_left(self, run) -> bool: ...                                   # would the loop read the goals back once more now (its cap, confirm_limit)
    def ask_model(self, run, prompt: str, schema: dict) -> Optional[dict]: ...  # ONE extra model call (no repair, stoppable, in the run's budget): the JSON dict or None
    def request_english(self, run) -> str: ...                                 # the request's words in the prompt language ("" if no trusted reading)
    def unsure_leftover(self, run, text) -> Optional[str]: ...                 # an "I don't know" answer without its phrases ("" = nothing else); None = not one
    def say_name(self, places, key: str) -> str: ...
    def in_lang(self, text, lang: str): ...
    def humanize(self, text) -> str: ...
    def goal_phrase(self, goal, places) -> str: ...
    def mode(self, name: str) -> Any: ...             # a run-level engine setting an option needs to read (not an option key)
