"""S31 golden (a feature cassette, recorded on the step's new code): the pack key ``questions_default_kind``. The run of the
cassette ``two_destinations`` (the validator's question has no kind) with a pack that sets the key: that question carries the
declared kind; only the streamed clarify events differ from the base cassette (the run log is the same)."""

from scenarios import house, understood

DS, DT, CT = "desk@study room", "dining table@kitchen", "counter@kitchen"
PACK = {"schema": 2, "name": "s31_kind", "extends": "household", "assumed": True, "questions_default_kind": "line.question"}

SCENARIOS = [
    (dict(house("s31_question_default_kind", "Put the apple on the dining table and on the counter.", None,
                robot={"answers": ["the dining table"]}),
          llm={"script": [understood(("on", "apple", "desk", "dining table"), ("on", "apple", "desk", "counter"))],
               "default": understood(("on", "apple", "desk", "dining table"))},
          pack=PACK),
     ["question", "failure"], "two destinations for one object with questions_default_kind set: both questions (the validator's and the "
     "read-back's issue, neither has a kind of its own) carry the declared kind in the stream; the run log is as without the key"),
]
