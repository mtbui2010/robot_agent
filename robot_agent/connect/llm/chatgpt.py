import os, cv2
from .llm_object import LLMobj
from ..helpers import crop_image

TEXT_MODEL   = "gpt-4o-mini"
VISION_MODEL = "gpt-4o"


class ChatGptClient(LLMobj):
    """OpenAI ChatGPT client.

    API key priority:
      1. ``api_key`` argument
      2. ``OPENAI_API_KEY`` environment variable
    """

    def init(self, api_key=None, text_model=TEXT_MODEL, vision_model=VISION_MODEL, **kwargs):
        from openai import OpenAI
        api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "OpenAI API key not found. "
                "Pass api_key= or set the OPENAI_API_KEY environment variable."
            )
        self.text_model   = text_model
        self.vision_model = vision_model
        self.current_model = text_model
        self._client = OpenAI(api_key=api_key).chat.completions.create

    def make_msg(self, prompt, rgb=None, rgb_path=None, crop_roi=None, **kwargs):
        content = [{"type": "text", "text": prompt}]
        if rgb is not None or rgb_path is not None:
            if rgb is None:
                rgb = cv2.imread(rgb_path)[..., ::-1]
            rgb = crop_image(im=rgb, crop_roi=crop_roi, keep_size=False)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{self.encode_b64(rgb=rgb)}"},
            })
        return {"role": "user", "content": content}

    def _has_images(self, msgs):
        for msg in msgs:
            content = msg.get("content", [])
            if isinstance(content, list):
                if any(c.get("type") == "image_url" for c in content):
                    return True
        return False

    def send_msgs(self, msgs, **kwargs):
        self.current_model = self.vision_model if self._has_images(msgs) else self.text_model
        model = kwargs.get("model", self.current_model)
        return self._client(model=model, messages=msgs).choices[0].message.content


if __name__ == "__main__":
    client = ChatGptClient()
    print(client.chat(prompt="Hello! What model are you?"))
