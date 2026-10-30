"""Robust understanding: the understand LLM call and its checks, robot-agnostic.

* ``LLMBudget``: a per-run cap on the LLM calls the core makes (understand, its repairs and samples, planner calls).
* ``ask_json``: one JSON call with a bounded repair retry when the answer is not JSON; a client error (server down,
  timeout) becomes ``LLMUnavailable`` with a FailureCode, so the run can end with a clear message.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from robot_agent.contract import FailureCode


# ─────────────────────────────────────────────────────────────────────────────
# LLM calls: errors, budget, JSON repair
# ─────────────────────────────────────────────────────────────────────────────
_UNREACHABLE = re.compile(r"connect|refused|unreachable|timed? ?out|timeout|http|50[234]|name resolution|"
                          r"remote end closed|max retries", re.I)


class LLMUnavailable(Exception):
    """A language-model / planner call raised: the server is down or timed out (``unreachable``), or the call
    failed otherwise (a bug, a bad answer it choked on). ``code`` is the run's FailureCode."""

    def __init__(self, cause: BaseException, where: str = "understand"):
        super().__init__(f"{where}: {cause}")
        self.cause, self.where = cause, where
        self.unreachable = isinstance(cause, (ConnectionError, TimeoutError)) or \
            bool(_UNREACHABLE.search(f"{type(cause).__name__} {cause}"))
        self.code = llm_error_code(cause) if self.unreachable else FailureCode.SOFTWARE

    def message(self, what: str = "language model") -> str:
        return f"the {what} is not reachable: {self.cause}" if self.unreachable else f"the {what} failed: {self.cause}"


class LLMBudgetExceeded(Exception):
    """The run used its LLM-call budget."""


class LLMBadOutput(Exception):
    """The model kept answering something that is not the JSON asked for."""


def llm_error_code(e: BaseException) -> FailureCode:
    """TIMEOUT for a timed-out call, HW_FAULT (a server down / unreachable) otherwise."""
    txt = f"{type(e).__name__} {e}".lower()
    if isinstance(e, TimeoutError) or "timed out" in txt or "timeout" in txt:
        return FailureCode.TIMEOUT
    return FailureCode.HW_FAULT


class LLMBudget:
    """Counts the core's LLM calls in one run; ``take`` raises LLMBudgetExceeded past ``limit`` (0 = no cap)."""

    def __init__(self, limit: int):
        self.limit, self.used = int(limit), 0

    def take(self, n: int = 1, what: str = "LLM call") -> None:
        if self.limit and self.used + n > self.limit:
            raise LLMBudgetExceeded(f"{what}: the LLM-call budget of this run ({self.limit}) is used up")
        self.used += n

    def left(self) -> int:
        return max(0, self.limit - self.used) if self.limit else 1 << 30


REPAIR_PROMPT = ("Your previous answer was not valid JSON:\n{bad}\n\nAnswer the request below again with ONLY the "
                 "JSON object (no prose, no code fence).\n\n{prompt}")


def parse_json_object(raw) -> Optional[dict]:
    """The JSON object in a model answer (bare, fenced or inside prose), else None."""
    if isinstance(raw, dict):
        return raw
    s = str(raw or "")
    for cand in (s, *re.findall(r"\{.*\}", s, re.S)):
        try:
            d = json.loads(cand)
        except Exception:
            continue
        if isinstance(d, dict):
            return d
    return None


def ask_json(llm, prompt: str, schema: dict, budget: Optional[LLMBudget] = None, retries: int = 1,
             where: str = "understand") -> dict:
    """One JSON answer from ``llm.chat``: on a non-JSON answer, up to ``retries`` repair calls (the bad answer
    quoted back). Raises LLMUnavailable (client error), LLMBudgetExceeded, LLMBadOutput (still not JSON)."""
    p, last = prompt, ""
    for _k in range(1 + max(0, int(retries))):
        if budget is not None:
            budget.take(what=where)
        try:
            raw = llm.chat(prompt=p, format=schema)
        except Exception as e:                       # connection refused, read timeout, HTTP 5xx ...
            raise LLMUnavailable(e, where) from e
        d = parse_json_object(raw)
        if d is not None:
            return d
        last = str(raw)[:400]
        p = REPAIR_PROMPT.format(bad=last, prompt=prompt)
    raise LLMBadOutput(f"{where}: the language model did not answer JSON: {last[:120]!r}")
