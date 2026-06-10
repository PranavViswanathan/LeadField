"""Thin wrapper around the Ollama REST generation API.

Exposes two entry points:

* :func:`generate` -- blocking call that returns the full completion.
* :func:`generate_stream` -- generator yielding token chunks as they arrive.

Both handle transient network/server failures with bounded retries and fall
back from the primary model to a secondary model when the primary is not
installed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import Settings, get_settings


class OllamaError(RuntimeError):
    """Raised when Ollama cannot satisfy a generation request."""


class OllamaModelMissingError(OllamaError):
    """Raised when the requested model is not installed in Ollama."""


# Errors worth retrying: transient network issues and 5xx responses.
_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


@dataclass(frozen=True)
class GenerationResult:
    """Result of a completed (non-streaming) generation."""

    text: str
    model: str


def _is_model_missing(response: httpx.Response) -> bool:
    """Return True if a 404 response indicates an uninstalled model."""
    if response.status_code != httpx.codes.NOT_FOUND:
        return False
    return "model" in response.text.lower()


def _request_payload(
    *, model: str, prompt: str, stream: bool, options: dict[str, float]
) -> dict[str, object]:
    return {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": options,
    }


def _generate_once(
    *,
    client: httpx.Client,
    model: str,
    prompt: str,
    temperature: float,
) -> str:
    """Issue a single non-streaming generation request for ``model``."""

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _call() -> httpx.Response:
        response = client.post(
            "/api/generate",
            json=_request_payload(
                model=model,
                prompt=prompt,
                stream=False,
                options={"temperature": temperature},
            ),
        )
        if _is_model_missing(response):
            raise OllamaModelMissingError(f"model '{model}' not installed")
        response.raise_for_status()
        return response

    response = _call()
    data = response.json()
    return str(data.get("response", "")).strip()


def generate(
    prompt: str,
    *,
    temperature: float = 0.7,
    settings: Settings | None = None,
) -> GenerationResult:
    """Generate a completion for ``prompt``, returning the full text.

    Tries the primary model first; if it is not installed, transparently
    retries with the configured fallback model.

    Args:
        prompt: The full prompt to send to the model.
        temperature: Sampling temperature.
        settings: Optional settings override (defaults to :func:`get_settings`).

    Returns:
        A :class:`GenerationResult` with the generated text and model used.

    Raises:
        OllamaError: If generation fails on both primary and fallback models.
    """
    cfg = settings or get_settings()
    models = [cfg.ollama_model, cfg.ollama_fallback_model]

    last_error: Exception | None = None
    with httpx.Client(
        base_url=cfg.ollama_base_url, timeout=cfg.ollama_timeout_seconds
    ) as client:
        for model in models:
            try:
                text = _generate_once(
                    client=client,
                    model=model,
                    prompt=prompt,
                    temperature=temperature,
                )
                return GenerationResult(text=text, model=model)
            except OllamaModelMissingError as exc:
                last_error = exc
                continue
            except _RETRYABLE as exc:
                last_error = exc
                continue

    raise OllamaError(
        f"generation failed for models {models}: {last_error}"
    ) from last_error


def generate_stream(
    prompt: str,
    *,
    temperature: float = 0.7,
    settings: Settings | None = None,
) -> Iterator[str]:
    """Stream a completion for ``prompt`` chunk by chunk.

    Yields the incremental ``response`` fragments emitted by Ollama. Falls back
    to the secondary model if the primary is missing. Streaming responses are
    not retried mid-stream; transient connection failures raise
    :class:`OllamaError`.

    Args:
        prompt: The full prompt to send to the model.
        temperature: Sampling temperature.
        settings: Optional settings override.

    Yields:
        Text fragments of the generated completion in order.

    Raises:
        OllamaError: If the stream cannot be established on any model.
    """
    cfg = settings or get_settings()
    models = [cfg.ollama_model, cfg.ollama_fallback_model]
    last_error: Exception | None = None

    with httpx.Client(
        base_url=cfg.ollama_base_url, timeout=cfg.ollama_timeout_seconds
    ) as client:
        for model in models:
            try:
                yield from _stream_one(
                    client=client,
                    model=model,
                    prompt=prompt,
                    temperature=temperature,
                )
                return
            except OllamaModelMissingError as exc:
                last_error = exc
                continue
            except _RETRYABLE as exc:
                last_error = exc
                continue

    raise OllamaError(
        f"streaming failed for models {models}: {last_error}"
    ) from last_error


def _stream_one(
    *,
    client: httpx.Client,
    model: str,
    prompt: str,
    temperature: float,
) -> Iterator[str]:
    """Stream a single model's response, raising on a missing model."""
    with client.stream(
        "POST",
        "/api/generate",
        json=_request_payload(
            model=model,
            prompt=prompt,
            stream=True,
            options={"temperature": temperature},
        ),
    ) as response:
        if response.status_code != httpx.codes.OK:
            # Body is not read yet on a streamed response; read before inspecting.
            response.read()
            if _is_model_missing(response):
                raise OllamaModelMissingError(f"model '{model}' not installed")
            response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            fragment = chunk.get("response")
            if fragment:
                yield fragment
            if chunk.get("done"):
                return


def health_check(settings: Settings | None = None) -> bool:
    """Return True if the Ollama server responds to ``/api/tags``."""
    cfg = settings or get_settings()
    try:
        with httpx.Client(base_url=cfg.ollama_base_url, timeout=5.0) as client:
            response = client.get("/api/tags")
            return response.status_code == httpx.codes.OK
    except httpx.HTTPError:
        return False
