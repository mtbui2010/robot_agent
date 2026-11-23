# tests/legacy_ref

Reference copies of the functions that the core generalization moves (docs/proposals/generalize_core_design.md, "Migration
steps"). A step that moves or rewrites a function of `robot_agent/core/planning/assist_loop.py` may copy the OLD function here
first, verbatim, with the commit it was copied from, and add a differential test: the new function and the copy give the same
answer on the same inputs (a grid of made-up cases). The copy is deleted when the step that needs it is reviewed.

Rules: test code only; nothing in `robot_agent/` imports this package (checked by `tests/golden/test_golden_order.py`); one
module per step (`<step>_<what>.py`); never edited after it is added (it is the old behaviour). Empty after Step S01.
