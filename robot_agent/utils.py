
from pathlib import Path
import os, numpy as np, cv2, glob, copy
from pyconnect.utils import str2dict, dict2str, run_parallel, init_llm_client, evaluate
import numpy as np
import io, threading, subprocess

try:
    from langdetect import detect as detect_lang
    from gtts import gTTS
    from pydub import AudioSegment
    import sounddevice as sd
    import soundfile as sf

except Exception as e:
    print(e)

from robot_agent.skill_configs import LLM_SERVERS, LIFT_CONFIGS, KR2EN, EN2KR, DO_TEXT2VOICE, ENV, UTIL_AGENT_CONFIGS

root_dir = Path(__file__).parent
test_dir = os.path.join(root_dir, 'test_images')


task2act = lambda task: task.split('--')[0]
task2agent = lambda node, task: node.agents[task2act(task)]
get_attrs = lambda x, atrs: {el:getattr(x, el) for el in atrs}
set_atrrs = lambda x, adict: ([setattr(x,k,v) for k,v in adict.items() if hasattr(x, k)], x)[-1]
update_dict = lambda adict, x: (adict.update(x), adict)[-1]

llm_client=None


def voice2text(audio_obj):
    from robot_agent.state import current
    client = current().dm.get_client('vlms')
    if client is None:
        raise RuntimeError("TCP connect 'vlms' not registered — add it in the Connection panel")
    return client.send({'audio_obj': audio_obj, 'detector':'audio'})

def describe_object(rgb, to_language='korean'):
    global llm_client
    if llm_client is None:
        llm_client = init_llm_client(cfg=LLM_SERVERS['llama'])
    return llm_client.chat(rgb=rgb, prompt=f'''What is this? Largest text in this image?
                                Answer in short, daily and native spoken {to_language}.
                                Ouput result only.
                                ''')

def translate(text, to_language='korean'):
    global llm_client
    if llm_client is None:
        llm_client = init_llm_client(cfg=LLM_SERVERS['llama'])
    return llm_client.chat(prompt=f'Translate to daily {to_language}. Output result only: {text}')

def text2voice(text, lang=None, run_thread=True, slow=False):
    if not DO_TEXT2VOICE:
        return

    lang = detect_lang(text) if lang is None else lang
    lang = 'en' if lang!='ko' else lang

    def func():
        try:
            spk = gTTS(text=text, lang=lang, slow=slow)
            mp3_fp = io.BytesIO()
            spk.write_to_fp(mp3_fp)
            mp3_fp.seek(0)

            audio = AudioSegment.from_file(mp3_fp, format="mp3")
            audio = audio.set_channels(1).set_frame_rate(44100)
            samples = np.array(audio.get_array_of_samples()).astype(np.float32)
            samples /= np.iinfo(audio.array_type).max

            sd.play(samples, samplerate=audio.frame_rate)
            sd.wait()
            return True
        except Exception as e:
            print(e)
            return False
    if run_thread:
        threading.Thread(target=func, daemon=True).start()
    else:
        func()

def decode_prompt(structed_prompt):
    target, destination_loc= structed_prompt.split('::')[-1].split('>>')

    splits = target.split('@')
    obj_name, target_loc = (splits[0], splits[1:]) if len(splits)>1 else (splits[0], None)

    splits = destination_loc.split('@')
    destination = " ".join(splits[::-1])
    return obj_name, target_loc, destination

def correct_text(text, lang='korean'):
    global llm_client
    if llm_client is None:
        llm_client = init_llm_client(cfg=LLM_SERVERS['llama'])

    return llm_client.chat(prompt=f'''
                        Correct the following sentence, make it short, direct and native spoken {lang}.
                        Ouput result only: {text}
                        ''')

