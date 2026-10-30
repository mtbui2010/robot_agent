# robot_agent docs

Design and reference documentation of the core (rule 9 in `../CLAUDE.md`).
One topic per file, named after the module or feature it covers.

The planning-layer specs written so far still live in the kcare_robot repo and
are to be moved here:

- `kcare_robot/docs/CLOSED_LOOP_ARCHITECTURE.md`: closed-loop planning (GRACE / SAGE via embodiplan)
- `kcare_robot/docs/ACTION_MAPPER_SPEC.md`: plan step -> skill call, world-state effects
- `kcare_robot/docs/TRACKING_VERIFY_VOICE.md`: run records, layered verification, announcements

Tests: `make test` (see `../tests/conftest.py` for the fake robot, LLM and planner).
