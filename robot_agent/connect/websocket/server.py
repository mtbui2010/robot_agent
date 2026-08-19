import threading
from ..serde import byte2dict, dict2byte
from ..helpers import Timer, data_info
from ..transport import BaseServer


class WebSocketServer(BaseServer):
    """WebSocket server (request-reply).

    Receives binary Python dicts (including numpy arrays) and replies with the
    result of ``run_func``.  Mirrors
    :class:`~robot_agent.connect.tcp_ip.server.TcpIpServer`: each connection is served in
    its own thread by the ``websockets.sync`` runtime; set ``run_func`` to
    process incoming requests, then call ``listen`` (blocking) or
    ``listen(run_thread=True)`` (background daemon thread).

    Args:
        host:     Bind address (default ``"0.0.0.0"`` → all interfaces).
        port:     TCP port to bind on.
        run_func: Callable invoked as ``run_func(**received_dict)``.
                  Must return a serialisable value.

    Example::

        def my_handler(**kwargs):
            return {'result': kwargs.get('value', 0) * 2}

        server = WebSocketServer(host='0.0.0.0', port=8888, run_func=my_handler)
        server.listen()
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 8888,
                 run_func=None, **kwargs):
        self.host = host
        self.port = port
        self.run_func = run_func
        self.active = True
        self._server = None
        print(f"Server configured on ws://{self.host}:{self.port}")

    def listen(self, run_thread=False):
        from websockets.sync.server import serve

        def func():
            print("Waiting for connections...")
            with serve(self._handle, self.host, self.port,
                       max_size=None) as server:
                self._server = server
                server.serve_forever()
        if not run_thread:
            return func()
        threading.Thread(target=func, daemon=True).start()

    def _handle(self, conn):
        """Handle the full lifecycle of one connected client."""
        address = getattr(conn, 'remote_address', '?')
        print(f"Connection established with {address}")
        try:
            for message in conn:
                timer = Timer()
                received_data = byte2dict(message)
                print(f'{data_info(received_data)}')
                timer.pin_time('deserialise')
                response_data = self.process_data(received_data)
                timer.pin_time('process')
                conn.send(dict2byte(response_data))
                timer.pin_time('send')
                print(f"[{address}] {timer.pin_times_str}")
        except Exception as e:
            print(f"Connection error with {address}: {e}")

    def process_data(self, received_data):
        """Process the received data and return the response."""
        if received_data == 'hello':
            return f'Hi from server {self.host}:{self.port}'
        if self.run_func:
            return self.run_func(**received_data)
        return received_data  # echo back if no processing function is provided

    def stop(self):
        """Stop the server and release the socket."""
        self.active = False
        try:
            if self._server is not None:
                self._server.shutdown()
        except Exception:
            pass
        print("Server stopped.")

    def spin(self, run_thread=False):
        self.listen(run_thread=run_thread)


def run_server(custom_func=None):
    from ..helpers import parse_keys_values
    HOST, PORT = '0.0.0.0', 8888
    args = parse_keys_values(optional_args={'host': HOST, 'port': PORT})
    server = WebSocketServer(host=args['host'], port=int(args['port']))

    if custom_func is None:
        def run_func(**kwargs):
            return kwargs
        server.run_func = run_func
    else:
        server.run_func = custom_func
    server.listen()


if __name__ == '__main__':
    run_server()
