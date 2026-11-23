"""S26 mutations, the declared rules of pack actions (checked by the cassettes of tests/golden/scenario_sets/s26_action_rules.py):
one behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def pre_ignored(mp):
    from robot_agent.core.pack import action_rules
    mp.setattr(action_rules, "violations", lambda rules, step, world: [])


def effects_ignored(mp):
    from robot_agent.core.pack import action_rules
    mp.setattr(action_rules, "apply_effects", lambda rules, step, bound, world: None)


def typed_args_dropped(mp):
    from robot_agent.core.pack import action_rules
    mp.setattr(action_rules, "typed_params", lambda rules, step, accepted: {})


def facts_not_reset(mp):
    from robot_agent.core.planning import base
    mp.setattr(base.WorldState, "clear_facts", lambda self: None)


def facts_key_always(mp):
    from robot_agent.core.planning import base
    mp.setattr(base.WorldState, "to_dict", _with_facts_always(base.WorldState.to_dict))


def _with_facts_always(orig):
    def to_dict(self):
        out = orig(self)
        out.setdefault("facts", {})
        return out
    return to_dict


def rules_never_active(mp):
    from robot_agent.core.pack import action_rules
    mp.setattr(action_rules, "of_robot", lambda robot: action_rules.EMPTY)


MUTATIONS = {
    "declared_pre_ignored": ("the unmet pre of a declared action is not reported (pack.action_rules.violations)", pre_ignored),
    "declared_effects_ignored": ("the effects of a declared action do not change the fact store (pack.action_rules.apply_effects)",
                                 effects_ignored),
    "typed_args_dropped": ("a step's typed args never reach the skill (pack.action_rules.typed_params)", typed_args_dropped),
    "facts_not_reset_at_run_start": ("a fact from before the run is kept and satisfies a precondition (WorldState.clear_facts)",
                                     facts_not_reset),
    "facts_key_always_written": ("the world snapshot carries an empty facts key (WorldState.to_dict)", facts_key_always),
    "declared_rules_never_active": ("a run never makes its robot's declared rules active (pack.action_rules.of_robot)",
                                    rules_never_active),
}
