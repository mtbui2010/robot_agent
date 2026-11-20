"""Intent router: keyword STOP / PAUSE / RESUME, ANSWER, LLM with fallback."""

import pytest

from robot_agent.interact.intent_router import Intent, IntentDecision, IntentRouter, classify


@pytest.mark.parametrize('text', [
    'stop', 'Stop!', 'STOP.', 'please stop', 'stop now', 'stop stop stop', 'robot, stop',
    'halt', 'cancel', 'cancel it', "that's enough",
    '멈춰', '멈춰요', '멈춰!', '그만', '그만해', '정지', '스톱', '로봇 멈춰', '제발 그만',
])
def test_stop_keywords(text):
    d = classify(text)
    assert d.intent == Intent.STOP and d.source == 'keyword', text


@pytest.mark.parametrize('text', [
    'shop', 'stopwatch', 'top', 'step', 'stomp', 'the bus stop', 'stop at the door',
    'bring the stop sign', "don't stop", 'do not stop', 'never stop',
    '멈추지 마', '그만두지 말고 계속해', '서울로 가',
    'where is the bus stop near the kitchen',
])
def test_stop_like_not_matched(text):
    assert classify(text).intent != Intent.STOP, text


@pytest.mark.parametrize('text,intent', [
    ('pause', Intent.PAUSE), ('wait', Intent.PAUSE), ('hold on', Intent.PAUSE),
    ('wait a second', Intent.PAUSE), ('잠깐', Intent.PAUSE), ('잠깐만요', Intent.PAUSE),
    ('일시정지', Intent.PAUSE),
    ('resume', Intent.RESUME), ('continue', Intent.RESUME), ('go on', Intent.RESUME),
    ('keep going', Intent.RESUME), ('계속해', Intent.RESUME),
])
def test_pause_resume_keywords(text, intent):
    d = classify(text)
    assert d.intent == intent and d.source == 'keyword', text


def test_answer_when_awaiting():
    d = classify('the red one', awaiting='Which cup?')
    assert d.intent == Intent.ANSWER and d.source == 'awaiting'
    # a stop still wins over an answer
    assert classify('stop', awaiting='Which cup?').intent == Intent.STOP
    assert classify('the red one').intent != Intent.ANSWER


@pytest.mark.parametrize('text,intent', [
    ('bring the mug instead', Intent.MODIFY), ('actually the blue cup', Intent.MODIFY),
    ('컵 말고 물병', Intent.MODIFY),
    ('also bring water', Intent.ADD),
    ('forget the apple', Intent.DROP), ('사과는 빼', Intent.DROP),
    ('what are you doing', Intent.STATUS), ('뭐 해?', Intent.STATUS),
    ('bring me the remote', Intent.NEW),
])
def test_keyword_fallback(text, intent):
    d = classify(text)
    assert d.intent == intent and d.source == 'fallback', (text, d)


def test_llm_used_and_guarded():
    r = IntentRouter(lambda t, c: 'add')
    assert r.classify('and the remote').intent == Intent.ADD
    assert r.classify('and the remote').source == 'llm'
    # the LLM cannot decide STOP / ANSWER: fall back
    assert IntentRouter(lambda t, c: 'stop').classify('bring the mug instead').intent == Intent.MODIFY
    # a broken LLM falls back
    def boom(t, c):
        raise RuntimeError('ollama down')
    assert IntentRouter(boom).classify('forget the apple').source == 'fallback'
    assert IntentRouter(lambda t, c: 'gibberish').classify('x').source == 'fallback'
    dec = IntentDecision(Intent.DROP, 'x', 'llm', 0.7)
    assert IntentRouter(lambda t, c: dec).classify('x') is dec


def test_llm_not_called_for_control():
    calls = []
    r = IntentRouter(lambda t, c: calls.append(t) or 'new')
    for t in ('stop', 'pause', 'resume'):
        r.classify(t)
    r.classify('yes', awaiting='ok?')
    assert calls == []
