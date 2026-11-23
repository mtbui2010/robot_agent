"""Option ``shared_instances`` (default off): an exact memory name that two or more remembered instances share is not taken as resolved.

What: a move goal whose object is EXACTLY the name of an object-memory record with no instance id, while the records with an id that hold
that name as whole words are two or more instances (``goal_fix.shared_instances``), gets the engine's which-question: in grounding through
``inventory_ask`` (service ``shared_names``, passed to ``goal_fix.canon_inventory``) and in the read-back as a ``choice`` issue (hook
``on_issues``, after ``ask_focus``), unless the person's own words fit exactly one instance (grounding renames, the read-back adds nothing).
Why: a memory that keeps one bare record for a name two objects share at one usual place hid the choice; both checks skipped an
exact name and the robot took one of them without asking (class C1, "an exact name hides a shared memory record").
Switch off: key ``enabled`` (legacy ``shared_instances``, default false). No bare record, or no instance ids: nothing changes. No pack data.
Event: lab flag ``shared_instances`` ``{at: canon | issues, name, n}`` each time a bare shared name is found.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import goal_fix, names

SPEC = OptionSpec(
    name="shared_instances",
    keys=(Key("enabled", "bool", False, aliases=("shared_instances",)),),
    hooks=("on_issues",),
    order=81,
    enabled="enabled",
    services=("shared_names",),
    doc="An exact memory name with no instance id that several remembered instances share is asked about, not taken as resolved.",
)


def open_choice(desc: str, memory: dict, said: set, langs=None) -> list:
    """The instances to ask about for the goal object ``desc``: those behind a bare shared record, [] when there are none or the
    person's words (``said``, folded) fit exactly one of them."""
    groups = goal_fix.shared_instances(desc, memory)
    fold = lambda w: names.fold(w, langs)                                                  # noqa: E731
    return [] if len(goal_fix.said_fits(groups, fold(desc), said, fold)) == 1 else groups


class SharedInstances(Option):
    spec = SPEC

    def _found(self, groups: list, at: str, name: str) -> list:
        if groups:
            self.svc.lab("shared_instances", at=at, name=name, n=len(groups))
        return groups

    def shared_names(self, run):
        """Service (asked at ``goal_fix.canon_inventory``): ``shared(name, memory, aliases)`` -> the instances behind a bare shared name."""
        return lambda name, memory, aliases: self._found(goal_fix.shared_instances(name, memory, aliases), "canon", name)

    def on_issues(self, issues, run, goals):
        memory = (getattr(getattr(run, "memory", None), "data", None) or {}).get("objects") or {}
        langs = names.langs_of(run)
        said = names.words(" ".join([str(getattr(run, "task", "") or "")] + [t for w, t in getattr(run, "dialogue", ()) if w == "user"]),
                           langs)                                                       # the request and the person's answers
        out = list(issues)
        for g in goals:
            desc = names.plain(g.get("object"), langs) if g.get("type") in ("on", "in") else ""
            if not desc or any(i.get("kind") == "choice" and i.get("what") == desc for i in out):
                continue
            groups = self._found(open_choice(desc, memory, said, langs), "issues", desc)
            if groups:
                out.append({"kind": "choice", "what": desc, "options": [grp[0] for grp in groups][:4]})
        return out if len(out) != len(issues) else None


register(SPEC, SharedInstances)
