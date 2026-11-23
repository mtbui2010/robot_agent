"""S29 mutations, a goal kind declared with no rules callable (checked by tests/golden/scenario_sets/s29_declared_kinds.py): one
behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def declared_holds_always(mp):
    from robot_agent.core.pack import declared_kinds
    mp.setattr(declared_kinds, "holds", lambda pack, decl, goal, world, placed: True)


def declared_plan_never_refuted(mp):
    from robot_agent.core.pack import declared_kinds
    mp.setattr(declared_kinds, "unmet", lambda pack, decl, goal, belief_after: [])


def unknown_is_ignored(mp):
    from robot_agent.core.pack import verifiers
    mp.setattr(verifiers, "unknown_fails", lambda pack, kind: False)


def phrase_key_ignored(mp):
    from robot_agent.core.pack import declared_kinds
    mp.setattr(declared_kinds, "phrase_entry", lambda pack, kind: None)


MUTATIONS = {
    "declared_holds_always": ("a declared kind's holds list is always True in the belief (declared_kinds.holds)",
                              declared_holds_always),
    "declared_plan_never_refuted": ("no plan refutes a declared kind's holds list (declared_kinds.unmet)",
                                    declared_plan_never_refuted),
    "unknown_is_ignored": ("unknown_is: failure is read as belief: a verifier that cannot tell leaves the goal done "
                           "(verifiers.unknown_fails)", unknown_is_ignored),
    "phrase_key_ignored": ("a declared kind's phrase key is not read: the read-back says '<kind> the <object>' "
                           "(declared_kinds.phrase_entry)", phrase_key_ignored),
}
