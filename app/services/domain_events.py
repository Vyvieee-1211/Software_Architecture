"""Sự kiện nghiệp vụ phát sau khi giao dịch đã commit."""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainEvent:
    name: Literal["OrderHeld", "OrderPaid", "TicketsIssued", "HoldExpired"]
    order_id: int
    timestamp: datetime
    quantity: int
    webhook_to_qr_ms: float | None = None


class DomainEvents:
    def __init__(self, *listeners: Callable[[DomainEvent], None]):
        self._listeners = listeners

    def publish(self, event: DomainEvent) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                # Lỗi quan sát không được làm hỏng nghiệp vụ đã commit.
                logger.exception("Lỗi bộ lắng nghe %s của đơn %s", event.name, event.order_id)
