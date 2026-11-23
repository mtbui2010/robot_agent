"""S14 mutations, recovery policies chosen by name (checked by tests/golden/scenario_sets/s14_recovery_policies.py and the
ladder cassettes of every set): one behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def by_kind_ignored(mp):
    """The goal kinds of the run are not read: by_kind never chooses a policy (core/pack/recovery.policy_name)."""
    from robot_agent.core.pack import recovery
    orig = recovery.policy_name
    mp.setattr(recovery, "policy_name", lambda pack, action, kinds=(): orig(pack, action, ()))


def stop_policy_is_planner(mp):
    """The builtin stop is read as the planner (core/pack/recovery.policy)."""
    from robot_agent.core.pack import recovery
    orig = recovery.policy
    mp.setattr(recovery, "policy", lambda pack, name: orig(pack, "planner" if name == "stop" else name))


def registered_policy_ignored(mp):
    """A declared policy's registered callable is never run: every named policy is the planner (core/pack/recovery.policy)."""
    from robot_agent.core.pack import recovery
    orig = recovery.policy
    mp.setattr(recovery, "policy", lambda pack, name: orig(pack, name if name in ("planner", "stop", "tree") else "planner"))


def pick_ladder_never_replans(mp):
    """The Pick ladder's may_replan is read as false (core/pack/recovery.ladder_of): no new plan after a grasp nobody helped."""
    from robot_agent.core.pack import recovery
    orig = recovery.ladder_of

    def ladder_of(pack, action):
        got = orig(pack, action)
        return (got[0], False) if got else got
    mp.setattr(recovery, "ladder_of", ladder_of)


MUTATIONS = {
    "by_kind_ignored": ("by_kind never chooses a policy (core/pack/recovery.policy_name)", by_kind_ignored),
    "stop_policy_is_planner": ("the builtin stop is read as the planner (core/pack/recovery.policy)", stop_policy_is_planner),
    "registered_policy_ignored": ("no registered recovery callable runs: every named policy is the planner "
                                  "(core/pack/recovery.policy)", registered_policy_ignored),
    "pick_ladder_never_replans": ("the Pick ladder never ends in a replan (core/pack/recovery.ladder_of)",
                                  pick_ladder_never_replans),
}
