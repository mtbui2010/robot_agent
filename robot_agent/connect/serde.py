"""Wire serialisation for the connect layer.

msgpack (fast, compact, numpy-aware) with a pickle fallback so environments
without msgpack keep working. ``dict2str`` / ``str2dict`` are the base64 text
forms used by the ROS string-message codecs, and are what the persisted
``connections.json`` encode/decode snippets ultimately call.
"""

import base64
import json
import os
import pickle as _pickle

import numpy as np


def _np_encode(obj: object):
    """msgpack encoder: numpy arrays native, everything else via pickle."""
    if isinstance(obj, np.ndarray):
        return {
            '__ndarray__': True,
            'data':  obj.tobytes(),
            'dtype': str(obj.dtype),
            'shape': list(obj.shape),
        }
    return {'__pickle__': True, 'data': _pickle.dumps(obj)}


def _np_decode(obj: dict):
    """msgpack decoder: numpy arrays + pickled fallback."""
    if obj.get('__ndarray__'):
        return np.frombuffer(obj['data'], dtype=obj['dtype']).reshape(obj['shape'])
    if obj.get('__pickle__'):
        return _pickle.loads(obj['data'])  # noqa: S301
    return obj


try:
    import msgpack as _msgpack

    def dict2byte(aDict) -> bytes:
        """Serialise *aDict* → bytes using msgpack (numpy-aware)."""
        return _msgpack.packb(aDict, default=_np_encode, use_bin_type=True)

    def byte2dict(byteData: bytes):
        """Deserialise bytes → dict using msgpack (numpy-aware)."""
        return _msgpack.unpackb(byteData, object_hook=_np_decode, raw=False)

    SERIALIZER = 'msgpack'

except ImportError:
    import warnings
    warnings.warn(
        'msgpack not found — falling back to pickle. '
        'Install msgpack for better performance:  pip install msgpack',
        ImportWarning, stacklevel=2,
    )

    def dict2byte(aDict) -> bytes:
        """Serialise *aDict* → bytes using pickle (fallback)."""
        return _pickle.dumps(aDict)  # noqa: S301

    def byte2dict(byteData: bytes):
        """Deserialise bytes → dict using pickle (fallback)."""
        return _pickle.loads(byteData)  # noqa: S301

    SERIALIZER = 'pickle'


def byte2str(byteData):
    return base64.b64encode(byteData).decode('utf-8')


def str2byte(astr):
    return base64.b64decode(astr)


dict2str = lambda aDict: byte2str(dict2byte(aDict))
str2dict = lambda astr: byte2dict(str2byte(astr))


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def is_json_serializable(value):
    try:
        json.dumps(value)
        return True
    except (TypeError, OverflowError):
        return False


def write_json(filepath, adict, skip_non_serializable=False):
    assert os.path.splitext(filepath)[-1] == '.json'

    if skip_non_serializable:
        adict = {k: v for k, v in adict.items() if is_json_serializable(v)}

    with open(filepath, 'w') as f:
        json.dump(adict, f)


def read_json(filepath):
    assert os.path.splitext(filepath)[-1] == '.json'
    with open(filepath) as f:
        return json.load(f)
