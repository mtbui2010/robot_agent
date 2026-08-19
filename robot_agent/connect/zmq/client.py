import zmq
from ..serde import byte2dict, dict2byte
from ..helpers import Timer, data_info
from ..transport import BaseTransport


class ZmqClient(BaseTransport):
    """ZeroMQ REQ client (request-reply).

    Sends and receives Python dicts (including numpy arrays) over a ``REQ``
    socket.  The REQ/REP lock-step is respected: every ``send`` is one
    request followed by one reply.  Reconnects automatically on the next
    ``send`` call after a failure.

    Args:
        host: Server hostname or IP.
        port: Server TCP port.
        timeout: Per-request receive timeout in milliseconds
                 (``None`` → block forever).

    Example::

        client = ZmqClient(host='192.168.1.10', port=8888)
        response = client.send({'rgb': frame, 'prompt': 'hello'})
    """

    def __init__(self, host: str = 'localhost', port: int = 8888,
                 timeout=10000, **kwargs):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.server_connected = False
        self.rev_data = None
        self._ctx = zmq.Context.instance()
        self.sock = None
        self._connect()

    def _connect(self):
        """Open a fresh REQ socket and connect to the server."""
        if self.sock is not None:
            self.sock.close(linger=0)
        self.sock = self._ctx.socket(zmq.REQ)
        # Don't block forever on a dead peer; drop pending msgs on close.
        if self.timeout is not None:
            self.sock.setsockopt(zmq.RCVTIMEO, int(self.timeout))
            self.sock.setsockopt(zmq.SNDTIMEO, int(self.timeout))
        self.sock.setsockopt(zmq.LINGER, 0)
        print(f'Client connecting to tcp://{self.host}:{self.port} ...')
        self.sock.connect(f'tcp://{self.host}:{self.port}')
        try:
            self.send('hello')           # handshake
            self.server_connected = True
            print('Connected.')
        except zmq.ZMQError as e:
            print(f'Connection failed: {e}')
            self.server_connected = False

    def send(self, data=None):
        """Send *data* to the server and return the decoded response.

        Reconnects automatically if the previous request failed (a failed
        REQ socket cannot be reused, so it is rebuilt).

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
            self.sock.send(dict2byte(data))
            timer.pin_time('send_data')
            self.rev_data = byte2dict(self.sock.recv())
            timer.pin_time('get_return')
            print(f'{data_info(self.rev_data)}')
            print(timer.pin_times_str)
            return self.rev_data
        except zmq.ZMQError as e:
            print(f'Send error: {e} — socket reset.')
            self.server_connected = False
            return None

    def close(self):
        """Close the underlying socket."""
        if self.sock is not None:
            self.sock.close(linger=0)
        self.server_connected = False

    # ---- legacy alias kept for backward compatibility ----
    def send_dict(self, aDict):
        return self.send(aDict)


def test_client(data=None):
    from ..helpers import parse_keys_values
    import numpy as np
    HOST, PORT = 'localhost', 8888
    args = parse_keys_values(optional_args={'host': HOST, 'port': PORT})

    if data is None:
        data = {'text': 'hello',
                'rgb': np.random.randint(0, 255, size=(2000, 2000, 3), dtype='uint8'),
                'depth': np.random.randint(0, 65000, size=(2000, 2000), dtype='uint16')}

    client = ZmqClient(host=args['host'], port=int(args['port']))
    client.send(data)


if __name__ == '__main__':
    test_client()
