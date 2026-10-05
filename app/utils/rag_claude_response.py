"""PDF answers from Claude, through the official Anthropic SDK.

The model is CLAUDE_MODEL (.env); the default is Claude Haiku 4.5, the least
expensive current Claude model, because the owner chose cost per answer over
capability for this feature (docs/models.md).
"""

import anthropic

from app.config import get_settings
from app.utils.logger import logger
from app.utils.rag_prompt import SYSTEM_PROMPT, user_message

CLAUDE_TIMEOUT_SECONDS = 120.0


class ClaudeUnavailableError(RuntimeError):
    """Claude could not answer; the message is safe to log, not to show."""


def get_rag_claude_response(query: str, relevant_context: str) -> str:
    """Answer one question about the retrieved PDF excerpts."""
    settings = get_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=CLAUDE_TIMEOUT_SECONDS,
        # The Dramatiq actor already retries the whole job.
        max_retries=1,
    )
    try:
        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=settings.cloud_model_max_output_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_message(query, relevant_context)}
            ],
        )
    except anthropic.RateLimitError as exc:
        raise ClaudeUnavailableError("Claude rate limit reached") from exc
    except anthropic.APIStatusError as exc:
        logger.error("Claude request failed with status %s", exc.status_code)
        raise ClaudeUnavailableError("Claude request failed") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeUnavailableError("Claude could not be reached") from exc

    if response.stop_reason == "refusal":
        return "**Response:**\nThis question could not be answered for this document."
    # content is a list of blocks; only text blocks carry the answer.
    return "".join(block.text for block in response.content if block.type == "text")