def correct_noun(noun, lang='ko', translate=False):
    noun = noun.strip().lower()
    word_dict =  EN2KR if lang=='ko' else KR2EN
    if noun in word_dict:
        return word_dict[noun]

    if not translate:
        return noun

    global llm_client
    if llm_client is None:
        llm_client = init_llm_client(cfg=LLM_SERVERS['llama'])

    return llm_client.chat(prompt=f'''
                        Translate the follwing noun to {lang}, make it short, direct and native.
                        Ouput result only: {noun}
                        ''')

def correct_loc(loc, lang='korean'):
    global llm_client
    if llm_client is None:
        llm_client = init_llm_client(cfg=LLM_SERVERS['llama'])

    return llm_client.chat(prompt=f'''
                        Translate the follwing location to {lang}, make it short, direct and native.
                        Ouput result only: {loc}
                        ''')


def create_reply(obj_name, target_loc, destination):
    global llm_client
    if llm_client is None:
        llm_client = init_llm_client(cfg=LLM_SERVERS['llama'])

    return llm_client.chat(prompt=f'''
                        Create a short, direct, daily, informal, and native spoken confirmation from a request
                        with target object "{obj_name}", target location "{target_loc}",
                        and destination location "{destination}". Begin with got it and include I will.
                        Ouput result with text only.
                        ''')

def create_replies(plan):
    return '. '.join([create_reply(*decode_prompt(line)) for line in plan.split('\n')])


def create_request(obj_name, target_loc, destination):
    global llm_client
    if llm_client is None:
        llm_client = init_llm_client(cfg=LLM_SERVERS['llama'])
    return llm_client.chat(prompt=f'''
                        Create a short, direct and native spoken request
                        with target object "{obj_name}", target location "{target_loc}",
                        and destination location "{destination}".
                        Ouput result only.
                        ''')
def create_requests(plan):
    return '. '.join([create_request(*decode_prompt(line)) for line in plan.split('\n')])

def url2hostport(url):
    ret = url.split(':')
    return [ret[0], int(ret[1])]


def agent_log_data(agent, data, msg_type='sent'):
    agent.log_msg(data=data, msg_type=msg_type)


state2liftprompt = lambda state: f'lift::{round(state["/elevation/state"]["current_position"])}\n' if '/elevation/state' in state else ''
state2gripprompt = lambda state: f'grip::{state["/gripper/state"]["motor_position"]}\n' if '/gripper/state' in state else ''
def state2armmoveprompt(state):
    agent_name = '/xarm/robot_states'
    if agent_name not in state:
        return ''

    x, y, z = np.load(state[agent_name]['pose'])[:3]
    return f'arm_move::{x:.2f},{y:.2f},{z:.2f}\n'

def state2imagepaths(state):
    return '\n'.join([f'{k}::{v}' for k,v in state.items() if 'image_raw' in k])

def get_actions_from_trajectory(trajectory=None):
    states = get_states_from_trajectory(trajectory=trajectory)
    plans = []
    for state in states:
        plan = ''
        plan += state2liftprompt(state=state)
        plan += state2gripprompt(state=state)
        plan += state2armmoveprompt(state=state)
        plan += state2imagepaths(state=state)

        plans.append(plan)

    return plans


n2colormap = lambda n: cv2.applyColorMap((np.arange(n) / n * 255).astype('uint8'), cv2.COLORMAP_JET)
normavector2islying = lambda normal, plan_normal: abs(np.sum(np.multiply(normal,  plan_normal))) > 0.5

def show_masks_on_rgb(rgb, masks, colors=None):
    if masks is None:
        return rgb
    if len(masks)==0:
        return rgb
    rgb = rgb if isinstance(rgb, np.ndarray) else rgb.detach().cpu().numpy()
    masks = [m if isinstance(m, np.ndarray) else m.detach().cpu().numpy() for m in masks]
    colors = n2colormap(len(masks)) if colors is None else colors
    out = rgb.copy()
    for i, (m, color) in enumerate(zip(masks, colors)):
        loc = np.where(m>0)
        out[loc] = 0.6*out[loc] + tuple((0.4*color).tolist())

    return out


