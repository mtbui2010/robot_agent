"""S31 golden (opt-in ``questions_default_kind``): the two-destinations run of s01_language.py, with a schema-2 pack that
declares a literal kind. The validator's question carries no kind of its own, so its ``clarify`` event now carries the pack's;
a read-back keeps its own kind ``confirm``. Every other cassette sets no key and replays unchanged. A made-up pack on the
made-up fakebot; recorded on the new code (a feature cassette, listed as such in tests/golden/commits/s31.json)."""

from scenarios import house, understood

APPLE = ("on", "apple", "desk", "dining table")
PACK = {"schema": 2, "name": "line", "extends": "household", "assumed": True, "questions_default_kind": "line.question"}

SCENARIOS = [
    (dict(house("question_default_kind", "Put the apple on the dining table and on the counter.", None,
                robot={"answers": ["the dining table"]}),
          pack=PACK,
          llm={"script": [understood(("on", "apple", "desk", "dining table"), ("on", "apple", "desk", "counter"))],
               "default": understood(("on", "apple", "desk", "dining table"))}),
     ["question", "failure"], "two destinations for one object with a pack that declares questions_default_kind: the validator's "
     "question and the follow-up question (neither has a kind of its own) both carry the pack's kind"),
    (dict(house("question_default_kind_readback", "Bring the apple from the desk to the dining table.", [APPLE],
                options={"confirm_task": True}, robot={"answers": ["yes"]}), pack=PACK),
     ["question", "success"], "a read-back answered yes with the same pack: the question site gave the kind confirm, so the "
     "declared default does not replace it"),
]
