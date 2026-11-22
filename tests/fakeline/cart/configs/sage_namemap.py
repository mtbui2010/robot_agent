"""The name map of ``fakeline.cart`` (made up)."""

SKILL_MAP: dict = {"MoveTo": "move", "Say": "say", "WaitForUnload": "wait_for_unload"}
NOOP_ACTIONS: set = set()
SUPPORTED_ACTIONS: set = set(SKILL_MAP)


def to_loc(name: str) -> str:
    return name or ""


def to_obj(name: str) -> str:
    return (name or "").strip().lower()


def robot_xy(node):
    """The cart's base position for the core's generic reconcile (made up: it reports the bin station's spot, x 1.0, y 1.0, as a
    parked cart would; assumed). The pack's own pose reader, the second level of loop.reconcile_world."""
    return (1.0, 1.0)