def create_plane(size=10, step=1, noise_level=0.1):
    x = np.arange(-size, size, step)
    y = np.arange(-size, size, step)
    xx, yy = np.meshgrid(x, y)
    zz = np.zeros_like(xx) + np.random.normal(0, noise_level, xx.shape)
    points = np.vstack((xx.ravel(), yy.ravel(), zz.ravel())).T
    return points

def create_parabolic(size=10, step=1, noise_level=0.1):
    x = np.arange(-size, size, step)
    y = np.arange(-size, size, step)
    xx, yy = np.meshgrid(x, y)
    zz = 0.1 * (xx**2 + yy**2) + np.random.normal(0, noise_level, xx.shape)
    points = np.vstack((xx.ravel(), yy.ravel(), zz.ravel())).T
    return points

def calc_normalvector(points):
    centroid = np.mean(points, axis=0)
    centered_points = points - centroid
    cov_matrix = np.cov(centered_points, rowvar=False)
    _, _, vh = np.linalg.svd(cov_matrix)
    normal_vector = vh[-1]
    return normal_vector

def xyz2Ixy(x,y,z, cam_params, eps=1e-10):
    fx, fy, xc, yc = cam_params[:4]
    Ix =  np.divide(x, z + eps) * fx + xc
    Iy =  np.divide(y, z + eps) * fy + yc
    return int(Ix), int(Iy)

def Ixy2xyz(Ix, Iy, Z, cam_params):
    fx, fy, xc, yc = cam_params[:4]
    Ix, Iy = Ix - xc, Iy - yc
    return np.multiply(Ix, Z) / fx, np.multiply(Iy, Z) / fy, Z

def run_parallel_check(funcs):
    rets = run_parallel(funcs=funcs)
    return {'isdone': np.all([ret.get('isdone', True) if isinstance(ret, dict) else True for ret in rets]), 'rets': rets}

def exception_handler(func):
    import logging, traceback as _tb
    _logger = logging.getLogger('robot_agent.skill')

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            tb = _tb.format_exc()
            _logger.error(f"Exception in {func.__name__}: {e}\n{tb}")
            result = {'isdone': False, 'msg': f"'Exception in {func.__name__}': {e}"}
            try:
                from robot_agent.logging_config import debug_response_enabled
                if debug_response_enabled():
                    result['func'] = func.__name__
                    result['traceback'] = tb
            except Exception:
                pass
            return result
    return wrapper

def get_env_specs(env_name, ENV, recursive=False):
    try:
        if len(env_name)==0:
            return {}
        for k, v in ENV.items():
            if f'@{env_name}@'  in f'@{k}@':
                return copy.deepcopy(v)
        if recursive:
            return get_env_specs('@'.join(env_name.split('@')[1:]), ENV=ENV)
        else:
            return {}
    except:
        return {}

def get_closest_loc(node, ENV, threshold=0.6):
    ret = node.agents['mobile_pose'].get()
    if ret is None:
        return None
    x, y = ret['x'], ret['y']

    valid_locs = {k:[v['loc']['x']-x, v['loc']['y']-y]  for k, v in ENV.items() if 'loc' in v}
    dd = np.linalg.norm(np.array(list(valid_locs.values())), axis=-1)
    argmin = int(np.argmin(dd))
    if dd[argmin]>threshold:
        return None
    return list(valid_locs.keys())[argmin]

def warmup_cam(node, cam, ntimes=3):
    for _ in range(ntimes):
        node.agents[cam].get()
    return {'isdone':True}


