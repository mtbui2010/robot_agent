"""S31 mutations, the default kind of a question event (checked by the cassette of tests/golden/scenario_sets/s31_questions.py and
by tests/test_pack_question_kinds.py): one behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def default_kind_ignored(mp):
    from robot_agent.core.pack import questions
    mp.setattr(questions, "tag", lambda event, robot: event)


def default_kind_for_every_robot(mp):
    from robot_agent.core.pack import questions
    orig = questions.with_default_kind
    mp.setattr(questions, "tag", lambda event, robot: orig(event, "mutant.question")
               if event.get("event") == "clarify" and not event.get("kind") else event)


MUTATIONS = {
    "default_kind_ignored": ("a pack's questions_default_kind is not applied to a kind-less clarify (pack.questions.tag)",
                             default_kind_ignored),
    "default_kind_for_every_robot": ("every kind-less clarify gets a kind although the pack sets no key (pack.questions.tag)",
                                     default_kind_for_every_robot),
}
