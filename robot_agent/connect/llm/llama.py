import os, cv2
from .llm_object import LLMobj
from ..helpers import Timer, crop_image

DEFAULT_URL          = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_TEXT_MODEL   = os.environ.get("OLLAMA_TEXT_MODEL", "llama3.2")
DEFAULT_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "llama3.2-vision")


class LLamaClient(LLMobj):
    """Ollama/LLaMA client.

    URL priority:
      1. ``url`` argument
      2. ``OLLAMA_URL`` environment variable  (default: http://localhost:11434)
    """

    def init(self, url=None, text_model=None, vision_model=None, **kwargs):
        from ollama import Client
        self.url          = url          or DEFAULT_URL
        self.text_model   = text_model   or DEFAULT_TEXT_MODEL
        self.vision_model = vision_model or DEFAULT_VISION_MODEL
        self.chatclient   = Client(host=self.url).chat
        print(f"LLaMA client → {self.url}")
        print(self.chat(prompt="hello"))

    def make_msg(self, prompt, rgb=None, rgb_path=None, crop_roi=None, **kwargs):
        msg = kwargs['guide'].replace('COMMAND_HERE',prompt) if 'guide' in kwargs else prompt
        msg = {'role': 'user', 'content': msg}
        if rgb is not None or rgb_path is not None:
            rgb = cv2.imread(rgb_path)[...,::-1] if rgb is None else rgb
            rgb = crop_image(im=rgb, crop_roi=crop_roi, keep_size=False)
            msg['images'] = [self.encode_b64(rgb=rgb),]
        return msg
    
    def image_contained(self, msgs):
        for msg in msgs:
            if 'images' in msg:
                return True
        return False
        
    
    def send_msgs(self, msgs, **kwargs):
        self.current_model = self.vision_model if self.image_contained(msgs=msgs) else self.text_model
        model = kwargs.get('model', self.current_model)
        return self.chatclient(model=model, messages=msgs, format=kwargs.get('format', None)).message.content
        

if __name__=='__main__':
    
    llamaclient = LLamaClient(url='192.168.1.18:9090')
    aa = 1
    # llamaclient.demo_chatloop()
    # ret = llamaclient.chat(prompt='How many cup in the image? Simply return number.',
    #                  rgb_path='/media/keti/workdir/projects/data/care_robot/logs/vision/20241216160408599135_20241216161440141380_rgb.png')
    # print(llamaclient.chat(prompt='hello'))
    # ret = llamaclient.chat_findobj(
    #     # obj_name='pen',  
    #     rgb_path='/media/keti/workdir/projects/data/care_robot/logs/vision/20241216160408599135_20241216161440141380_rgb.png')
    # ret = llamaclient.chat_guide(prompt='move orange on the shelf to table', reuse=True)
    # ret = llamaclient.chat_guide(prompt='give me orange', reuse=False)
    # print(ret)
    # llamaclient.demo(rgb_path='/media/keti/workdir/projects/data/care_robot/logs(1)/vision/20241220102327856283_20241220102503831801_rgb_hand.png')
        