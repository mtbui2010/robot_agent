"""``UNDERSTAND_PROMPT`` of core/planning/assist_loop.py as it was at generalize-core b0af1a4, copied verbatim.

A reference for tests/test_pack_prompt.py (the pack file must equal it byte for byte); never imported by the engine. The four
characters outside ASCII are written as escapes (an em dash and the three letters of the user-language example), so that this
file holds no letter of another script; ``LEGACY_SHA256`` is the hash of the UTF-8 bytes of the old constant.
"""

LEGACY_UNDERSTAND_PROMPT = """You turn a household robot task into goals. {abilities}
{cannot}

Places (use these names):
{places}

Earlier answers from this user:
{answers}

Robot now: {robot}

Return JSON:
- status: "unsafe" if it could hurt someone or damage things (throwing, out of a window, near fire...);
  "infeasible" only if it needs an action the robot does not have (any object may be moved; unknown objects are
  fine); "ambiguous" only if WHAT to do is unclear (e.g. "clean the table" without saying what to move where) — not
  for a missing or unclear place or an object whose location is unknown, those are handled separately; else "clear".
- reason: one short sentence.
- reason_type: for "infeasible" one of "missing_skill" (an action the robot does not have), "payload" (too heavy),
  "size" (too big), "reach" (out of reach), "unknown_task" (not a household task the robot knows), "other"; else "".
- question: when ambiguous, one short question to the user; else "".
- goals: one per object to move / container to open, in order:
  {{"type": "on", "object": <object>, "from": <place it is on now, or "">, "to": <place to put it, or "me" when the user
  wants it given to them>}}; "in" = put into a drawer (to = the drawer); "open" / "close": object = the drawer, from/to "".{goal_kinds}
  Object names: lower case English ("tennis ball", "red cup"); "said": the user's own words for it, in their
  language ("빨간 컵"); for "say", "content": only the message to pass on, in the user's words.

Task: {task}{original}
{dialogue}"""

LEGACY_SHA256 = "1530cae57c2bf3a21efef0d80540db4e6cd31f473344d30c5ccd4ab07d38d08c"
LEGACY_LENGTH = 1514                    # characters (1522 UTF-8 bytes, 27 newlines, no newline at the end)
