"""Option ``trace``: one JSONL file per run, built from what the engine already makes (docs/trace.md). Made-up objects and skills only."""

import json
import logging

import numpy as np

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options.dispatch import NULL
from robot_agent.observe import llm as observed
from robot_agent.observe.llm import RecordingLLM
from robot_agent.options import trace as T

DS, DT = "desk@study room", "dining table@kitchen"
TASK = "Bring the zorb cup from the desk to the dining table."
READ = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "zorb cup", "from": "desk", "to": "dining table"}]}


class GripRobot(FakeRobot):
    """The first pick of each object fails with a number and a camera frame in its result (a made-up grip check)."""

    def _exec(self, skill, o, params):
        if skill == "pick" and not self.counts.get(("first_pick", o)):
            self.counts[("first_pick", o)] = 1
            return {"isdone": False, "msg": "grasp failed: grip check low", "grip_ratio": 0.42, "frame": np.zeros((48, 64, 3))}
        return super()._exec(skill, o, params)


def play(make_assist, env, robot_cls=GripRobot, **opts):
    robot = robot_cls(env, {"zorb cup": DS})
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find zorb cup", "Pick zorb cup", f"MoveTo {DT}", f"Place {DT}"))
    loop = make_assist(robot, RecordingLLM(FakeLLM(default=READ)), planner, **opts)
    return loop, run_task(loop, TASK)


def rows(folder):
    files = list(folder.rglob("trace.jsonl"))
    assert len(files) == 1, files
    return [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]


def scrub(x):
    """The events without their clock fields (run id, times) at any depth, and without the start reconcile (the second run of a
    test starts where the first one left the robot)."""
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "age_s")}
    return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")] if isinstance(x, list) else x


# ── off / empty ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_trace_writes_no_file_and_on_it_changes_no_event_off(make_assist, env, tmp_path):
    loop, off = play(make_assist, env)
    assert not any(tmp_path.rglob("trace.jsonl")) and "trace" not in {o.spec.name for o in loop.options.active}
    _l, on = play(make_assist, env, trace="1", trace_dir=str(tmp_path / "tr"))
    same = [json.dumps(scrub(e), sort_keys=True, default=repr) for e in (on, off)]
    assert same[0] == same[1] and done(on)["status"] == "success"
    assert observed._LISTENERS == []                       # the run's LLM listener is gone with the run


def test_trace_run_with_nothing_to_record_is_a_valid_file_with_the_header_only_empty(tmp_path):
    run = type("Run", (), {"run_id": "r-empty", "task": "", "lang": "xx", "robot": None})()
    opt = T.Trace({"enabled": True, "dir": str(tmp_path)}, type("Svc", (), {"lab": lambda self, *a, **k: None})())
    opt.on_start(run)
    opt.on_event(None)                                     # the stream ended with nothing in it
    (head,) = rows(tmp_path)
    assert head["k"] == "header" and head["schema"] == T.SCHEMA and head["run_id"] == "r-empty" and head["t"] == 0.0


def test_trace_tap_is_the_generator_itself_when_nobody_watches():
    gen = (e for e in ())
    assert NULL.tap("on_event", gen) is gen and NULL.tap_push("on_event", print) is print


# ── pure record builders ─────────────────────────────────────────────────────────────────────────────────────────────
def test_trace_summarise_keeps_numbers_and_unicode_and_replaces_arrays_and_images():
    out = T.summarise({"ratio": 0.42, "n": 3, "ok": True, "none": None, "word": "çà ñø ✓", "img": "A" * 9000,
                       "raw": b"\x00" * 10, "pose": np.array([0.6, 0.05, 0.8]), "depth": np.zeros((4, 5), np.float32),
                       "pts": list(range(100))})
    assert out["ratio"] == 0.42 and out["n"] == 3 and out["ok"] is True and out["none"] is None and out["word"] == "çà ñø ✓"
    assert out["img"] == {"_len": 9000} and out["raw"] == {"_bytes": 10}
    assert out["pose"] == [0.6, 0.05, 0.8] and out["depth"] == {"_shape": [4, 5], "_dtype": "float32"}
    assert out["pts"]["_list"] == 100 and out["pts"]["head"] == list(range(8))
    json.dumps(out, ensure_ascii=False)


def test_trace_event_record_drops_the_world_and_audio_but_keeps_the_result():
    ev = {"event": "step_done", "index": 2, "result": {"isdone": False, "grip_ratio": 0.42}, "world": {"x": 1}, "say_audio": "QQ=="}
    rec = T.event_record(ev, 2)
    assert rec == {"k": "event", "step": 2, "ev": "step_done", "data": {"index": 2, "result": {"isdone": False, "grip_ratio": 0.42}}}
    assert "world" in T.event_record({"event": "done", "world": {"x": 1}}, 2)["data"]


