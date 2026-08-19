import os, cv2
from .llm_object import LLMobj
from ..helpers import crop_image

DEFAULT_TEXT_MODEL   = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.0-flash")
DEFAULT_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-2.0-flash")


class GeminiClient(LLMobj):
    """Google Gemini client (uses `google.generativeai`).

    API key priority:
      1. ``api_key`` argument
      2. ``GOOGLE_API_KEY`` environment variable
    """

    def init(self, api_key=None, text_model=None, vision_model=None, **kwargs):
        import google.generativeai as genai
        api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "Google API key not found. "
                "Pass api_key= or set the GOOGLE_API_KEY environment variable."
            )
        genai.configure(api_key=api_key)
        self._genai = genai
        self.text_model = text_model or DEFAULT_TEXT_MODEL
        self.vision_model = vision_model or DEFAULT_VISION_MODEL
        self.current_model = self.text_model

    def make_msg(self, prompt, rgb=None, rgb_path=None, crop_roi=None, **kwargs):
        # Gemini SDK accepts a list of "parts" (str + PIL image / blob).
        parts = [prompt]
        if rgb is not None or rgb_path is not None:
            if rgb is None:
                rgb = cv2.imread(rgb_path)[..., ::-1]
            rgb = crop_image(im=rgb, crop_roi=crop_roi, keep_size=False)
            jpg = cv2.imencode('.jpg', rgb[..., ::-1])[1].tobytes()
            parts.append({"mime_type": "image/jpeg", "data": jpg})
        return {"role": "user", "parts": parts}

    def _has_images(self, msgs):
        for m in msgs:
            for p in m.get("parts", []):
                if isinstance(p, dict) and "mime_type" in p:
                    return True
        return False

    def send_msgs(self, msgs, **kwargs):
        self.current_model = self.vision_model if self._has_images(msgs) else self.text_model
        model_name = kwargs.get("model", self.current_model)
        model = self._genai.GenerativeModel(model_name)
        # Gemini's generate_content takes the conversation list directly.
        resp = model.generate_content(msgs)
        return resp.text


if __name__ == "__main__":
    client = GeminiClient()
    print(client.chat(prompt="Hello! What model are you?"))
