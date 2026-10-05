"""What each provider is actually asked, because that is what gets billed."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from app.config import get_settings, override_settings
from app.utils.rag_claude_response import (
    ClaudeUnavailableError,
    get_rag_claude_response,
)
from app.utils.rag_cloudmodel_response import get_rag_cloudmodel_response
from app.utils.rag_prompt import SYSTEM_PROMPT


def _openai_reply(text: str = "answer"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


@pytest.mark.parametrize(
    ("model", "expected_id", "cap_key", "base_url"),
    [
        ("openai", "gpt-6-luna", "max_completion_tokens", None),
        ("gemini", "gemini-3.8-flash", "max_tokens", "generativelanguage"),
        ("deepseek", "deepseek-flash", "max_tokens", "api.deepseek.com"),
    ],
)
def test_each_provider_gets_its_configured_model_and_an_output_cap(
    model, expected_id, cap_key, base_url
):
    with patch("app.utils.rag_cloudmodel_response.OpenAI") as client_class:
        create = client_class.return_value.chat.completions.create
        create.return_value = _openai_reply()

        assert get_rag_cloudmodel_response("q", "context", model) == "answer"

    kwargs = create.call_args.kwargs
    assert kwargs["model"] == expected_id
    assert kwargs[cap_key] >= get_settings().cloud_model_max_output_tokens
    # Current OpenAI and Gemini models reject a non-default temperature.
    assert "temperature" not in kwargs
    assert kwargs["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    if base_url:
        assert base_url in client_class.call_args.kwargs["base_url"]


def test_the_model_ids_are_configuration_not_code():
    with (
        override_settings(openai_model="gpt-next-mini"),
        patch("app.utils.rag_cloudmodel_response.OpenAI") as client_class,
    ):
        create = client_class.return_value.chat.completions.create
        create.return_value = _openai_reply()
        get_rag_cloudmodel_response("q", "c", "openai")
    assert create.call_args.kwargs["model"] == "gpt-next-mini"


def test_claude_answers_through_the_anthropic_sdk_with_the_cheapest_model():
    reply = SimpleNamespace(
        stop_reason="end_turn",
        content=[
            SimpleNamespace(type="thinking", thinking="..."),
            SimpleNamespace(type="text", text="**Response:** 42"),
        ],
    )
    with patch("app.utils.rag_claude_response.anthropic.Anthropic") as client_class:
        client_class.return_value.messages.create.return_value = reply

        answer = get_rag_claude_response("What is it?", "context")

    assert answer == "**Response:** 42"
    kwargs = client_class.return_value.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == get_settings().cloud_model_max_output_tokens
    assert kwargs["system"] == SYSTEM_PROMPT
    assert client_class.call_args.kwargs["api_key"] == "test-anthropic-key"


def test_a_claude_refusal_is_an_answer_not_a_crash():
    reply = SimpleNamespace(stop_reason="refusal", content=[])
    with patch("app.utils.rag_claude_response.anthropic.Anthropic") as client_class:
        client_class.return_value.messages.create.return_value = reply
        assert "could not be answered" in get_rag_claude_response("q", "c")


def test_claude_errors_do_not_leak_provider_detail():
    error = anthropic.APIConnectionError(request=MagicMock())
    with patch("app.utils.rag_claude_response.anthropic.Anthropic") as client_class:
        client_class.return_value.messages.create.side_effect = error
        with pytest.raises(ClaudeUnavailableError) as raised:
            get_rag_claude_response("q", "c")
    assert str(raised.value) == "Claude could not be reached"


def test_only_a_few_chunks_of_the_document_go_to_the_model():
    """It was 100 chunks (about 25,000 tokens) per question."""
    assert get_settings().rag_top_k == 8
    assert len(SYSTEM_PROMPT) < 400
