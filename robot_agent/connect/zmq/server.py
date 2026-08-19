import threading
import zmq
from ..serde import byte2dict, dict2byte
from ..helpers import Timer, data_info
from ..transport import BaseServer


class ZmqServer(BaseServer):
    """ZeroMQ REP server (request-reply).

    Receives Python dicts (including numpy arrays) on a ``REP`` socket and
    replies with the result of ``run_func``.  Mirrors
    :class:`~robot_agent.connect.tcp_ip.server.TcpIpServer`: set ``run_func`` to process
    incoming requests, then call ``listen`` (blocking) or
    ``listen(run_thread=True)`` (background daemon thread).

    Args:
        host:     Bind address (default ``"0.0.0.0"`` → all interfaces).
        port:     TCP port to bind on.
        run_func: Callable invoked as ``run_func(**received_dict)``.
                  Must return a serialisable value.

    Example::

        def my_handler(**kwargs):
            return {'result': kwargs.get('value', 0) * 2}

        server = ZmqServer(host='0.0.0.0', port=8888, run_func=my_handler)
        server.listen()
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 8888,
                 run_func=None, **kwargs):
        self.host = host
        self.port = port
        self.run_func = run_func
        self.active = True
        self._ctx = zmq.Context.instance()
        self.sock = self._ctx.socket(zmq.REP)
        # Time out recv periodically so stop() can break the loop.
        self.sock.setsockopt(zmq.RCVTIMEO, 500)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.bind(f'tcp://{self.host}:{self.port}')
        print(f"Server running on tcp://{self.host}:{self.port}")

    def listen(self, run_thread=False):
        def func():
            """Serve requests until ``stop`` is called."""
            print("Waiting for requests...")
            while self.active:
                try:
                    byte_data = self.sock.recv()
                except zmq.Again:
                    continue                 # recv timeout — re-check self.active
                except zmq.ZMQError as e:
                    if self.active:
                        print(f"Recv error: {e}")
                    break
                timer = Timer()
                received_data = byte2dict(byte_data)
                print(f'{data_info(received_data)}')
                timer.pin_time('deserialise')
                response_data = self.process_data(received_data)
                timer.pin_time('process')
                self.sock.send(dict2byte(response_data))
                timer.pin_time('send')
                print(timer.pin_times_str)
        if not run_thread:
            return func()
        threading.Thread(target=func, daemon=True).start()

    def process_data(self, received_data):
        """Process the received data and return the response."""
        if received_data == 'hello':
            return f'Hi from server {self.host}:{self.port}'
        if self.run_func:
            return self.run_func(**received_data)
        return received_data  # echo back if no processing function is provided

    def stop(self):
        """Stop the server and close the socket."""
        self.active = False
        self.sock.close(linger=0)
        print("Server stopped.")

    def spin(self, run_thread=False):
        self.listen(run_thread=run_thread)


def run_server(custom_func=None):
    from ..helpers import parse_keys_values
    HOST, PORT = '0.0.0.0', 8888
    args = parse_keys_values(optional_args={'host': HOST, 'port': PORT})
    server = ZmqServer(host=args['host'], port=int(args['port']))

    if custom_func is None:
        def run_func(**kwargs):
            return kwargs
        server.run_func = run_func
    else:
        server.run_func = custom_func
    server.listen()


if __name__ == '__main__':
    run_server()
