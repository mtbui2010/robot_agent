import socket
import threading
import time
from ..serde import byte2dict, dict2byte
from ..helpers import Timer, data_info, recvall
from ..transport import BaseServer


class TcpIpServer(BaseServer):
    """Multi-client TCP/IP server with length-prefixed framing.

    Each connected client is handled in its own daemon thread.  Set
    ``run_func`` to process incoming requests::

        def my_handler(**kwargs):
            return {'result': kwargs.get('value', 0) * 2}

        server = TcpIpServer(host='0.0.0.0', port=8888, run_func=my_handler)
        server.listen()   # blocks; use listen(run_thread=True) for background

    Args:
        host:        Bind address (default ``"0.0.0.0"`` → all interfaces).
        port:        TCP port to listen on.
        run_func:    Callable invoked as ``run_func(**received_dict)``.
                     Must return a serialisable value.
        num_connect: Max queued connections (default 5).
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 8888,
                 run_func=None, num_connect: int = 5):
        self.host = host
        self.port = port
        self.run_func = run_func
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(num_connect)
        self.active = True
        print(f"Server running on {self.host}:{self.port}")

    def listen(self, run_thread=False):
        def func():
            """Start listening for client connections."""
            print("Waiting for connections...")
            try:
                while self.active:
                    client, address = self.sock.accept()
                    print(f"Connection established with {address}")
                    threading.Thread(target=self.handle_client, args=(client, address), daemon=True).start()
            except KeyboardInterrupt:
                print("Server shutting down...")
                self.stop()
        if not run_thread:
            return func()
        threading.Thread(target=func, daemon=True).start()

    def handle_client(self, client: socket.socket, address):
        """Handle the full lifecycle of one connected client."""
        try:
            while True:
                # --- receive length header ---
                length = recvall(client, 16)
                if not length:
                    print(f"Client {address} disconnected.")
                    break

                # --- receive payload ---
                byte_data = recvall(client, int(length))
                if not byte_data:
                    print(f"Empty payload from {address}, closing.")
                    break

                timer = Timer()
                received_data = byte2dict(byte_data)
                timer.pin_time('deserialise')

                # --- process ---
                response_data = self.process_data(received_data)
                timer.pin_time('process')

                # --- reply ---
                self.send_data(client, response_data)
                timer.pin_time('send')
                print(f"[{address}] {timer.pin_times_str}")

        except (OSError, ConnectionResetError) as e:
            print(f"Connection error with {address}: {e}")
        except Exception as e:
            print(f"Unexpected error with {address}: {e}")
        finally:
            client.close()

    def process_data(self, received_data):
        """Process the received data and return the response."""
        if received_data=='hello':
            return f'Hi from server {self.host}:{self.port}'
        if self.run_func:
            return self.run_func(**received_data)
        return received_data  # Echo back the data if no processing function is provided

    def send_data(self, client, data):
        """Send data to the client."""
        byte_data = dict2byte(data)
        length = str(len(byte_data)).rjust(16, '0').encode()
        client.sendall(length + byte_data)

    def stop(self):
        """Stop the server and close the socket."""
        self.active = False
        self.sock.close()
        print("Server stopped.")
        
    def spin(self, run_thread=False):
        self.listen(run_thread=run_thread)

def run_server(custom_func=None):
    from ..helpers import parse_keys_values
    HOST, PORT = '0.0.0.0', 8888
    args = parse_keys_values(optional_args={'host':HOST, 'port':PORT})
    
    custom_func = lambda x:x if custom_func is None else custom_func
    server = TcpIpServer(host=args['host'], port=args['port'])
    server.run_func = custom_func
    server.listen()
    
    

if __name__ == '__main__':

    run_server()
