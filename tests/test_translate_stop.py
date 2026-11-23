"""utils.translate (legacy open-loop path) returns within 1.5 s of a Stop although the LLM call takes longer."""
import threading
import time

from robot_agent import utils
from robot_agent.core import run_control


class Slow:
    def chat(self, **kw):
        time.sleep(3.0)
        return "x"


def test_translate_is_interrupted_by_a_stop(monkeypatch):
    monkeypatch.setattr(utils, "_llm", lambda cfg=None: Slow())
    run_control._CANCEL.clear()
    run_control.begin_run()
    t = threading.Timer(0.3, run_control._CANCEL.set)
    t.start()
    t0 = time.time()
    try:
        utils.translate("hello", "korean")
    except BaseException:
        pass
    finally:
        run_control._CANCEL.clear()
    assert time.time() - t0 < 1.5


def test_translate_returns_the_answer_without_a_stop(monkeypatch):
    class Fast:
        def chat(self, **kw):
            return "annyeong"
    monkeypatch.setattr(utils, "_llm", lambda cfg=None: Fast())
    run_control._CANCEL.clear()
    assert utils.translate("hello", "korean") == "annyeong"