def get_dtool_next_state(node, dtool):
    x,y,z,roll,pitch,yaw = node.agents['robot_pose'].get()['pose']
    dx,dy,dz = dtool

    Rx = [[1,0,0],
          [0,np.cos(roll),-np.sin(roll)],
          [0,np.sin(roll), np.cos(roll)]]
    Ry = [[np.cos(pitch),0,np.sin(pitch)],
          [0,1,0],
          [-np.sin(pitch),0,np.cos(pitch)]]
    Rz = [[np.cos(yaw),-np.sin(yaw),0],
          [np.sin(yaw), np.cos(yaw),0],
          [0,0,1]]

    R = np.dot(Rz, np.dot(Ry, Rx))
    dxw, dyw, dzw = R @ [dx,dy,dz]
    return (x+dxw, y+dyw, z+dzw, roll*180/np.pi, pitch*180/np.pi, yaw*180/np.pi)

def tool_dz_to_target_z(node, target_z):
    x,y,z,roll,pitch,yaw = node.agents['robot_pose'].get()['pose']

    Rx = [[1,0,0],
          [0,np.cos(roll),-np.sin(roll)],
          [0,np.sin(roll), np.cos(roll)]]
    Ry = [[np.cos(pitch),0,np.sin(pitch)],
          [0,1,0],
          [-np.sin(pitch),0,np.cos(pitch)]]
    Rz = [[np.cos(yaw),-np.sin(yaw),0],
          [np.sin(yaw), np.cos(yaw),0],
          [0,0,1]]
    R = np.dot(Rz, np.dot(Ry, Rx))

    z_axis = R[:,2]
    dz_tool = (target_z - z) / z_axis[2]
    return dz_tool

# def get_wrist_angle(node):
#     ry = node.agents['arm_pose'].get()['pose'][4]
#     return abs(90 + ry)

def get_lift_height(env, robot_mode):
    height = env.get('height', LIFT_CONFIGS['home'][robot_mode])
    return height if height>= LIFT_CONFIGS['home'][robot_mode] else height + 0.05

def loc2text(loc, lang='ko'):
    if not isinstance(loc, str):
        return None
    return ' '.join([correct_noun(el, lang=lang) for el in loc.split('@')[::-1]])


caption_out=None
def announce_picking(caption_in, lang='ko'):
    global caption_out
    caption_out = correct_noun(caption_in, lang=lang)
    if lang=='ko':
        text2voice(f'{caption_out} 잡을게요', run_thread=False, lang=lang)
    else:
        text2voice(f'grasping {caption_out}', run_thread=False, lang=lang)

def announce_picked(lang='ko'):
    global caption_out
    if lang=='ko':
        text2voice(f'{caption_out} 잡았어요', run_thread=False, lang=lang)
    else:
        text2voice(f'{caption_out} grasped', run_thread=False, lang=lang)

loc_text_out=None
def announce_placing(inp=None, to_wipe=False, lang='ko'):
    if not isinstance(inp, str):
        return

    env = get_env_specs(inp, ENV=ENV)
    label = env.get('label', None)
    if inp is None and label is None:
        return

    global loc_text_out

    if to_wipe:
        text2voice(f'음료수를 쏟은 것을 닦을게요' if lang=='ko' else "I'll wipe up the spilled drink", lang=lang)
    else:
        loc_text_out =  loc2text(inp, lang=lang) if label is None else label
        if loc_text_out is not None:
            text2voice(f'{loc_text_out}에 내려 놓을게요' if lang=='ko' else f"I will put down at {loc_text_out}", run_thread=False, lang=lang)

def announce_placed(inp, to_wipe=False, lang='ko'):
    if not isinstance(inp, str):
        return

    global loc_text_out
    if to_wipe:
        text2voice(f'음료수를 쏟은 것을 닦았어요' if lang=='ko' else 'spilled drink was wiped up.', lang=lang)
    else:
        text2voice(f'{loc_text_out}에 내려 놓았어요' if lang=='ko' else f'Put down at {loc_text_out}', run_thread=False, lang=lang)

env_name_kr=None
def announce_moving(env_name):
    pass

def announce_arrived():
    pass

