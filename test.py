def encode_func(data, req):
    from pyconnect.utils import set_atrrs
    req.pose.header.frame_id = 'map'
    req.behavior_tree = ''

    req.pose.pose.position.x = data['x']
    req.pose.pose.position.y = data['y']
    req.pose.pose.position.z = data['z']

    req.pose.pose.orientation.x = data['qx']
    req.pose.pose.orientation.y = data['qy']
    req.pose.pose.orientation.z = data['qz']
    req.pose.pose.orientation.w = data['qw']
    return req