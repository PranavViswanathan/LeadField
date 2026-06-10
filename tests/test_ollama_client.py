"""Behavior tests for the Ollama REST client."""

from __future__ import annotations

import httpx
import pytest
import respx

from tasks import ollama_client


@respx.mock
def test_generate_returns_text_and_model(ollama_settings) -> None:
    respx.post("http://ollama.test:11434/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "  hello world  "})
    )
    result = ollama_client.generate("hi", settings=ollama_settings)
    assert result.text == "hello world"
    assert result.model == "primary-model"


@respx.mock
def test_generate_falls_back_when_primary_model_missing(ollama_settings) -> None:
    route = respx.post("http://ollama.test:11434/api/generate")
    route.side_effect = [
        httpx.Response(404, json={"error": "model 'primary-model' not found"}),
        httpx.Response(200, json={"response": "from fallback"}),
    ]
    result = ollama_client.generate("hi", settings=ollama_settings)
    assert result.text == "from fallback"
    assert result.model == "fallback-model"


@respx.mock
def test_generate_raises_when_all_models_missing(ollama_settings) -> None:
    respx.post("http://ollama.test:11434/api/generate").mock(
        return_value=httpx.Response(404, json={"error": "model not found"})
    )
    with pytest.raises(ollama_client.OllamaError):
        ollama_client.generate("hi", settings=ollama_settings)


@respx.mock
def test_generate_stream_yields_fragments(ollama_settings) -> None:
    body = (
        '{"response": "Hello", "done": false}\n'
        '{"response": " there", "done": false}\n'
        '{"response": "!", "done": true}\n'
    )
    respx.post("http://ollama.test:11434/api/generate").mock(
        return_value=httpx.Response(200, text=body)
    )
    fragments = list(ollama_client.generate_stream("hi", settings=ollama_settings))
    assert fragments == ["Hello", " there", "!"]
    assert "".join(fragments) == "Hello there!"


@respx.mock
def test_generate_stream_falls_back_on_missing_model(ollama_settings) -> None:
    route = respx.post("http://ollama.test:11434/api/generate")
    route.side_effect = [
        httpx.Response(404, json={"error": "model 'primary-model' not found"}),
        httpx.Response(200, text='{"response": "ok", "done": true}\n'),
    ]
    fragments = list(ollama_client.generate_stream("hi", settings=ollama_settings))
    assert fragments == ["ok"]


@respx.mock
def test_health_check_true_when_tags_ok(ollama_settings) -> None:
    respx.get("http://ollama.test:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    assert ollama_client.health_check(ollama_settings) is True


@respx.mock
def test_health_check_false_when_unreachable(ollama_settings) -> None:
    respx.get("http://ollama.test:11434/api/tags").mock(
        side_effect=httpx.ConnectError("refused")
    )
    assert ollama_client.health_check(ollama_settings) is False
