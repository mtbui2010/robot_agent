"""``GET /tts?text=…&lang=…`` -> the sentence as audio/mpeg (gTTS), for clients that record the robot's voice.

``lang`` defaults to the robot's manifest language. 400 for an empty or too long text, 503 when the speech could not be
made (no gTTS, no internet on the backend machine, a language gTTS does not have).
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from ..core.languages import default_language
from ..core.tts import MAX_CHARS, TtsError, synthesize_mp3

router = APIRouter()


@router.get('/tts')
def tts(text: str = Query(..., description='what to say'),
        lang: Optional[str] = Query(None, description="language code ('ko', 'en'); default: the manifest's")):
    if not text.strip():
        raise HTTPException(400, 'text is empty')
    if len(text) > MAX_CHARS:
        raise HTTPException(400, f'text is longer than {MAX_CHARS} characters')
    try:
        mp3 = synthesize_mp3(text.strip(), (lang or default_language()))
    except TtsError as e:
        raise HTTPException(503, f'text to speech failed: {e}')
    return Response(content=mp3, media_type='audio/mpeg', headers={'Cache-Control': 'max-age=3600'})
