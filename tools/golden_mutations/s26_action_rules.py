"""S26 mutations, the declared rules of pack actions (checked by tests/golden/scenario_sets/s26_action_rules.py): one behaviour
each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def declared_pre_ignored(mp):
    from robot_agent.core.pack import action_rules
    mp.setattr(action_rules, "violations", lambda rules, step, world: [])


def declared_effects_ignored(mp):
    from robot_agent.core.pack import action_rules
    mp.setattr(action_rules, "apply_effects", lambda rules, step, bound, world: None)


def typed_args_dropped(mp):
    from robot_agent.core.pack import action_rules
    mp.setattr(action_rules, "typed_params", lambda rules, step, skill_params=(): {})


def facts_not_reported(mp):
    from robot_agent.core.planning.base import WorldState
    orig = WorldState.to_dict
    mp.setattr(WorldState, "to_dict", lambda self: {k: v for k, v in orig(self).items() if k != "facts"})


MUTATIONS = {
    "declared_pre_ignored": ("a declared action's unmet `pre` is no violation at the gate or the guard (action_rules.violations)",
                             declared_pre_ignored),
    "declared_effects_ignored": ("a declared action's `effects` do not change the fact store (action_rules.apply_effects)",
                                 declared_effects_ignored),
    "typed_args_dropped": ("a step's typed `args` never reach the skill (action_rules.typed_params)", typed_args_dropped),
    "facts_not_reported": ("the events' world has no `facts` even when the store holds some (WorldState.to_dict)",
                           facts_not_reported),
}
