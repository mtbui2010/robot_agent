"""Config-dict builders for :meth:`CustomNode.add_agent`.

Each ``get_*_configs(**kwargs)`` returns the boilerplate (message type + the
matching encode/decode pair from :mod:`.codecs`) for one connection kind, with
*kwargs* overriding anything. ``agent_name`` and ``conn_name`` default to each
other so a caller only has to name the connection once.
"""

from ..helpers import update_dict
from .codecs import (
    decode_actionclient_revmsg, decode_actionserver_revmsg, decode_caminfomsg,
    decode_imgmsg, decode_srvclient_revmsg, decode_srvserver_revmsg,
    decode_topic_strmsg, encode_actionclient_sendmsg, encode_actionserver_retmsg,
    encode_srvclient_sendmsg, encode_srvserver_retmsg, encode_topic_strmsg,
)


def _name_defaults(kwargs: dict) -> dict:
    """Make ``agent_name`` and ``conn_name`` default to one another.

    Neither is required — `add_agent` does the same fill-in and raises its own
    assertion when both are missing.
    """
    kwargs.pop('conn_type', None)
    if 'agent_name' not in kwargs and 'conn_name' in kwargs:
        kwargs['agent_name'] = kwargs['conn_name']
    if 'conn_name' not in kwargs and 'agent_name' in kwargs:
        kwargs['conn_name'] = kwargs['agent_name']
    return kwargs


def get_pub_configs(**kwargs):
    from std_msgs.msg import String
    return update_dict({
        'conn_type': 'pub',
        'data_interface': String,
        'encode_func': encode_topic_strmsg,
    }, _name_defaults(kwargs))


def get_sub_configs(**kwargs):
    from std_msgs.msg import String
    return update_dict({
        'conn_type': 'sub',
        'data_interface': String,
        'decode_func': decode_topic_strmsg,
    }, _name_defaults(kwargs))


def get_service_client_configs(**kwargs):
    from rosinterfaces.srv import SendStringData
    return update_dict({
        'conn_type': 'service_client',
        'data_interface': SendStringData,
        'encode_func': encode_srvclient_sendmsg,
        'decode_func': decode_srvclient_revmsg,
        'do_log_msg': True,
    }, _name_defaults(kwargs))


def get_service_server_configs(**kwargs):
    from rosinterfaces.srv import SendStringData
    return update_dict({
        'conn_type': 'service_server',
        'data_interface': SendStringData,
        'encode_func': encode_srvserver_retmsg,
        'decode_func': decode_srvserver_revmsg,
        'do_log_msg': False,
    }, _name_defaults(kwargs))


def get_action_client_configs(**kwargs):
    from rosinterfaces.action import SendStringData
    return update_dict({
        'conn_type': 'action_client',
        'data_interface': SendStringData,
        'encode_func': encode_actionclient_sendmsg,
        'decode_func': decode_actionclient_revmsg,
        'do_log_msg': True,
    }, _name_defaults(kwargs))


def get_action_server_configs(**kwargs):
    from rosinterfaces.action import SendStringData
    return update_dict({
        'conn_type': 'action_server',
        'data_interface': SendStringData,
        'encode_func': encode_actionserver_retmsg,
        'decode_func': decode_actionserver_revmsg,
        'do_log_msg': False,
    }, _name_defaults(kwargs))


def get_image_sub_configs(**kwargs):
    from sensor_msgs.msg import CompressedImage
    return update_dict({
        'conn_type': 'sub',
        'data_interface': CompressedImage,
        'decode_func': decode_imgmsg,
        'do_log_msg': True,
    }, _name_defaults(kwargs))


def get_caminfo_sub_configs(**kwargs):
    from sensor_msgs.msg import CameraInfo
    return update_dict({
        'conn_type': 'sub',
        'data_interface': CameraInfo,
        'decode_func': decode_caminfomsg,
        'do_log_msg': False,
    }, _name_defaults(kwargs))


# Aliases matching the `get_default_*` names used by older skill templates.
get_default_pub = get_pub_configs
get_default_sub = get_sub_configs
get_default_service_client = get_service_client_configs
get_default_service_server = get_service_server_configs
get_default_action_client = get_action_client_configs
get_default_action_server = get_action_server_configs
