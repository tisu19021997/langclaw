# Channels

Channels are the inbound/outbound interfaces between users and your agent. Enable them via environment variables; langclaw wires the rest.

## Built-in channels

=== "Telegram"

    ```bash
    uv add "langclaw[telegram]"
    ```

    ```env
    LANGCLAW__CHANNELS__TELEGRAM__ENABLED=true
    LANGCLAW__CHANNELS__TELEGRAM__TOKEN=123456:ABC-DEF...
    ```

=== "Discord"

    ```bash
    uv add "langclaw[discord]"
    ```

    ```env
    LANGCLAW__CHANNELS__DISCORD__ENABLED=true
    LANGCLAW__CHANNELS__DISCORD__TOKEN=your-bot-token
    ```

=== "Slack"

    ```bash
    uv add "langclaw[slack]"
    ```

    ```env
    LANGCLAW__CHANNELS__SLACK__ENABLED=true
    LANGCLAW__CHANNELS__SLACK__BOT_TOKEN=xoxb-...
    LANGCLAW__CHANNELS__SLACK__APP_TOKEN=xapp-...
    ```

=== "WebSocket"

    ```bash
    uv add "langclaw[websocket]"
    ```

    Useful for web frontends and the probe harness.

    ```env
    LANGCLAW__CHANNELS__WEBSOCKET__ENABLED=true
    LANGCLAW__CHANNELS__WEBSOCKET__PORT=18789   # default
    ```

    **Wire format.** Send a JSON envelope; the agent streams its reply back as
    a sequence of frames:

    ```jsonc
    // client → server
    { "type": "message", "content": "Hello!", "user_id": "u1", "context_id": "default" }
    // optionally target a named agent via metadata:
    { "type": "message", "content": "...", "metadata": { "agent_name": "researcher" } }

    // server → client (stream)
    { "type": "ai_chunk", "content": "Hel" }
    { "type": "ai_chunk", "content": "lo" }
    { "type": "ai_stream_end" }   // terminator — no content / is_final field
    ```

    `user_id` and `context_id` are optional but recommended: omitting them falls
    back to `"ws-anon"` / `"default"`, so **every anonymous connection shares one
    session**. Pass distinct values per user to isolate conversations.

=== "Matrix"

    ```bash
    uv add "langclaw[matrix]"
    ```

    ```env
    LANGCLAW__CHANNELS__MATRIX__ENABLED=true
    LANGCLAW__CHANNELS__MATRIX__HOMESERVER_URL=https://matrix.org
    LANGCLAW__CHANNELS__MATRIX__USER_ID=@yourbot:matrix.org
    LANGCLAW__CHANNELS__MATRIX__ACCESS_TOKEN=syt_...
    LANGCLAW__CHANNELS__MATRIX__DEVICE_ID=LANGCLAWBOT
    ```

    To exercise this without writing a client, use the [probe harness](probe.md)
    or `langclaw probe 'your message'` against a running `langclaw gateway --probe`.

## Custom channel

Subclass `BaseChannel`:

```python
from langclaw.gateway.base import BaseChannel
from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage

class MyChannel(BaseChannel):
    name = "my_channel"

    async def start(self, bus: BaseMessageBus) -> None:
        # Connect to your service and publish InboundMessage to the bus.
        # channel, user_id, context_id, and content are all required.
        async for event in my_service.events():
            await bus.publish(InboundMessage(
                channel=self.name,
                user_id=event.user_id,
                context_id=event.thread_id,   # required — groups a conversation
                content=event.text,
            ))

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        # Deliver AI response back to the user. chat_id is the platform address.
        await my_service.send(msg.chat_id, msg.content)

    async def stop(self) -> None:
        await my_service.disconnect()
```

Register it on the app:

```python
app.add_channel(MyChannel())
```

## Slash commands

Commands bypass the message bus and LLM entirely — they return instantly.

```python
from langclaw.gateway.commands import CommandContext

@app.command("ping", description="health check")
async def ping(ctx: CommandContext) -> str:
    return "pong"
```

Users trigger them with `/ping` in any channel.

Built-in commands and when they're registered:

| Command | Available when |
|---|---|
| `/start`, `/reset`, `/help` | always |
| `/agentsmd`, `/logs`, `/file` | gateway mode (filesystem-rooted workspace) |
| `/cron` | `LANGCLAW__CRON__ENABLED=true` |
| `/agent` | at least one named agent is registered |
| `/workflows` | `LANGCLAW__WORKFLOWS__ENABLED=true` |
