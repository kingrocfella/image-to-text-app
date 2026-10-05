import asyncio

import ollama

from app.config import get_settings
from app.utils.rag_prompt import SYSTEM_PROMPT, user_message

_settings = get_settings()

# The timeout bounds the blocking read: without it a stalled daemon would hold
# a worker thread until the job's own time limit.
client = ollama.Client(
    host=_settings.ollama_url, timeout=_settings.ollama_timeout_seconds
)

# The shared VPS runs exactly one Ollama daemon (Lost Vowels'). Keeping the tag
# and the generation bounds in .env lets this app converge on a model that
# daemon already holds, instead of forcing it to swap weights per request.
OLLAMA_MODEL = _settings.ollama_model
OLLAMA_TEMPERATURE = _settings.ollama_temperature
OLLAMA_NUM_PREDICT = _settings.ollama_num_predict


async def get_rag_ollama_response(query: str, relevant_context: str) -> str:
    """Get a response from the RAG model using Ollama."""

    prompt = f"{SYSTEM_PROMPT}\n\n{user_message(query, relevant_context)}"

    def _generate():
        response = client.generate(
            model=OLLAMA_MODEL,
            prompt=prompt,
            stream=False,
            options={
                "temperature": OLLAMA_TEMPERATURE,
                "num_predict": OLLAMA_NUM_PREDICT,
            },
        )
        return str(response["response"])

    response = await asyncio.to_thread(_generate)
    return response
