import os
import asyncio

import ollama

client = ollama.Client(host=os.getenv("OLLAMA_URL"))

# The shared VPS runs exactly one Ollama daemon (Lost Vowels'). Keeping the tag
# and the generation bounds in .env lets this app converge on a model that
# daemon already holds, instead of forcing it to swap weights per request.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b").strip()
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "500"))


async def get_rag_ollama_response(query: str, relevant_context: str) -> str:
    """Get a response from the RAG model using Ollama."""

    prompt = f"""Based on this context from a PDF:
      {relevant_context}

      Question: {query}

      Answer concisely using only the context provided. Do not make up information.
      And finally provide your answers in a markdown format. This is very important.
      For example, if the user's query is "What is 10 + 10?", the response should be:
        
      **Query:**
      What is 10 + 10?

      **Response:**
      The answer is **42**
      
      Do not use any other formatting.
      Do not use any other formatting.
    """

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
