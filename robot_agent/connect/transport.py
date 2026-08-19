"""Abstract base classes and enumerations for all transports.

Transport servers/clients inherit from BaseTransport / BaseServer so they can
be swapped transparently. Use TransportType / LLMBackend everywhere instead of
raw strings, so a typo raises instead of silently selecting nothing.

Example
-------
>>> from robot_agent.connect import TransportType, init_detect_client
>>> client = init_detect_client("tcp:localhost:8888")   # validated by Enum
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TransportType(Enum):
    """Supported point-to-point transport backends."""
    TCP       = "tcp"
    ZMQ       = "zmq"
    WEBRTC    = "webrtc"
    UDP       = "udp"
    MQTT      = "mqtt"
    HTTP      = "http"
    WEBSOCKET = "websocket"
    VISIONSERVE = "visionserve"


class LLMBackend(Enum):
    """Supported LLM backend identifiers."""
    LLAMA   = "llama"
    CHATGPT = "chatgpt"
    OPENAI  = "openai"    # alias for chatgpt
    GEMINI  = "gemini"


# ---------------------------------------------------------------------------
# Abstract transport interface
# ---------------------------------------------------------------------------

class BaseTransport(ABC):
    """Minimal interface every transport client must implement.

    Subclasses only need to provide ``send`` and ``close``; the context
    manager protocol is provided for free.

    Example
    -------
    Implementing a custom transport::

        class MyTransport(BaseTransport):
            def send(self, data):
                ...
                return response

            def close(self):
                self._sock.close()
    """

    @abstractmethod
    def send(self, data: Any) -> Any:
        """Send *data* to the remote endpoint and return the response.

        Args:
            data: Anything serialisable by the transport's codec (dict,
                  numpy array, str, …).

        Returns:
            The decoded response from the remote endpoint, or ``None`` on
            failure.
        """

    @abstractmethod
    def close(self) -> None:
        """Release all resources held by this transport."""

    # Context manager support — allows ``with MyTransport(...) as t: ...``
    def __enter__(self) -> "BaseTransport":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class BaseServer(ABC):
    """Minimal interface every transport server must implement."""

    @abstractmethod
    def listen(self, run_thread: bool = False) -> None:
        """Start accepting connections.

        Args:
            run_thread: When ``True`` the server runs in a background daemon
                        thread so the caller is not blocked.
        """

    @abstractmethod
    def stop(self) -> None:
        """Shut down the server and release all resources."""
