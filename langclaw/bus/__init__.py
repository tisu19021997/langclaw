from langclaw.bus.asyncio_bus import AsyncioMessageBus
from langclaw.bus.base import (
    Attachment,
    AttachmentType,
    BaseMessageBus,
    InboundMessage,
    OutboundMessage,
)
from langclaw.bus.kafka_bus import KafkaMessageBus
from langclaw.bus.rabbitmq_bus import RabbitMQMessageBus


def make_message_bus(
    backend: str,
    *,
    rabbitmq_url: str = "amqp://guest:guest@localhost/",
    rabbitmq_queue: str = "langclaw.inbound",
    kafka_servers: str = "localhost:9092",
    kafka_topic: str = "langclaw.inbound",
    kafka_group_id: str = "langclaw",
) -> BaseMessageBus:
    """
    Factory that instantiates the correct bus backend from a config string.

    Args:
        backend: One of ``"asyncio"``, ``"rabbitmq"``, or ``"kafka"``.
    """
    if backend == "asyncio":
        return AsyncioMessageBus()
    if backend == "rabbitmq":
        return RabbitMQMessageBus(amqp_url=rabbitmq_url, queue_name=rabbitmq_queue)
    if backend == "kafka":
        return KafkaMessageBus(
            bootstrap_servers=kafka_servers,
            topic=kafka_topic,
            group_id=kafka_group_id,
        )
    raise ValueError(f"Unknown bus backend: {backend!r}. Choose 'asyncio', 'rabbitmq', or 'kafka'.")


__all__ = [
    "Attachment",
    "AttachmentType",
    "BaseMessageBus",
    "InboundMessage",
    "OutboundMessage",
    "AsyncioMessageBus",
    "RabbitMQMessageBus",
    "KafkaMessageBus",
    "make_message_bus",
]
