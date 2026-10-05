"""PDF answers from the OpenAI-compatible providers: OpenAI, Gemini, DeepSeek.

Claude is in ``rag_claude_response.py`` (its own SDK). Which model each
provider answers with is configuration (``*_MODEL`` in .env, reasoning in
docs/models.md), as is the cap on an answer's length: together with RAG_TOP_K
they bound what one question can cost.
"""

from typing import Any

from openai import OpenAI

from app.config import get_settings
from app.utils.constants import model_names, models_supported
from app.utils.rag_prompt import SYSTEM_PROMPT, user_message

# A cloud call that hangs would hold a worker thread until the job time limit.
CLOUD_MODEL_TIMEOUT_SECONDS = 120.0


def get_rag_cloudmodel_response(query: str, relevant_context: str, model: str) -> str:
    """Get a response from the RAG model using Cloud Models."""
    settings = get_settings()
    limit = settings.cloud_model_max_output_tokens
    # Sampling parameters are deliberately not sent: the current OpenAI and
    # Gemini models reject or ignore a non-default temperature.
    extra: dict[str, Any]
    if model == models_supported["gemini"]:
        client = OpenAI(
            api_key=settings.gemini_api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=CLOUD_MODEL_TIMEOUT_SECONDS,
        )
        # Gemini 3 models always reason, and reasoning is billed as output:
        # ask for the least. The cap leaves room for it.
        extra = {"max_tokens": limit * 2, "reasoning_effort": "low"}
    elif model == models_supported["deepseek"]:
        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            timeout=CLOUD_MODEL_TIMEOUT_SECONDS,
        )
        extra = {"max_tokens": limit}
    elif model == models_supported["openai"]:
        client = OpenAI(
            api_key=settings.openai_api_key, timeout=CLOUD_MODEL_TIMEOUT_SECONDS
        )
        extra = {"max_completion_tokens": limit}
    else:
        raise ValueError(f"Invalid model: {model}")

    response = client.chat.completions.create(
        model=model_names(settings)[model],
        stream=False,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message(query, relevant_context)},
        ],
        **extra,
    )

    return str(response.choices[0].message.content or "")
