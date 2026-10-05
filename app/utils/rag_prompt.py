"""The one prompt every PDF answer is generated from.

Kept short on purpose: the instructions are sent with every question, so each
line here is paid for on every request, by every provider.
"""

SYSTEM_PROMPT = (
    "You answer questions about a PDF using only the excerpts provided. "
    "If the excerpts do not contain the answer, say so; never invent "
    "information. Be concise. Reply in Markdown with exactly two sections: "
    "**Query:** restating the question, then **Response:** with the answer."
)


def user_message(query: str, relevant_context: str) -> str:
    return f"Excerpts from the PDF:\n\n{relevant_context}\n\nQuestion: {query}"
