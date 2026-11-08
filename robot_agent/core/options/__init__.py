"""Option plug-in interface of the engine (docs/options.md): spec, config, registry, dispatch, services.

Layer: engine. Nothing here knows an option, a robot, a language or a domain; it loads and calls what is registered.
"""

from .config import OptionConfig, resolve
from .dispatch import Dispatcher
from .registry import REGISTRY, Registry, register
from .services import Services
from .spec import HOOKS, NEXT, STOP, Key, Option, OptionSpec

__all__ = ["HOOKS", "NEXT", "STOP", "Key", "Option", "OptionSpec", "OptionConfig", "resolve", "Registry", "REGISTRY", "register",
           "Dispatcher", "Services"]
