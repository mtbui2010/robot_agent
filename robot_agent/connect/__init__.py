"""Connectivity layer — transports, ROS 2 plumbing and LLM clients.

Absorbed from the former external ``pyconnect`` dependency so robot_agent is
self-contained. Only the surface robot backends actually use was carried over;
the transport/ROS demos, MQTT, UDP streaming, WebRTC server and Telegram bot
were left behind.

Layout
------
``transport``  TransportType / LLMBackend enums, BaseTransport / BaseServer
``serde``      msgpack↔bytes↔base64 codecs, JSON read/write
``helpers``    Timer, data_info, evaluate, set_atrrs, …
``parallel``   run_parallel / run_parallel_check
``paths``      where the connect layer writes its logs
``ros``        CustomNode + message codecs + ``get_*_configs`` builders
``tcp_ip`` ``zmq`` ``websocket`` ``http`` ``webrtc``   point-to-point transports
``llm``        LLaMA / ChatGPT / Gemini clients + ``init_llm_client``

Submodules are NOT imported eagerly — most of them need optional third-party
packages (rclpy, pyzmq, aiortc, openai) that a given deployment may not have.
"""

from .transport import BaseServer, BaseTransport, LLMBackend, TransportType  # noqa: F401


def init_detect_client(detect_url: str):
    """Create a transport client from a URL string ``"type:host:port"``.

    Args:
        detect_url: e.g. ``"tcp:localhost:8888"`` or ``"zmq:192.168.1.5:9000"``.

    Returns:
        Connected client instance.

    Raises:
        ValueError: If the URL format is wrong or the type is unsupported.
    """
    parts = detect_url.split(':')
    if len(parts) != 3:
        raise ValueError(
            f"Invalid detect_url '{detect_url}'. Expected format: 'type:host:port'"
        )
    client_type_str, host, port = parts

    try:
        client_type = TransportType(client_type_str.lower())
    except ValueError:
        supported = [t.value for t in (TransportType.TCP, TransportType.ZMQ,
                                       TransportType.WEBSOCKET)]
        raise ValueError(
            f"Unknown transport type '{client_type_str}'. Supported: {supported}"
        )

    if client_type == TransportType.TCP:
        from .tcp_ip.client import TcpIpClient
        return TcpIpClient(host=host, port=int(port))
    if client_type == TransportType.ZMQ:
        from .zmq.client import ZmqClient
        return ZmqClient(host=host, port=int(port))
    if client_type == TransportType.WEBSOCKET:
        from .websocket.client import WebSocketClient
        return WebSocketClient(host=host, port=int(port))
    raise NotImplementedError(
        f"Transport '{client_type.value}' has no client implementation yet.")
