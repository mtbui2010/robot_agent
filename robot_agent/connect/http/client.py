import io
import requests
from ..transport import BaseTransport


class HttpClient(BaseTransport):
    """Generic HTTP/REST client with an Inferix inference helper.

    Two ways to use it:

    1. **Generic** — :meth:`send` issues a configurable HTTP request (JSON body
       by default) and returns the decoded JSON response, satisfying the
       :class:`~robot_agent.connect.transport.BaseTransport` ``send`` contract::

           client = HttpClient(url='https://api.example.com/run', token='…')
           result = client.send({'prompt': 'hello'})

    2. **Inference** — :meth:`infer_array` serialises an ``HxWx3`` uint8 RGB
       numpy frame to ``.npy`` and uploads it as multipart ``file`` (the
       Inferix ``/infer_array`` contract), returning the parsed detections::

           client = HttpClient(
               url='https://label.aistations.org/api/deploy/endpoint-xxxx/infer_array',
               token='INFERIX_TOKEN_VALUE')
           dets = client.infer_array(rgb_frame, conf=0.3, iou=0.7, prompt='object')

    Args:
        url:     Default endpoint URL used by ``send`` / ``infer_array``.
        method:  HTTP method for the generic ``send`` (default ``"POST"``).
        headers: Extra headers merged into every request.
        token:   Bearer token; when given, an ``Authorization: Bearer <token>``
                 header is added automatically.
        timeout: Request timeout in seconds.
    """

    def __init__(self, url: str = '', method: str = 'POST',
                 headers: dict | None = None, token: str | None = None,
                 timeout: float = 30.0, **kwargs):
        self.url = url
        self.method = method.upper()
        self.timeout = timeout
        self.session = requests.Session()
        if headers:
            self.session.headers.update(headers)
        if token:
            self.session.headers['Authorization'] = f'Bearer {token}'
        self.server_connected = False
        self.ping()

    # ------------------------------------------------------------------
    # BaseTransport interface
    # ------------------------------------------------------------------
    def send(self, data=None, url: str | None = None, method: str | None = None,
             **kwargs):
        """Issue a generic HTTP request and return the decoded response.

        Args:
            data: Request body. A dict/list is sent as JSON; anything else is
                  sent as the raw body. ``None`` sends an empty body.
            url:    Override the configured endpoint for this call.
            method: Override the configured HTTP method for this call.

        Returns:
            Parsed JSON response (falls back to raw text), or ``None`` on
            failure.
        """
        target = url or self.url
        verb = (method or self.method).upper()
        try:
            req = {'timeout': self.timeout}
            if isinstance(data, (dict, list)):
                req['json'] = data
            elif data is not None:
                req['data'] = data
            req.update(kwargs)
            r = self.session.request(verb, target, **req)
            r.raise_for_status()
            self.server_connected = True
            try:
                return r.json()
            except ValueError:
                return r.text
        except requests.RequestException as e:
            print(f'HTTP send error: {e}')
            self.server_connected = False
            return None

    def close(self):
        """Close the underlying HTTP session."""
        self.session.close()
        self.server_connected = False

    # ------------------------------------------------------------------
    # Inferix inference helper
    # ------------------------------------------------------------------
    def infer_array(self, img, conf: float = 0.3, iou: float = 0.7,
                    min_size: int = 0, max_size: int = 15,
                    prompt: str = 'object', url: str | None = None,
                    field: str = 'file', filename: str = 'frame.npy',
                    **extra):
        """Run object detection on an in-memory RGB frame (Inferix contract).

        Serialises *img* as ``.npy`` (preserving shape + dtype) and uploads it
        as multipart ``file``, alongside the detection overrides.

        Args:
            img:      ``HxWx3`` uint8 **RGB** numpy array (a frame in memory).
            conf:     Confidence threshold.
            iou:      NMS IoU threshold.
            min_size: Minimum box size filter.
            max_size: Maximum box size filter.
            prompt:   Open-vocabulary detection prompt.
            url:      Override the configured ``/infer_array`` endpoint.
            field:    Multipart field name (default ``"file"``).
            filename: Upload filename (default ``"frame.npy"``).
            extra:    Any additional form fields to send.

        Returns:
            Parsed JSON detections.

        Raises:
            requests.HTTPError: If the endpoint returns a non-2xx status.
        """
        import numpy as np  # local import: numpy only needed for inference
        buf = io.BytesIO()
        np.save(buf, img)            # serialize as .npy (keeps shape + dtype)
        buf.seek(0)

        data = {'conf': conf, 'iou': iou, 'min_size': min_size,
                'max_size': max_size, 'prompt': prompt, **extra}
        r = self.session.post(
            url or self.url,
            files={field: (filename, buf, 'application/octet-stream')},
            data=data,
            timeout=self.timeout,
        )
        r.raise_for_status()
        self.server_connected = True
        return r.json()

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        """Best-effort reachability check against the endpoint host."""
        if not self.url:
            return False
        try:
            # HEAD is cheap; many inference endpoints reject it (405) but a
            # response at all means the host is reachable.
            self.session.head(self.url, timeout=min(self.timeout, 5.0),
                              allow_redirects=True)
            self.server_connected = True
        except requests.RequestException:
            self.server_connected = False
        return self.server_connected


def test_client():
    import numpy as np
    from ..helpers import parse_keys_values
    args = parse_keys_values(optional_args={'url': '', 'token': ''})
    client = HttpClient(url=args['url'], token=args['token'] or None)
    img = np.zeros((640, 640, 3), dtype=np.uint8)
    print(client.infer_array(img, conf=0.3, iou=0.7, prompt='object'))


if __name__ == '__main__':
    test_client()
