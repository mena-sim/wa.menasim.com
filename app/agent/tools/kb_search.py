from __future__ import annotations

from typing import Any

from app.agent.tools.context import ToolContext
from app.services.kb import store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "kb_search",
        "description": (
            "Search the menasim knowledge base for eSIM install/activation guides "
            "(iPhone and Android), troubleshooting, FAQ, and policies. Use this for "
            "any how-to / why-isn't-it-working / policy question before answering."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user's question or topic to look up."},
                "language": {
                    "type": "string",
                    "enum": ["en", "ar"],
                    "description": "Preferred KB language; defaults to the conversation language.",
                },
            },
            "required": ["query"],
        },
    },
}


def run(ctx: ToolContext, query: str, language: str | None = None) -> dict[str, Any]:
    lang = language or ctx.language or "en"
    results = store.query(query, language=lang, top_k=4)
    if not results:
        # Fallback: search without language filter
        results = store.query(query, language=None, top_k=4)
    if not results:
        return {"found": False, "message": "No knowledge base entries found."}
    return {
        "found": True,
        "results": [
            {
                "source": r["metadata"].get("source"),
                "title": r["metadata"].get("title"),
                "text": r["text"],
            }
            for r in results
        ],
    }
