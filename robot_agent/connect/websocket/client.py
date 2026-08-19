from ..serde import byte2dict, dict2byte
from ..helpers import Timer, data_info
from ..transport import BaseTransport


class WebSocketClient(BaseTransport):
    """WebSocket client (request-reply over a persistent connection).

    Sends and receives Python dicts (including numpy arrays) as **binary**
    WebSocket messages, serialised with the same ``dict2byte`` / ``byte2dict``
    codec used by the TCP and ZMQ transports.  WebSocket frames are
    self-delimiting, so no length prefix is needed.  Reconnects automatically
    on the next ``send`` call after a failure.

    Uses the synchronous ``websockets.sync`` API (``websockets>=13``) so it
    matches the blocking style of :class:`~robot_agent.connect.tcp_ip.client.TcpIpClient`.

    Args:
        host:    Server hostname or IP.
        port:    Server TCP port.
        path:    URL path to connect to (default ``"/"``).
        secure:  Use ``wss://`` instead of ``ws://`` when ``True``.
        timeout: Per-request receive timeout in seconds (``None`` → block).

    Example::

        client = WebSocketClient(host='192.168.1.10', port=8888)
        response = client.send({'rgb': frame, 'prompt': 'hello'})
    """

    def __init__(self, host: str = 'localhost', port: int = 8888,
                 path: str = '/', secure: bool = False, timeout=10.0,
                 **kwargs):
        self.host = host
        self.port = port
        self.path = path if path.startswith('/') else f'/{path}'
        self.scheme = 'wss' if secure else 'ws'
        self.timeout = timeout
        self.server_connected = False
        self.rev_data = None
        self.ws = None
        self._connect()

    @property
    def url(self) -> str:
        return f'{self.scheme}://{self.host}:{self.port}{self.path}'

    def _connect(self):
        """Open a fresh WebSocket connection to the server."""
        from websockets.sync.client import connect
        print(f'Client connecting to {self.url} ...')
        try:
            self.ws = connect(self.url, open_timeout=self.timeout,
                              max_size=None)
            self.server_connected = True
            print('Connected.')
            self.send('hello')           # handshake
        except Exception as e:
            print(f'Connection failed: {e}')
            self.server_connected = False

    def send(self, data=None):
        """Send *data* to the server and return the decoded response.

        Reconnects automatically if the socket is closed.

        Args:
            data: Dict, string, or any serialisable object.

        Returns:
            Decoded response dict, or ``None`` on failure.
        """
        if not self.server_connected and data != 'hello':
            self._connect()
            if not self.server_connected:
                return None
        try:
            timer = Timer()
            self.ws.send(dict2byte(data))
            timer.pin_time('send_data')
            reply = self.ws.recv(timeout=self.timeout)
            self.rev_data = byte2dict(reply)
            timer.pin_time('get_return')
            print(f'{data_info(self.rev_data)}')
            print(timer.pin_times_str)
            return self.rev_data
        except Exception as e:
            print(f'Send error: {e} — socket closed.')
            self.close()
            return None

    def close(self):
        """Close the underlying WebSocket connection."""
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.server_connected = False


def test_client(data=None):
    from ..helpers import parse_keys_values
    import numpy as np
    HOST, PORT = 'localhost', 8888
    args = parse_keys_values(optional_args={'host': HOST, 'port': PORT})

    if data is None:
        data = {'text': 'hello',
                'rgb': np.random.randint(0, 255, size=(2000, 2000, 3), dtype='uint8'),
                'depth': np.random.randint(0, 65000, size=(2000, 2000), dtype='uint16')}

    client = WebSocketClient(host=args['host'], port=int(args['port']))
    client.send(data)


if __name__ == '__main__':
    test_client()
