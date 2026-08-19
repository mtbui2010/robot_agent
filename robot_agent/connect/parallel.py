"""Run a batch of zero-arg callables concurrently and collect their results.

Used by skills that fire several device commands at once (arm + lift + gripper)
and by the plan executor for ``a::x && b::y`` parallel task groups.
"""

import threading

import numpy as np


def run_parallel(funcs):
    """Run every callable in *funcs* in its own thread; return results in order."""
    results = [None] * len(funcs)

    def wrapper(i, func):
        results[i] = func()

    threads = [threading.Thread(target=wrapper, args=(i, func), daemon=True)
               for i, func in enumerate(funcs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


def run_parallel_check(funcs):
    """:func:`run_parallel` plus an aggregate ``isdone`` over all skill returns.

    A non-dict result counts as success — only an explicit ``{'isdone': False}``
    fails the group.
    """
    rets = run_parallel(funcs=funcs)
    return {
        'isdone': bool(np.all([ret.get('isdone', True) if isinstance(ret, dict) else True
                               for ret in rets])),
        'rets': rets,
    }
