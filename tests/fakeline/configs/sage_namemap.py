"""The fakeline name map: the plan actions and the skills they run (made-up line cart, see adapter.py)."""

from __future__ import annotations

SKILL_MAP: dict = {
    "MoveTo": "move",
    "Find": "find",
    "Pick": "pick",
    "Place": "place",
    "Say": "say",
    "WaitForUnload": "wait_for_unload",
    "Inspect": "inspect",
    "Fasten": "fasten",
}
NOOP_ACTIONS: set = set()
SUPPORTED_ACTIONS: set = set(SKILL_MAP)
VLM_ACTIONS: set = set()


def to_loc(name: str) -> str:
    return name or ""


def to_obj(name: str) -> str:
    return (name or "").strip().lower()


def build_params(action: str, obj: str, world) -> dict:
    if action == "Pick":
        found = getattr(world, "found", None) if world is not None else None
        return {"inputs": to_obj(found or obj)}
    return {}