def text2audiofile(text):
    tts = gTTS(text=text, lang='ko')
    tts.save("output_korean.mp3")

def mp3file2array(mp3_path):
    audio = AudioSegment.from_mp3(mp3_path)
    audio = audio.set_channels(1)
    audio = audio.set_frame_rate(16000)
    audio = audio.set_sample_width(2)

    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    samples = samples / 32768.0

    return samples

def play_audio_array(audio_array, samplerate=16000):
    sd.play(audio_array, samplerate=samplerate)


from difflib import SequenceMatcher
def match_command(text, commands, threshold=0.6):
    best_cmd = None
    best_score = 0

    for cmd in commands:
        score = SequenceMatcher(None, text, cmd).ratio()
        if score > best_score:
            best_score = score
            best_cmd = cmd

    if best_score < threshold:
        return None
    return best_cmd

def _get_observe_agent_name():
    cfg = UTIL_AGENT_CONFIGS.get('observe', {})
    if isinstance(cfg, dict):
        return cfg.get('agent_name', cfg.get('conn_name', 'observe'))
    return 'observe'

obbserve_agent_name = None

def publish_data(node, data):
    global obbserve_agent_name
    if obbserve_agent_name is None:
        obbserve_agent_name = _get_observe_agent_name()
    threading.Thread(target=lambda: node.agents[obbserve_agent_name].send(data), daemon=True).start()

def log_data(node, data):
    global obbserve_agent_name
    if obbserve_agent_name is None:
        obbserve_agent_name = _get_observe_agent_name()
    node.agents[obbserve_agent_name].log_msg(data)

def refine_inputs(inputs):
    """Refine input strings into a dictionary format.

    Args:
        inputs (str): Input string to parse.

    Returns:
        dict: Parsed input dictionary.
    """
    if isinstance(inputs, (int, float)):
        return {'inputs': inputs}

    if len(inputs.strip()) == 0:
        return {}
    if '=' not in inputs:
        inputs = evaluate(inputs, recursive=False)
        inputs = f'inputs="{inputs}"' if isinstance(inputs, str) else f'inputs={inputs}'
    return eval(f'dict({inputs})')

import math
def quaternion2deg(qx, qy, qz, qw):
    # Roll (x-axis rotation)
    sinr_cosp = 2 * (qw * qx + qy * qz)
    cosr_cosp = 1 - 2 * (qx * qx + qy * qy)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # Pitch (y-axis rotation)
    sinp = 2 * (qw * qy - qz * qx)

    if abs(sinp) >= 1:
        pitch = math.copysign(math.pi / 2, sinp)  # use 90 degrees if out of range
    else:
        pitch = math.asin(sinp)

    # Yaw (z-axis rotation)
    siny_cosp = 2 * (qw * qz + qx * qy)
    cosy_cosp = 1 - 2 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    # Convert to degrees
    roll_deg = math.degrees(roll)
    pitch_deg = math.degrees(pitch)
    yaw_deg = math.degrees(yaw)
    return roll_deg, pitch_deg, yaw_deg

def deg2quaternion(roll_deg, pitch_deg, yaw_deg):
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)

    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)

    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy

    return qx, qy, qz, qw


# ---------------------------------------------------------------------------
# LLM backend selection
# ---------------------------------------------------------------------------

def list_llm_clients(dm=None) -> list[str]:
    """Return the agent_names of all registered type='llm' connects."""
    if dm is None:
        from robot_agent.state import current
        dm = current().dm
    return [e['id'] for e in dm.get_all() if e.get('type') == 'llm']


def set_active_llm(agent_name: str, dm=None) -> bool:
    """Mark the type='llm' entry `agent_name` as active (peers get cleared).

    `dm.get_client('llm')` will then return this entry's client. Persists to
    connections.json. Returns False if no such llm entry is registered.
    """
    if dm is None:
        from robot_agent.state import current
        dm = current().dm
    return dm.set_active(agent_name)


