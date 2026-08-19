import json
import queue
import asyncio
import ssl
import aiohttp
import threading
import base64
import numpy as np
from io import BytesIO
from PIL import Image
from aiortc import RTCPeerConnection, RTCSessionDescription


class WebRTCClient:
    def __init__(self, host='0.0.0.0', port=8443, **kwargs):
        self.server_url = f"https://{host}:{port}/offer"
        self.pc = RTCPeerConnection()
        self.channel = self.pc.createDataChannel("data")
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, daemon=True).start()

        # Future to wait for channel open
        self.channel_ready = asyncio.run_coroutine_threadsafe(
            self._create_future(), self.loop
        ).result()
        # FIFO list of futures waiting for a response; responses on a single
        # DataChannel are ordered, so pop(0) always matches the right send().
        self._pending_responses: list = []
        # Bounded queue (maxsize=2): always keep the latest frame,
        # drop the oldest when full to avoid lag build-up.
        self.stream_queue: queue.Queue = queue.Queue(maxsize=2)
        self.connected: bool = False

        # Callbacks
        @self.channel.on("open")
        def on_open():
            if not self.channel_ready.done():
                self.channel_ready.set_result(True)

        @self.channel.on("message")
        def on_message(message):
            try:
                data = json.loads(message)
            except Exception:
                return
            # Stream frames go to the queue; everything else resolves the
            # oldest pending send() future (FIFO — DataChannel is ordered).
            if data.get("type") == "stream":
                try:
                    self.stream_queue.put_nowait(data)
                except queue.Full:
                    try:
                        self.stream_queue.get_nowait()   # drop oldest frame
                    except queue.Empty:
                        pass
                    self.stream_queue.put_nowait(data)
            elif self._pending_responses:
                fut = self._pending_responses.pop(0)
                if not fut.done():
                    fut.set_result(data)

        # Connect synchronously so the object is ready to use on return
        self._connect(host, port)

    # --------------------------
    # Background loop helpers
    # --------------------------
    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _create_future(self):
        return asyncio.get_event_loop().create_future()

    # --------------------------
    # Connection
    # --------------------------
    def _connect(self, host: str, port: int, timeout: float = 10.0):
        """Block until the WebRTC data channel is open. Sets self.connected."""
        print(f"[WebRTCClient] Connecting to {host}:{port} …")
        fut = asyncio.run_coroutine_threadsafe(self._async_init(), self.loop)
        try:
            fut.result(timeout=timeout)
            self.connected = True
            print(f"[WebRTCClient] Connected to {host}:{port}")
        except Exception as e:
            self.connected = False
            print(f"[WebRTCClient] Not connected to {host}:{port} — {e}")

    async def _async_init(self):
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        # Server uses a self-signed cert — skip verification
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.server_url,
                json={"sdp": offer.sdp, "type": offer.type},
                ssl=ssl_ctx,
            ) as resp:
                answer = await resp.json()

        await self.pc.setRemoteDescription(
            RTCSessionDescription(answer["sdp"], answer["type"])
        )

        await self.channel_ready

    # --------------------------
    # Sending data
    # --------------------------
    async def _async_send(self, data: dict, timeout: float) -> dict:
        await self.channel_ready
        fut = self.loop.create_future()
        self._pending_responses.append(fut)
        self.channel.send(json.dumps(data))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            try:
                self._pending_responses.remove(fut)
            except ValueError:
                pass
            raise TimeoutError(f"send() got no response from server within {timeout}s")

    def send(self, data: dict, timeout: float = 10.0) -> dict:
        """Sync send; raises RuntimeError if not connected, TimeoutError if no response."""
        if not self.connected:
            raise RuntimeError("[WebRTCClient] Not connected to server")
        future = asyncio.run_coroutine_threadsafe(
            self._async_send(data, timeout), self.loop
        )
        return future.result(timeout=timeout + 1)  # outer safety net

    def fetch(self, timeout: float = 5.0):
        """
        Generator that yields decoded stream frames sent by the server.

        Each yielded dict may contain:
          "rgb"   – numpy uint8 H×W×3 array
          "depth" – numpy array with original dtype and shape
          plus any extra JSON fields the server included.

        Args:
            timeout: seconds to wait for the next frame before stopping
                     (None = wait forever).

        Example::
            for frame in client.fetch():
                rgb   = frame.get("rgb")    # numpy H×W×3
                depth = frame.get("depth")  # numpy H×W
        """
        while True:
            try:
                frame = self.stream_queue.get(timeout=timeout)
            except queue.Empty:
                return  # no new frame within timeout → stop iteration

            # Remove the routing key before handing the frame to the caller
            frame.pop("type", None)

            # Leave "rgb" as base64 string — caller forwards it directly
            # without decode/re-encode (avoids double encode cycle).

            # Decode depth raw bytes → numpy
            if "depth" in frame:
                d = frame["depth"]
                arr = np.frombuffer(base64.b64decode(d["data"]), dtype=d["dtype"])
                frame["depth"] = arr.reshape(d["shape"])

            yield frame


# --------------------------
# Example usage
# --------------------------
if __name__ == "__main__":
    from ..helpers import Timer

    timer = Timer()
    client = WebRTCClient()
    timer.pin_time("init")

    # One-shot request/response
    ret = client.send({"value": 100})
    print(f"send return: {ret}")
    timer.pin_time("send1")

    print(timer.pin_times_str)

    # Continuous stream
    print("Waiting for stream frames (Ctrl-C to stop)…")
    for frame in client.fetch(timeout=5.0):
        rgb   = frame.get("rgb")    # numpy H×W×3 uint8
        depth = frame.get("depth")  # numpy H×W float32
        idx   = frame.get("frame")
        print(f"frame {idx}: rgb={rgb.shape if rgb is not None else None}, "
              f"depth={depth.shape if depth is not None else None}")
