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

def decode_imgmsg(msg):
    """sensor_msgs Image or CompressedImage → ``{'im': ndarray, 'isdone': True}``."""
    from cv_bridge import CvBridge
    iscompressed = hasattr(msg, 'format')
    if not iscompressed:
        return {'im': CvBridge().imgmsg_to_cv2(msg, msg.encoding), 'isdone': True}
    
    if  ';' in msg.format:
        depth_fmt, compr_type = msg.format.split(';')
        depth_fmt, compr_type = depth_fmt.strip(), compr_type.strip()
    else:
        depth_fmt, compr_type = 'rgb8', msg.format
    

    if 'rgb' in depth_fmt:
        return {'im': CvBridge().compressed_imgmsg_to_cv2(msg, depth_fmt), 'isdone': True}

    depth_header_size = 12
    raw_data = msg.data[depth_header_size:]
    return {'im': cv2.imdecode(np.frombuffer(raw_data, np.uint8), cv2.IMREAD_UNCHANGED),
            'isdone': True}


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
