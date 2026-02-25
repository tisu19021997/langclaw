# Langclaw Architecture & Roadmap

This document details the core design principles, architectural decisions, and future roadmap of the Langclaw framework. For high-level diagrams, package structures, and data flow, please refer to the [README](../README.md).

## Design Vision: A Framework, Not an App

Langclaw's fundamental philosophy is to be a **framework** that developers build upon, similar to FastAPI or Flask, rather than a standalone application to be forked. 

### Core Tenets

1. **Explicit Registration over Implicit Magic:** Tools, channels, and middleware are registered explicitly on the `Langclaw` app object (e.g., `@app.tool()`, `app.add_channel()`). We avoid auto-discovery (like directory scanning) because explicit registration is safer and more predictable for production systems.
2. **Pluggability:** The framework provides robust abstractions (Message Bus, Checkpointer, Providers) that can easily be swapped out. You can use the built-in SQLite checkpointer or write your own Postgres implementation.
3. **Middleware-Driven Safety:** Security, rate limiting, and Role-Based Access Control (RBAC) are implemented as middleware. This ensures all interactions, regardless of the channel or tool, pass through the same security checks before reaching the LLM.

## Architectural Deep Dive

While the README shows the physical data flow, here we analyze the *why* behind the core components:

### The `Langclaw` App Class
Previously, developers had to manually wire the LangGraph agent, gateway, bus, and channels. The introduction of the `Langclaw` class unified this. It serves as the central registry and orchestrator, managing the lifecycle of the entire system (startup/shutdown hooks, tool scoping, and channel initialization).

### Message Bus (`BaseMessageBus`)
Channels and the cron scheduler do not talk to the agent directly. They publish `InboundMessage` objects to a unified bus. 
- **Why?** This decoupling allows the gateway to horizontally scale. You can swap the default `asyncio` memory bus for RabbitMQ or Kafka in distributed environments.

### Middleware Pipeline
Instead of hardcoding tool permission logic into the agent prompt, Langclaw uses a middleware pipeline (e.g., `ToolPermissionMiddleware`).
- **Why?** It securely filters the available tools based on the user's resolved role *before* the LangGraph agent even sees them, preventing prompt injection attacks from accessing restricted tools.

### Checkpointer Abstraction
Conversation state is handled by `BaseCheckpointerBackend`.
- **Why?** AI agents require persistent memory across asynchronous channel events. Abstracting this allows swapping between in-memory (testing), SQLite (local deployments), and robust databases (production) without changing agent logic.

## Comparison with Alternative Frameworks

Understanding where Langclaw sits in the ecosystem helps clarify its architectural choices:

### OpenClaw
- **Approach:** Highly declarative plugin system with auto-discovery from an `extensions/` directory.
- **Pros:** Very extensible, great UX via a dedicated CLI plugin manager.
- **Cons:** TypeScript-only, high configuration surface area, and heavy plugin manifest boilerplate.

### Langclaw's Position
Langclaw aims to be a robust production-ready framework (thanks to the LangChain/LangGraph ecosystem) that is simpler and more explicit in Python than OpenClaw.



## Roadmap

The framework is evolving in phases to reach full maturity.

### Phase 1: The App Class & Explicit Registration (✅ Completed)
- Created `Langclaw` as the central application object.
- Replaced hardcoded tool loading with explicit `@app.tool()` and `register_tools()`.
- Added lifecycle hooks (`@app.on_startup`, `@app.on_shutdown`).

### Phase 2: Kill the Global Config Singleton (✅ Completed)
- Removed the module-level `config: LangclawConfig = load_config()` singleton from `langclaw.config.schema`.
- Configuration now flows exclusively through the `Langclaw` app object or via explicit `load_config()` calls.
- The CLI loads config lazily inside each command, enabling multiple distinct `Langclaw` instances in a single process.

### Phase 3: Dynamic Provider Registration (🚧 Planned)
LLM providers are currently maintained in a module-level list.
- **Goal:** Add a `register()` method to `ProviderRegistry` so developers can inject custom model providers (e.g., local Ollama endpoints or proprietary enterprise gateways) without modifying core code.

### Phase 4: Multi-Agent Support (🚧 Planned)
Currently, a single `Langclaw` app maps to a single agent configuration.
- **Goal:** Support named agents with distinct models and per-agent tool sets, allowing complex multi-agent routing.
- **Example Use Case:** A "support" agent running a fast Haiku model with FAQ tools, and an "admin" agent running a reasoning model with full system access, routed based on channel or user intent.
