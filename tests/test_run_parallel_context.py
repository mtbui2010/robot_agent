"""run_parallel hands the calling thread's skill context (log emitter, spoken names, dataset targets) to its threads.

Why: log_data / spoken / dataset_dir keep their state per thread. A skill run by run_parallel (kcare fine_move runs find_grasp
with grip in parallel) used to lose its log_image and msg lines silently. Switch off: not switchable; no context set = old behaviour.
"""
import threading

import pytest

from robot_agent import skills as S
from robot_agent.connect.parallel import run_parallel, run_parallel_check


@pytest.fixture(autouse=True)
def clean_context():
    yield
    S._clear_emitter()
    S.clear_dataset_dir()
    S.clear_stream_dataset()
    S._local.spoken = None


def _emitter():
    got, lock = [], threading.Lock()

    def emit(d):
        with lock:
            got.append(d)
    return got, emit


def test_log_data_from_parallel_threads_reaches_the_emitter():
    got, emit = _emitter()
    S._set_emitter(emit)
    run_parallel([lambda: S.log_data({'k': 'a'}), lambda: S.log_data({'k': 'b'})])
    assert sorted(d['k'] for d in got) == ['a', 'b']


def test_log_image_of_a_skill_in_run_parallel_check_reaches_the_emitter():
    got, emit = _emitter()
    S._set_emitter(emit)

    def find_like():
        S.log_data({'log_image': 'frame'})
        return {'isdone': True}
    ret = run_parallel_check([lambda: {'isdone': True}, find_like])
    assert ret['isdone'] and got == [{'log_image': 'frame'}]


def test_dataset_targets_and_spoken_names_follow_into_the_threads():
    S.set_dataset_dir('/tmp/ds')
    S.set_stream_dataset(True)
    S._local.spoken = {'inputs': ('said', 'real')}
    seen = run_parallel([lambda: (S.dataset_dir(), S.stream_dataset(), S.spoken('inputs', 'real'))])
    assert seen == [('/tmp/ds', True, 'said')]


def test_no_context_behaves_as_before():
    got = run_parallel([lambda: S.dataset_dir(), lambda: S.has_emitter()])
    assert got == [None, False]


def test_results_keep_their_order():
    assert run_parallel([lambda: 1, lambda: 2, lambda: 3]) == [1, 2, 3]


def test_the_caller_keeps_its_own_context_and_a_later_thread_starts_empty():   # passes without the fix too: a guard, not a proof
    got, emit = _emitter()
    S._set_emitter(emit)
    run_parallel([lambda: None])
    leaked = []
    t = threading.Thread(target=lambda: leaked.append(S.has_emitter()))
    t.start(); t.join()
    assert leaked == [False] and S.has_emitter()


def test_a_failing_function_does_not_stop_the_others_logging():
    got, emit = _emitter()
    S._set_emitter(emit)

    def boom():
        raise RuntimeError('x')
    run_parallel([boom, lambda: S.log_data({'k': 'ok'})])
    assert got == [{'k': 'ok'}]


def test_a_nested_run_parallel_passes_the_context_on():
    got, emit = _emitter()
    S._set_emitter(emit)
    run_parallel([lambda: run_parallel([lambda: S.log_data({'k': 'inner'})])])
    assert got == [{'k': 'inner'}]
