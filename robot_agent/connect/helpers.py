"""Small shared helpers used across the connect layer.

Absorbed from ``pyconnect.utils``. Only the parts robot_agent / robot packages
actually call are kept — the CLI-demo and prompt-toolkit helpers were dropped.
"""

import ast
import os
import re
import sys
import time
from datetime import datetime

import numpy as np


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

class Timer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.times = [time.time()]
        self.labels = []

    def __len__(self):
        return len(self.times)

    def pin_time(self, label='NaN'):
        self.add_time(label=label)
        return self.times[-1] - self.times[-2]

    def add_time(self, label='NaN'):
        self.times.append(time.time())
        self.labels.append(label)

    @property
    def run_time(self):
        if len(self) < 2:
            self.add_time()
        return self.times[-1] - self.times[0]

    @property
    def fps(self):
        return 1 / self.run_time

    @property
    def run_time_str(self):
        runtime = self.run_time
        if runtime < 1:
            return 'Total:%dms' % (1000 * runtime)
        return 'Total:%.2fs' % runtime

    @property
    def pin_times_str(self):
        if len(self) < 2:
            return self.run_time_str
        s = ''
        for i in range(len(self) - 1):
            label = self.labels[i]
            if label == 'NaN':
                continue
            dtime = self.times[i + 1] - self.times[i]
            if dtime < 1:
                s += '%s:%dms-' % (label, 1000 * dtime)
            else:
                s += '%s:%.2fs-' % (label, dtime)
        return s + self.run_time_str


strftime = lambda: datetime.now().strftime('%Y%m%d%H%M%S%f')
type_name = lambda v: type(v).__name__


def printif(msg, do_print=True):
    if do_print:
        print(msg)


# ---------------------------------------------------------------------------
# Introspection / dict helpers
# ---------------------------------------------------------------------------

get_attrs = lambda x, atrs: {el: getattr(x, el) for el in atrs}
set_atrrs = lambda x, adict: ([setattr(x, k, v) for k, v in adict.items() if hasattr(x, k)], x)[-1]
update_dict = lambda adict, x: (adict.update(x), adict)[-1]


def data_info(data, prefix=''):
    """Compact, printable summary of a nested dict/list/array payload."""
    if isinstance(data, (list, tuple)):
        return ', '.join([f' {prefix}{data_info(el)}' for el in data])
    if isinstance(data, dict):
        return ',\n'.join([f'{prefix}{k}:{data_info(v, prefix=prefix + "  ")}'
                           for k, v in data.items()])
    if isinstance(data, np.ndarray):
        if len(data.flatten()) < 50:
            return f'{prefix}{np.round(data, decimals=3)}'
        return f'{prefix}array{data.shape}'
    return f'{prefix}{round(data, 3) if isinstance(data, (int, float)) else data}'


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def evaluate(v: str, recursive=True):
    """Best-effort literal parse of *v*, falling back to the original string."""
    v = v.strip()

    if ',' in v and recursive:
        return [evaluate(el) for el in v.split(',') if el.strip()]

    try:
        val = ast.literal_eval(v)   # safer than eval()
    except (ValueError, SyntaxError):
        return v

    if isinstance(val, str):
        return val
    if isinstance(val, (type, object)) and ('function' in str(type(val)) or 'type' in str(type(val))):
        return v
    return val


def parse_keys_values(optional_args={}):
    """Parse ``key=value`` pairs from ``sys.argv`` into *optional_args*.

    Pass ``--help`` / ``help`` on the CLI to print available keys and exit.
    """
    argv = sys.argv[1:]

    if any(a in ('--help', 'help', '-h') for a in argv):
        print('Available arguments (key=value):')
        for k, v in optional_args.items():
            print(f'  {k:<20} default={v!r}')
        sys.exit(0)

    args = [arg.split('=', 1) for arg in argv if '=' in arg]
    args = {k: evaluate(v) for k, v in args}

    optional_args.update(args)
    print(f'{"=" * 10} Optional args: {", ".join([f"{k}={v}" for k, v in optional_args.items()])}')

    return optional_args


def findNumdersInString(sentence):
    return [eval(el) for el in re.findall(r'\d+', sentence)]


def try_parse(f, s):
    try:
        f(s)
        return True
    except Exception:
        return False


is_not_number = lambda s: not any(map(lambda f: try_parse(f, s), [int, float]))
str2num = lambda s: s if is_not_number(s) else eval(s)


# ---------------------------------------------------------------------------
# Image / socket helpers
# ---------------------------------------------------------------------------

def crop_image(im, crop_roi=None, keep_size=False):
    if crop_roi is None:
        return im
    x0, y0, x1, y1 = crop_roi
    if not keep_size:
        return im[y0:y1, x0:x1, ...]
    out = np.zeros_like(im)
    out[y0:y1, x0:x1, ...] = im[y0:y1, x0:x1, ...]
    return out


def recvall(sock, count):
    """Read exactly *count* bytes from *sock*, or None if the peer closed."""
    buf = b''
    while count:
        newbuf = sock.recv(count)
        if not newbuf:
            return None
        buf += newbuf
        count -= len(newbuf)
    return buf


def path2module(module_path):
    """Import *module_path* as a dotted module name, or load it as a file."""
    import importlib
    import importlib.util

    try:
        return importlib.import_module(module_path)
    except Exception as e:
        print(f'{module_path}: {e}')
        module_name = os.path.splitext(os.path.basename(module_path))[0]
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None:
            raise Exception(f'Cannot find spec for {module_path}')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
