"""ROS 2 layer of the connect package.

Import from the submodules — ``.node`` (CustomNode + agents), ``.codecs``
(message encoders/decoders) and ``.configs`` (``get_*_configs`` builders).
Nothing is imported eagerly here: these modules pull in ``rclpy`` /
``cv_bridge``, which must stay optional for non-ROS deployments.
"""
