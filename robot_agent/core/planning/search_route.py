"""Search route (default off): among places the belief finds about equally likely, look first at the nearer one.

The search belief ranks places by how likely the object is there. Taken alone, that order can send the robot back and forth
across the home. With ``route_weight`` > 0 the next look is chosen by likelihood minus ``route_weight`` times the distance from
where the robot stands, as a share of the farthest place (so 0..route_weight): a likely place still wins by a clear margin, and ties
and near-ties go to the closer one, which shortens the whole route.

Generic: positions are data. Each ENV place may carry ``loc: {x, y}`` (robot_agent's optional ENV contract, docs/env.md). A place
without a position, a robot position that is not a place with one, or ``route_weight`` 0 leave the order exactly as it was.
"""

from __future__ import annotations

import math
from typing import Optional


def locs_from_env(env: dict, keys) -> dict:
    """{place: (x, y)} of the ENV places that declare a position."""
    out = {}
    for k in keys:
        loc = (env.get(k) or {}).get("loc")
        try:
            out[k] = (float(loc["x"]), float(loc["y"]))
        except (TypeError, KeyError, ValueError):
            continue
    return out


def route_scores(scores: dict, locs: dict, here: Optional[str], weight: float) -> dict:
    """``scores`` less the distance cost from ``here``; the same dict when there is nothing to apply."""
    if weight <= 0 or here not in locs:
        return scores
    hx, hy = locs[here]
    dist = {p: math.hypot(x - hx, y - hy) for p, (x, y) in locs.items()}
    far = max(dist.values(), default=0.0)
    if far <= 0:
        return scores
    return {p: s - weight * dist[p] / far if p in dist else s for p, s in scores.items()}
