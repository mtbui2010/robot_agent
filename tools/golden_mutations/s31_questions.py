"""S31 mutations, the default kind of a kind-less question (checked by tests/golden/scenario_sets/s31_questions.py): one behaviour
each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def default_kind_ignored(mp):
    from robot_agent.core.pack import questions
    mp.setattr(questions, "for_run", lambda event, robot: event)


def default_kind_overrides_given(mp):
    from robot_agent.core.pack import questions

    def for_run(event, robot):
        kind = questions.default_kind(robot.pack)
        return {**event, "kind": kind} if kind and event.get("event") == "clarify" else event
    mp.setattr(questions, "for_run", for_run)


def default_kind_without_a_pack(mp):
    from robot_agent.core.pack import questions
    mp.setattr(questions, "default_kind", lambda pack: "mutant")


MUTATIONS = {
    "default_kind_ignored": ("a pack's questions_default_kind is never applied (RunContext.ev leaves the event as built)",
                             default_kind_ignored),
    "default_kind_overrides_given": ("the default kind replaces the kind a question site gave (the read-back's `confirm`)",
                                     default_kind_overrides_given),
    "default_kind_without_a_pack": ("a pack that declares no default kind still labels its kind-less questions",
                                    default_kind_without_a_pack),
}
