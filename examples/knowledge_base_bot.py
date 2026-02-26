"""
Knowledge Base Bot — a multi-channel assistant that answers questions
using a RAG subagent, tracks token usage, and blocks off-topic requests.

Demonstrates
------------
- ``app.subagent(graph=...)``  — bring-your-own LangGraph RAG pipeline
- ``app.add_middleware()``     — custom LangChain middleware (token-usage logger)
- ``@app.command()``           — fast ``/usage`` command (bypasses the LLM)
- ``app.role()``               — RBAC: admins get all tools, members are scoped

RAG subagent
------------
A 2-node LangGraph ``StateGraph`` that the main agent delegates to via
the ``task`` tool whenever the user asks a company-policy question:

    retrieve  →  generate

- **retrieve** — embeds the question, searches an ``InMemoryVectorStore``
  seeded with company-policy sentences.
- **generate** — feeds the retrieved context + question into an LLM and
  returns a concise answer.

Run
---
1. Copy ``.env.example`` to ``.env`` and fill in at least one LLM provider key
   and one channel token.
2. ``pip install 'langclaw[telegram]'``
3. ``python examples/knowledge_base_bot.py``
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.messages import AIMessage
from langchain_core.vectorstores import InMemoryVectorStore
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.runtime import Runtime
from loguru import logger
from typing_extensions import TypedDict

from langclaw import Langclaw
from langclaw.gateway.commands import CommandContext

app = Langclaw(
    system_prompt=(
        "## Knowledge Base Bot\n"
        "You are a helpful support assistant for an e-commerce company.\n\n"
        "- For company-policy questions (shipping, returns, refunds, "
        "pricing, support hours, warranties, accounts, payments), "
        "delegate to the **kb-rag** subagent.\n"
        "- For general questions, answer directly."
    ),
)

# ---------------------------------------------------------------------------
# Company knowledge base — sentences embedded into a vector store
# ---------------------------------------------------------------------------

KB_SENTENCES = [
    "Refund policy: full refund within 30 days of purchase for unused items.",
    "After 30 days, only store credit is available. Contact support@example.com.",
    "Standard shipping takes 5-7 business days and is free on orders over $50.",
    "Express shipping costs $12.99 and delivers in 1-2 business days.",
    "International shipping is available to 40+ countries; fees calculated at checkout.",
    "Returns are accepted within 30 days. Items must be unused and in original packaging.",
    "Start a return at example.com/returns or contact our support team.",
    "Defective items may be returned within 90 days for a full replacement.",
    "Support hours are Monday through Friday, 9 AM to 6 PM Eastern Time.",
    "Weekend support is available via email only — expect a response within 24 hours.",
    "Live chat is available during business hours at example.com/chat.",
    "All electronics come with a 1-year manufacturer warranty.",
    "Extended warranty plans (2 or 3 years) can be purchased at checkout.",
    "We accept Visa, Mastercard, AMEX, PayPal, and Apple Pay.",
    "Gift cards never expire and can be used on any product in our store.",
    "Loyalty members earn 2 points per dollar spent. 500 points = $10 discount.",
    "Price matching is available within 14 days of purchase if you find a lower price.",
    "Bulk orders of 50+ units receive a 15% discount — email sales@example.com.",
    "Account deletion requests are processed within 5 business days.",
    "Two-factor authentication is available in your account security settings.",
]

embeddings = init_embeddings("azure_openai:text-embedding-3-small")
vectorstore = InMemoryVectorStore.from_texts(KB_SENTENCES, embedding=embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

logger.info("Retriever is ready!")

# ---------------------------------------------------------------------------
# RAG subagent graph — retrieve → generate
# ---------------------------------------------------------------------------


class RAGState(TypedDict):
    messages: Annotated[list, add_messages]
    context: str


rag_llm = init_chat_model("azure_openai:gpt-5-mini")


def retrieve(state: RAGState) -> dict[str, Any]:
    """Embed the question and retrieve relevant KB passages."""
    question = state["messages"][-1].content
    docs = retriever.invoke(question)
    context = "\n".join(doc.page_content for doc in docs)
    return {"context": context}


def generate(state: RAGState) -> dict[str, Any]:
    """Answer the question using retrieved context."""
    question = state["messages"][-1].content
    context = state.get("context", "")
    prompt = (
        "You are a company support assistant. Use ONLY the context below "
        "to answer the question. If the context doesn't contain the "
        "answer, say you don't know.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}"
    )
    response = rag_llm.invoke([{"role": "user", "content": prompt}])
    return {"messages": [AIMessage(content=response.content)]}


workflow = StateGraph(RAGState)
workflow.add_node("retrieve", retrieve)
workflow.add_node("generate", generate)
workflow.add_edge(START, "retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)

rag_graph = workflow.compile()

# ---------------------------------------------------------------------------
# Register the RAG graph as a subagent
# ---------------------------------------------------------------------------

app.subagent(
    "kb-rag",
    description=(
        "Answers company-policy questions (shipping, returns, refunds, "
        "warranties, payments, support hours, accounts) using a knowledge "
        "base. Delegate any policy-related question to this subagent."
    ),
    graph=rag_graph,
)

# ---------------------------------------------------------------------------
# Custom middleware — token-usage tracker (LangChain AgentMiddleware)
# ---------------------------------------------------------------------------

_usage_log: dict[str, dict[str, int]] = defaultdict(
    lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0}
)


class UsageTrackerMiddleware(AgentMiddleware):
    """Track per-user model call counts and token usage.

    Uses LangChain's ``wrap_model_call`` hook to intercept every
    model invocation, record stats, and pass through transparently.

    Channel context is read from ``runtime.context`` — a
    ``LangclawContext`` instance that langclaw always injects.
    """

    @staticmethod
    def _get_user_id(request: ModelRequest) -> str:
        ctx = getattr(request.runtime, "context", None)
        return ctx.user_id if ctx else "unknown"

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        response = await handler(request)

        user_id = self._get_user_id(request)
        _usage_log[user_id]["calls"] += 1

        last_msg = response.result[-1] if response.result else None
        usage = getattr(last_msg, "usage_metadata", None) if last_msg else None
        if usage:
            _usage_log[user_id]["input_tokens"] += usage.get("input_tokens", 0)
            _usage_log[user_id]["output_tokens"] += usage.get("output_tokens", 0)
        logger.debug("Usage for {}: {}", user_id, dict(_usage_log[user_id]))

        return response

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Log each agent invocation with a timestamp."""
        ctx = getattr(runtime, "context", None)
        user = ctx.user_id if ctx else "?"
        logger.info(
            "[{}] Agent invoked by user {} ({} messages)",
            datetime.now(UTC).strftime("%H:%M:%S"),
            user,
            len(state.get("messages", [])),
        )
        return None


app.add_middleware(UsageTrackerMiddleware())


# ---------------------------------------------------------------------------
# Custom command — show token usage stats (no LLM call)
# ---------------------------------------------------------------------------


@app.command("usage", description="show your token usage stats")
async def usage_cmd(ctx: CommandContext) -> str:
    stats = _usage_log.get(ctx.user_id)
    if not stats or stats["calls"] == 0:
        return "No usage recorded yet. Send a message first!"
    return (
        f"Your usage stats:\n"
        f"  Model calls:   {stats['calls']}\n"
        f"  Input tokens:  {stats['input_tokens']}\n"
        f"  Output tokens: {stats['output_tokens']}"
    )


# ---------------------------------------------------------------------------
# RBAC — admins get all tools, members get KB + search only
# ---------------------------------------------------------------------------

app.role("admin", tools=["*"])
app.role("member", tools=["web_search"])


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run()
