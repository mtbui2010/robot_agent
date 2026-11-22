"""Execution helpers of AssistLoop: bind a step to a skill, read the hand and base sensors, locate a far point.

What: ``ExecHelpers`` is a mixin with ``_bind``, ``_params``, ``_holding``, ``_base_pose``, ``_dist_to_place``, ``_found_point``
and ``_place_of_far_point``, moved VERBATIM out of ``assist_loop.py`` (step S23a of the core generalization). They use only
``self.robot``, ``self.sr``, ``self.cfg``, ``self._query_of``, ``self._in_lang`` and ``self._find_params``, as before.
Why: the execute phase calls them on every step; they are the small, side-effect-free (apart from sensor reads) part of it
and leave the loop file shorter. ``_effect`` stays in ``assist_loop.py``: it calls ``current()``, which tests patch there.
Switch off: remove the ``ExecHelpers`` line from ``PHASE_MIXINS`` in ``phases/__init__.py`` and put the methods back.
"""
from __future__ import annotations

import math

from .. import langdata, persons
from ...pack import actions as action_data

_LOC_ACTIONS = set(action_data.names(action_data.default_table(), arg=("place", "container")))   # pack: actions.yaml


class ExecHelpers:
    def _bind(self, st, world):
        """``(skill, params)`` of a grounded step (None: a no-op); raises Unbindable. The step's object is
        already the robot's name (an ENV key / the object), so it goes in as is."""
        mapped = self.robot.bind(dict(st), world)
        if mapped is None:
            return None
        skill, p = mapped[0], dict(mapped[1] or {})
        a, o = st["action"], st["object"]
        if a in _LOC_ACTIONS or a in ("Find", "Pick"):
            p["inputs"] = self._query_of(st, o)          # the instance the person chose (which_one), else the name
        lang = getattr(self, "_run_lang", "en") or "en"
        if a in ("Say", "Announce") and not langdata.is_prompt_lang(lang):
            from .. import lexicon
            for k_ in ("inputs", "text"):
                if isinstance(p.get(k_), str) and p[k_].strip() and lexicon.lang_of(p[k_]) != lang[:2]:
                    p[k_] = self._in_lang(p[k_], lang)       # a Say in the user's language (F5.4)
        if a == "Find" and self.cfg["find_min_conf"] > 0 and "min_conf" in self._find_params:
            p["min_conf"] = self.cfg["find_min_conf"]
        if a == "Find" and self.cfg["find_verify"] and "verify" in self._find_params:
            p["verify"] = True
        return skill, p

    def _params(self, st, world):
        """The params of a grounded step (legacy entry point; see ``_bind``)."""
        mapped = self._bind(st, world)
        return dict(mapped[1]) if mapped else {}

    def _holding(self, node, default: bool) -> bool:
        """Is something in the hand? The robot's holding sensor (the one that verifies its Pick);
        ``default`` when it has none or it cannot be read."""
        skill = self.robot.holding_sensor()
        if skill is None:
            return default
        try:
            return bool(self.sr.execute(skill, {}, node).get("isdone"))
        except Exception:
            return default

    def _base_pose(self, node):
        """[x, y, yaw_deg] from the robot's pose sensor, or None."""
        skill = self.robot.pose_sensor()
        if skill is None:
            return None
        try:
            return list(self.sr.execute(skill, {}, node)["pose"])[:3]
        except Exception:
            return None

    def _dist_to_place(self, key, node, places):
        """Base distance (m) from the ENV loc of `key`, or None when unknown."""
        l = (places.env.get(key) or {}).get("loc") or {}
        if "x" not in l or "y" not in l:
            return None
        pose = self._base_pose(node)
        if pose is None:
            return None
        return math.hypot(pose[0] - l["x"], pose[1] - l["y"])

    @staticmethod
    def _found_point(result, obj):
        """Base-frame [x, y, z] of what Find detected (``ins[obj].pose_3d``), or None."""
        ins = (result or {}).get("ins") if isinstance(result, dict) else None
        if not isinstance(ins, dict) or not ins:
            return None
        e = ins.get(obj) or next(iter(ins.values()))
        p = e.get("pose_3d") or e.get("loc_3d") if isinstance(e, dict) else None
        try:
            return [float(v) for v in p[:3]]
        except Exception:
            return None

    def _place_of_far_point(self, p3, node, places, world):
        """The ENV place to stand at for a point seen beyond reach (None when it is within reach / unknown)."""
        if math.hypot(p3[0], p3[1]) <= self.cfg["reach_far"]:
            return None
        pose = self._base_pose(node)
        if pose is None or len(pose) < 3:
            return None
        x, y, rz = pose
        c, s_ = math.cos(math.radians(rz)), math.sin(math.radians(rz))
        wx, wy = x + c * p3[0] - s_ * p3[1], y + s_ * p3[0] + c * p3[1]
        best = None
        for k in places.keys:
            l = places.env[k].get("loc") or {}
            if "x" in l and "y" in l and not places.openable(k) and not persons.is_person(places, k):
                d = math.hypot(l["x"] - wx, l["y"] - wy)
                if best is None or d < best[0]:
                    best = (d, k)
        if best and best[0] < self.cfg["reach_far"] + 0.5 and best[1] != world.arrived:
            return best[1]
        return None
