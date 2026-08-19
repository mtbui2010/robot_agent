import socket, numpy as np
from ..serde import byte2dict, dict2byte
from ..helpers import Timer, data_info, recvall
from ..transport import BaseTransport


class TcpIpClient(BaseTransport):
    """TCP/IP client with length-prefixed framing.

    Sends and receives Python dicts (including numpy arrays) over a persistent
    socket connection.  Automatically reconnects on the next ``send`` call
    after a connection failure.

    Args:
        host: Server hostname or IP.
        port: Server TCP port.

    Example::

        client = TcpIpClient(host='192.168.1.10', port=8888)
        response = client.send({'rgb': frame, 'prompt': 'hello'})
    """

    def __init__(self, host: str = 'localhost', port: int = 8888):
        self.host = host
        self.port = port
        self.server_connected = False
        self.rev_data = None
        self._connect()

    def _connect(self):
        """Open a fresh socket and connect to the server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        print(f'Client connecting to {self.host}:{self.port} ...')
        try:
            self.sock.connect((self.host, self.port))
            self.server_connected = True
            print('Connected.')
            self.send('hello')           # handshake
        except OSError as e:
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
        if not self.server_connected:
            self._connect()
            if not self.server_connected:
                return None

        try:
            timer = Timer()
            self._send_raw(data)
            timer.pin_time('send_data')
            ret = self._recv_raw()
            timer.pin_time('get_return')
            print(timer.pin_times_str)
            return ret
        except (OSError, ConnectionResetError) as e:
            print(f'Send error: {e} — socket closed.')
            self.sock.close()
            self.server_connected = False
            return None
            
            

    def _send_raw(self, data):
        """Serialise and send with a 16-byte length prefix."""
        byte_data = dict2byte(data)
        length = str(len(byte_data)).rjust(16, '0').encode()
        self.sock.sendall(length + byte_data)

    def _recv_raw(self):
        """Receive a length-prefixed message and return the decoded object."""
        ret_len = recvall(self.sock, 16)
        if not ret_len:
            raise ConnectionResetError("Server closed the connection.")
        byte_data = recvall(self.sock, int(ret_len))
        self.rev_data = byte2dict(byte_data)
        return self.rev_data

    def close(self):
        """Close the underlying socket."""
        self.sock.close()
        self.server_connected = False

    # ---- legacy aliases kept for backward compatibility ----
    def send_dict(self, aDict):
        self._send_raw(aDict)

    def get_return(self):
        return self._recv_raw()

def test_client():
    from ..helpers import parse_keys_values
    HOST, PORT = 'localhost', 8801
    args = parse_keys_values(optional_args={'host':HOST, 'port':PORT})
    
    client = TcpIpClient(host=args['host'], port=args['port'])
    client.send({'text': 'hello', 'rgb': np.random.randint(0,255, size=(2000, 2000,3), dtype='uint8'), 
                 'depth': np.random.randint(0, 65000, size=(2000, 2000), dtype='uint16')})
    
if __name__=='__main__':
    test_client()
    