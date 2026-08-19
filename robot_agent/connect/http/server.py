import threading
from ..transport import BaseServer


class HttpServer(BaseServer):
    """Minimal FastAPI/uvicorn server (request-reply over HTTP).

    Mirrors :class:`~robot_agent.connect.tcp_ip.server.TcpIpServer`: set ``run_func`` to
    process incoming requests, then call ``listen`` (blocking) or
    ``listen(run_thread=True)`` (background daemon thread).  Exposes a single
    ``POST {path}`` endpoint whose JSON body is passed as keyword arguments to
    ``run_func``; the returned value is sent back as JSON.

    Note: a full ``robot_agent`` deployment already *is* a FastAPI app — this
    class is for standalone endpoints (tests, lightweight detectors) that want
    the same ``run_func`` contract as the other transports.

    Args:
        host:     Bind address (default ``"0.0.0.0"``).
        port:     TCP port to bind on.
        run_func: Callable invoked as ``run_func(**json_body)``.
        path:     Route to expose (default ``"/run"``).

    Example::

        def my_handler(**kwargs):
            return {'result': kwargs.get('value', 0) * 2}

        server = HttpServer(host='0.0.0.0', port=8888, run_func=my_handler)
        server.listen(run_thread=True)
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 8888,
                 run_func=None, path: str = '/run', **kwargs):
        self.host = host
        self.port = port
        self.run_func = run_func
        self.path = path if path.startswith('/') else f'/{path}'
        self.active = True
        self._server = None
        self.app = self._build_app()
        print(f"Server configured on http://{self.host}:{self.port}{self.path}")

    def _build_app(self):
        from fastapi import FastAPI, Request
        app = FastAPI()

        @app.post(self.path)
        async def _handle(request: Request):
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {'data': body}
            return self.process_data(body)

        @app.get('/health')
        async def _health():
            return {'ok': True}

        return app

    def listen(self, run_thread=False):
        import uvicorn
        config = uvicorn.Config(self.app, host=self.host, port=self.port,
                                log_level='info')
        self._server = uvicorn.Server(config)

        def func():
            print("Waiting for requests...")
            self._server.run()
        if not run_thread:
            return func()
        threading.Thread(target=func, daemon=True).start()

    def process_data(self, received_data):
        """Process the received data and return the response."""
        if self.run_func:
            return self.run_func(**received_data)
        return received_data  # echo back if no processing function is provided

    def stop(self):
        """Stop the server."""
        self.active = False
        if self._server is not None:
            self._server.should_exit = True
        print("Server stopped.")

    def spin(self, run_thread=False):
        self.listen(run_thread=run_thread)


def run_server(custom_func=None):
    from ..helpers import parse_keys_values
    HOST, PORT = '0.0.0.0', 8888
    args = parse_keys_values(optional_args={'host': HOST, 'port': PORT})
    server = HttpServer(host=args['host'], port=int(args['port']))

    if custom_func is None:
        def run_func(**kwargs):
            return kwargs
        server.run_func = run_func
    else:
        server.run_func = custom_func
    server.listen()


if __name__ == '__main__':
    run_server()
