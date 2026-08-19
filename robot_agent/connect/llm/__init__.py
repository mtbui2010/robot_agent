"""LLM backends and the config-driven factory that picks between them."""

from ..transport import LLMBackend


def init_llm_client(cfg: dict):
    """Instantiate an LLM client from a config dict.

    The ``name`` key selects the backend: ``llama``, ``chatgpt`` / ``openai``,
    or ``gemini``. Everything else in *cfg* is forwarded to the client.

    Args:
        cfg: Dict with at least ``{"name": "<backend>", ...}``.

    Returns:
        Initialised LLM client.

    Raises:
        ValueError: If ``name`` is missing or unsupported.
    """
    name = cfg.get('name', '')
    try:
        backend = LLMBackend(name.lower())
    except ValueError:
        supported = [b.value for b in LLMBackend if b != LLMBackend.OPENAI]
        raise ValueError(f"Unknown LLM backend: '{name}'. Supported: {supported}")

    if backend == LLMBackend.LLAMA:
        from .llama import LLamaClient
        return LLamaClient(**cfg)
    if backend in (LLMBackend.CHATGPT, LLMBackend.OPENAI):
        from .chatgpt import ChatGptClient
        return ChatGptClient(**cfg)
    if backend == LLMBackend.GEMINI:
        from .gemini import GeminiClient
        return GeminiClient(**cfg)
    raise NotImplementedError(f"LLM backend '{name}' has no client implementation yet.")
