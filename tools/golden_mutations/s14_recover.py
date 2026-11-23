"""S14 mutations of the methods moved to phases/recover.py (RecoverPhase): one behaviour each, by monkeypatch on the MIXIN
(so a mutation that no golden catches would also show that AssistLoop no longer resolves the name through RecoverPhase).
The golden replays that reach them are the W0 equivalence cassettes; tools/golden_mutation_check.py loads this file.
No golden reaches _reset_container (it needs the manifest's planner.recovery.blocked_putin, which the set harness cannot
set): its proof is the syntax-tree identity (tests/test_recover_phase.py) and the unit tests of tests/test_sage_lab_r3.py."""


def _recover_phase():
    from robot_agent.core.planning.phases.recover import RecoverPhase
    return RecoverPhase


def policy_rung_skipped(mp):
    """The recovery policy's own rung never runs (no wait for health, no re-approach, no stop of WAIT_HEALTH / STOP)."""
    def apply_policy(self, r, st, skill, code, rule, reason, device):
        return None
        yield                                                    # a generator, as the rung is
    mp.setattr(_recover_phase(), "_apply_policy", apply_policy)


def success_says_nothing(mp):
    """A step that succeeded announces nothing: its step_done event loses the spoken line."""
    RP = _recover_phase()
    orig = RP._step_succeeded

    def step_succeeded(self, r, st, rec):
        gen = orig(self, r, st, rec)
        sent = None
        while True:
            try:
                ev = gen.send(sent)
            except StopIteration as e:
                return e.value
            if isinstance(ev, dict) and ev.get("event") == "step_done" and ev.get("status") == "success":
                ev = {k: v for k, v in ev.items() if k != "say"}
            sent = yield ev
    mp.setattr(RP, "_step_succeeded", step_succeeded)


MUTATIONS = {
    "policy_rung_skipped": ("the policy rung of a failed step never runs (RecoverPhase._apply_policy gives up at once)",
                            policy_rung_skipped),
    "success_says_nothing": ("a succeeded step announces nothing (RecoverPhase._step_succeeded, its step_done say)",
                             success_says_nothing),
}