def test_trace_llm_record_has_purpose_duration_and_sizes():
    call = {"source": "robot_agent", "purpose": "understand", "t0": 105.0, "latency_ms": 812.5, "model": "toy-7b",
            "request": {"method": "chat", "prompt": "abcd", "messages": [{"content": "xy", "images": ["Z" * 50]}]}, "response": "{}"}
    rec = T.llm_record(call, 100.0, 3, None)
    assert (rec["purpose"], rec["t"], rec["ms"], rec["model"], rec["step"]) == ("understand", 5.0, 812.5, "toy-7b", 3)
    assert rec["prompt_chars"] == len("chat") + 4 + 2 and rec["reply_chars"] == 2
    assert T.llm_record({}, 0.0, None, None)["purpose"] == "other"


def test_trace_choice_of_names_what_the_loop_did_after_a_failure():
    assert T.choice_of({"event": "recover", "kind": "relook"}, 2) == "recover:relook"
    assert T.choice_of({"event": "done", "status": "failed"}, 2) == "stop:failed"
    assert T.choice_of({"event": "step_start", "index": 2}, 2) == "retry"
    assert T.choice_of({"event": "step_start", "index": 3}, 2) == "next_step"
    assert T.choice_of({"event": "replan"}, 2) == "replan" and T.choice_of({"event": "clarify"}, 2) == "ask"
    assert T.choice_of({"event": "status"}, 2) is None


def test_trace_llm_listener_sees_calls_outside_a_recorded_run_and_a_broken_one_is_ignored():
    seen = []
    stop = observed.listen(seen.append)
    stop_bad = observed.listen(lambda c: 1 / 0)
    try:
        assert observed.call("robot_agent", lambda: "fine", {"prompt": "p"}, model="toy") == "fine"
    finally:
        stop(), stop_bad()
    assert [(c["source"], c["response"], c["model"]) for c in seen] == [("robot_agent", "fine", "toy")]
    assert observed._LISTENERS == []


# ── through the real loop ────────────────────────────────────────────────────────────────────────────────────────────
def test_trace_loop_final_failed_pick_has_result_numbers_recovery_rule_and_llm_purposes(make_assist, env, tmp_path):
    """The failure class: why a pick failed was only in a picture and in separate logs. Here the file alone answers it."""
    _l, evs = play(make_assist, env, trace="1", trace_dir=str(tmp_path))
    assert done(evs)["status"] == "success"                # the final outcome: recovered, the cup is on the table
    rs = rows(tmp_path)
    assert rs[0]["k"] == "header" and all(r["v"] == T.SCHEMA for r in rs) and [r["t"] for r in rs] == sorted(r["t"] for r in rs)
    failed = next(r for r in rs if r["k"] == "event" and r["ev"] == "step_done" and r["data"]["status"] == "failed")
    assert failed["data"]["result"]["grip_ratio"] == 0.42 and failed["data"]["result"]["frame"] == {"_shape": [48, 64, 3], "_dtype": "float64"}
    rec = next(r for r in rs if r["k"] == "recovery")
    assert (rec["action"], rec["object"], rec["step"]) == ("Pick", "zorb cup", failed["step"])
    assert rec["code"] and rec["rule"]["retry_allowed"] in (True, False) and "grip check low" in rec["reason"]
    assert rec["chose"].startswith(("recover:", "replan", "retry", "ask", "search"))
    llm = [r for r in rs if r["k"] == "llm"]
    assert llm and {r["purpose"] for r in llm} == {"understand"} and all(r["ms"] is not None and r["prompt_chars"] > 0 for r in llm)
    assert rs[-1]["k"] == "event" and rs[-1]["ev"] == "done" and "world" in rs[-1]["data"]


def test_trace_loop_final_write_error_does_not_stop_the_run(make_assist, env, tmp_path, caplog):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("a file where the trace folder should be")
    with caplog.at_level(logging.WARNING, logger=T.__name__):
        _l, evs = play(make_assist, env, trace="1", trace_dir=str(blocker))
    assert done(evs)["status"] == "success"
    assert len([r for r in caplog.records if "trace: cannot write" in r.getMessage()]) == 1


def test_trace_lab_entries_carry_the_step_and_its_own_flag_is_not_traced(tmp_path):
    run = type("Run", (), {"run_id": "r-lab", "task": "t", "lang": "xx", "robot": None})()
    opt = T.Trace({"enabled": True, "dir": str(tmp_path)}, type("Svc", (), {"lab": lambda self, *a, **k: None})())
    opt.on_start(run)
    opt.on_event({"event": "step_start", "index": 4})
    opt.on_lab("toy_option", {"why": "made up", "n": 2})
    opt.on_lab("trace", {"path": "x"})
    opt.on_event(None)
    lab = [r for r in rows(tmp_path) if r["k"] == "lab"]
    assert lab == [{**lab[0], "step": 4, "flag": "toy_option", "data": {"why": "made up", "n": 2}}] and len(lab) == 1
