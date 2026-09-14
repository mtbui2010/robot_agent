"""ROS 2 message encoders / decoders.

These are the functions the persisted ``connections.json`` snippets import by
name (``decode_imgmsg``, ``decode_caminfomsg``, …), so their names are part of
the on-disk contract — see ``DeviceManager._compile_run_func`` for the
``pyconnect.*`` compatibility aliasing that keeps pre-refactor config files
loading.
"""

import cv2
import numpy as np

from ..serde import dict2str, str2dict


# ---------------------------------------------------------------------------
# Images / camera info
# ---------------------------------------------------------------------------

# Channel-order fix per ROS encoding, applied so `im` is ALWAYS RGB. Every
# consumer assumes that: the dataset writers and the camera streamer all do
# cvtColor(RGB->BGR) before handing the array to cv2, so a bgr8 topic left
# unconverted shows up with red and blue swapped everywhere downstream.
_TO_RGB = {
    'bgr8':   cv2.COLOR_BGR2RGB,
    'bgr16':  cv2.COLOR_BGR2RGB,
    'bgra8':  cv2.COLOR_BGRA2RGB,
    'bgra16': cv2.COLOR_BGRA2RGB,
    'rgba8':  cv2.COLOR_RGBA2RGB,
    'rgba16': cv2.COLOR_RGBA2RGB,
}
_ALREADY_RGB = {'rgb8', 'rgb16'}


def _to_rgb(im, encoding: str):
    """Normalise a decoded colour image to RGB channel order.

    Single-channel data (depth, mono) passes through untouched. An unrecognised
    3-channel encoding is left alone rather than guessed at — better a known
    unknown than a silent double-swap.
    """
    if im is None or getattr(im, 'ndim', 0) != 3:
        return im
    enc = (encoding or '').strip().lower()
    code = _TO_RGB.get(enc)
    if code is not None:
        return cv2.cvtColor(im, code)
    if enc not in _ALREADY_RGB:
        print(f'[decode_imgmsg] unknown colour encoding {encoding!r}; '
              f'passing channels through as-is')
    return im


def decode_imgmsg(msg):
    """sensor_msgs Image or CompressedImage → ``{'im': ndarray, 'isdone': True}``.

    `im` is RGB for colour topics and untouched for depth/mono, whatever the
    publisher's encoding. Three cases:

    * raw ``Image``      — decoded with passthrough, then converted per
      ``msg.encoding``.
    * ``CompressedImage`` colour — cv_bridge is asked for ``bgr8`` explicitly
      (its output order is then known regardless of what the publisher declared)
      and flipped to RGB.
    * ``CompressedImage`` depth — ``compressedDepth`` prefixes a 12-byte header
      before the PNG payload, which has to be stripped before imdecode.

    The colour/depth split keys off ``compressedDepth`` in the format string.
    Matching on ``'rgb' in fmt`` instead — as this used to — sent the very
    common ``"bgr8; jpeg compressed bgr8"`` down the depth path, where the
    header strip corrupted the JPEG.
    """
    from cv_bridge import CvBridge

    if not hasattr(msg, 'format'):                      # sensor_msgs/Image
        im = CvBridge().imgmsg_to_cv2(msg, desired_encoding='passthrough')
        return {'im': _to_rgb(im, msg.encoding), 'isdone': True}

    fmt = (msg.format or '').lower()
    if 'compresseddepth' in fmt.replace(' ', ''):       # depth: strip header
        depth_header_size = 12
        raw_data = bytes(msg.data)[depth_header_size:]
        return {'im': cv2.imdecode(np.frombuffer(raw_data, np.uint8), cv2.IMREAD_UNCHANGED),
                'isdone': True}

    im = CvBridge().compressed_imgmsg_to_cv2(msg, desired_encoding='bgr8')
    return {'im': _to_rgb(im, 'bgr8'), 'isdone': True}


def encode_imgmsg(img, msg):
    """ndarray → sensor_msgs/Image (``rgb8`` for colour, ``16UC1`` for depth)."""
    from cv_bridge import CvBridge
    encoding = 'rgb8' if len(img.shape) == 3 else '16UC1'
    return CvBridge().cv2_to_imgmsg(img, encoding=encoding)


def decode_caminfomsg(msg):
    """sensor_msgs/CameraInfo → ``{'cam_params': [fx, fy, cx, cy], 'isdone': True}``."""
    k = msg.k.reshape(3, 3)
    cam_params = [k[(0, 0)], k[(1, 1)], k[(0, 2)], k[(1, 2)]]
    return {'cam_params': cam_params, 'isdone': True}


# ---------------------------------------------------------------------------
# Generic dict-over-string codecs (std_msgs/String, rosinterfaces SendStringData)
# ---------------------------------------------------------------------------

decode_topic_strmsg = lambda msg: str2dict(msg.data)


def encode_topic_strmsg(data, msg):
    msg.data = dict2str(data)
    return msg


decode_srvserver_revmsg = lambda req: str2dict(req.req)


def encode_srvserver_retmsg(data, res):
    res.ret = dict2str(data)
    return res


decode_srvclient_revmsg = lambda res: str2dict(res.ret)


def encode_srvclient_sendmsg(data, req):
    req.req = dict2str(data)
    return req


decode_actionserver_revmsg = lambda req: str2dict(req.data_goal)


def encode_actionserver_retmsg(data, res):
    res.data_result = dict2str(data)
    return res


decode_actionclient_revmsg = lambda res: str2dict(res.data_result)


def encode_actionclient_sendmsg(data, req):
    req.data_goal = dict2str(data)
    return req


# Legacy aliases kept because older connections.json files reference them.
decode_actserver_revmsg = decode_actionserver_revmsg
encode_actserver_retmsg = encode_actionserver_retmsg
decode_actclient_recmsg = decode_actionclient_revmsg
encode_actclient_sendmsg = encode_actionclient_sendmsg
