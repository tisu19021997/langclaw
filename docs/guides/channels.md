# Channels

Channels are the inbound/outbound interfaces between users and your agent. Enable them via environment variables; langclaw wires the rest.

## Built-in channels

=== "Telegram"

    ```bash
    uv add "langclaw[telegram]"
    ```

    ```env
    LANGCLAW__CHANNELS__TELEGRAM__ENABLED=true
    LANGCLAW__CHANNELS__TELEGRAM__BOT_TOKEN=123456:ABC-DEF...
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

    WebSocket is included in the base package. Useful for web frontends and the probe harness.

    ```env
    LANGCLAW__CHANNELS__WEBSOCKET__ENABLED=true
    LANGCLAW__CHANNELS__WEBSOCKET__PORT=8765
    ```

## Custom channel

Subclass `BaseChannel` in `langclaw/gateway/base.py`:

```python
from langclaw.gateway.base import BaseChannel
from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage

class MyChannel(BaseChannel):
    name = "my_channel"

    async def start(self, bus: BaseMessageBus) -> None:
        # Connect to your service and publish InboundMessage to the bus.
        async for event in my_service.events():
            await bus.publish(InboundMessage(
                channel=self.name,
                user_id=event.user_id,
                content=event.text,
            ))

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        # Deliver AI response back to the user.
        await my_service.send(msg.channel_user_id, msg.content)

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

Users trigger them with `/ping` in any channel. Built-in commands: `/start`, `/reset`, `/help`, `/agent`, `/workflows`.
