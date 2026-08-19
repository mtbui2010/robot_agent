from abc import ABC, abstractmethod
import cv2, base64, os, glob
from ..helpers import Timer, findNumdersInString
from ..paths import log_dir as _log_dir
from ..serde import read_json, write_json


# Per-tag chat history and the chat_guide response cache. Resolved at call time
# (not import time) so they land in the robot's runtime log dir rather than
# inside the installed package — see robot_agent.connect.paths.
history_dir = lambda: str(_log_dir('llm', 'history'))
cache_dir = lambda: str(_log_dir('llm', 'guide_cache'))

fname = lambda p: os.path.splitext(os.path.split(p)[-1])[0]

class LLMobj(ABC):
    """Abstract base class for all LLM backends.

    Args:
        max_history_turns (int | None): Keep at most this many *user+assistant*
            turn pairs per tag.  ``None`` or ``0`` means unlimited (default
            ``50``).  Set a small value (e.g. ``10``) for long-running robots
            to cap memory and API token usage.

    Example::

        client = LLamaClient(max_history_turns=20)
        client.chat(prompt='hello', tag='session1')
    """

    def __init__(self, max_history_turns: int | None = 50, **kwargs):
        # max_history_turns: number of user+assistant message *pairs* to keep.
        # Each pair = 2 messages, so the window cap = max_history_turns * 2.
        self.max_history_turns = max_history_turns or 0
        self.read_history()
        self.current_tag, self.current_model = 'untagged', None
        self.init(**kwargs)

    @abstractmethod
    def init(self, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def make_msg(self, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def send_msgs(self, **kwargs):
        raise NotImplementedError

    def make_msgs(self, **kwargs):
        msg = self.make_msg(**kwargs)
        tag = kwargs.get('tag', 'untagged')

        if tag == 'untagged':
            # No persistent context for untagged calls
            self.history[tag] = [msg]
        else:
            history = self.history.get(tag, []) + [msg]

            # --- sliding window: evict oldest messages when over the cap ---
            if self.max_history_turns:
                cap = self.max_history_turns * 2   # each turn = user + assistant
                if len(history) > cap:
                    history = history[-cap:]

            self.history[tag] = history

        return self.history[tag]
        
    def chat(self, **kwargs):
        timer = Timer()
        msgs = self.make_msgs(tag=self.current_tag, **kwargs)
        timer.pin_time('make_msgs')
        ret = self.send_msgs(msgs=msgs, **kwargs)
        timer.pin_time('get_return')
        self.write_history(tag=kwargs['tag'] if 'tag' in kwargs else 'untagged')
        print(f'[{self.current_model}/{self.current_tag}]: {timer.pin_times_str}')
        return ret
        
    def read_history(self,):
        try:
            self.history = {fname(p): read_json(p)
                            for p in glob.glob(os.path.join(history_dir(), '*.json'))}
        except Exception as e:
            print(f'[LLM] Failed to read history: {e}')
            self.history = {}

    def write_history(self, tag: str) -> None:
        """Persist the history for *tag* to disk as JSON."""
        if tag not in self.history:
            return
        try:
            write_json(os.path.join(history_dir(), f'{tag}.json'), self.history[tag])
        except Exception as e:
            print(f"[LLM] Failed to write history for tag '{tag}': {e}")

    def reset_history(self, tag='untagged'):
        if tag=='all' or not hasattr(self, 'history'):
            self.history = dict()
            return
        self.history[tag] = []
    
    def encode_b64(self, rgb):
        buffer = cv2.imencode('.jpg', rgb[...,::-1])[-1]
        return base64.b64encode(buffer).decode('utf-8')
    
    def chat_findobj(self, obj_name=None, **kwargs):
        if obj_name is None:
            prompt = 'List object names in the image. Simply return list only using [format object_0, object_1, ..., object_n]'
        else:
            prompt= f'How many {obj_name} in the image? Simply return number only.'
        ret = self.chat(prompt=prompt, **kwargs).replace('.', '')
        
        if obj_name is None:
            return [el for el in ret[1:-1].split(',')]
        
        numInString = findNumdersInString(ret)
        return int(numInString[0]) if len(numInString)>0 else 0
        
    
    def chat_guide(self, prompt, guide, reuse=False, **kwargs):
        """Freeform planning call: substitute *prompt* into *guide* and chat.

        *guide* is the active planner guide (see
        :mod:`robot_agent.core.guide_manager`) — its ``COMMAND_HERE`` marker is
        replaced by the normalised prompt. With ``reuse=True`` a previous answer
        for the same prompt is read back from the on-disk cache instead of
        hitting the model.
        """
        words_to_remove = ['', '.', ',', 'the', 'a']
        prompt = ' '.join(el for el in prompt.lower().split(' ') if el not in words_to_remove)
        filepath = os.path.join(cache_dir(), prompt.replace(' ', '_').replace('/', '_'))

        toread = reuse and os.path.exists(filepath)
        with open(filepath, 'r' if toread else 'w') as f:
            if toread:
                return f.read()
            kwargs['prompt'] = guide.replace('COMMAND_HERE', prompt)
            ret = self.chat(**kwargs)
            f.writelines(ret)
            return ret
