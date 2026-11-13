"""Nghiệp vụ thanh toán: tạo giao dịch, xử lý phản hồi của cổng, chốt đơn hoặc hoàn kho.

Nguyên tắc:
- KHÔNG gọi cổng thanh toán bên trong transaction DB. Mỗi bước là một UoW ngắn; lời gọi
  ra ngoài nằm giữa các UoW, nên cổng chậm/treo không giữ khóa hay kết nối DB.
- Mọi sự kiện tiền đều được ghi vào `transactions` (chỉ ghi thêm). Ràng buộc duy nhất
  trên sự kiện làm webhook gửi lặp trở thành thao tác không có tác dụng.
- Tiền về mà không chốt được đơn (đơn đã hết hạn/bị hủy/đã được trả bằng giao dịch
  khác, hoặc sai số tiền) thì KHÔNG xuất vé; ghi yêu cầu hoàn tiền rồi gọi cổng hoàn.
"""
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.services.errors import Forbidden, HoldExpired, InvalidState, NotFound, PaymentGatewayError
from app.services.order_data import OrderData
from app.services.order_service import release_hold
from app.services.payment_gateway import PaymentEvent, PaymentGateway, RefundResult
from app.services.unit_of_work import UnitOfWork


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class PaymentData:
    order_id: int
    payment_id: str
    payment_url: str
    amount: Decimal
    provider: str


@dataclass(frozen=True)
class WebhookOutcome:
    order: OrderData
    result: str  # paid | failed | refund_pending | duplicate
    refund_amount: Decimal | None = None


class PaymentService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], gateway: PaymentGateway,
                 make_ticket_code: Callable[[int], str], clock=_utcnow, execute=None):
        self.uow_factory = uow_factory
        self.gateway = gateway
        self.make_ticket_code = make_ticket_code
        self.clock = clock
        self.execute = execute or (lambda operation: operation())

    # --- Bắt đầu thanh toán ------------------------------------------------

    def start_payment(self, user_id: int, order_id: int) -> PaymentData:
        """Tạo giao dịch ở cổng thanh toán cho một đơn đang giữ chỗ và còn hạn.

        Gọi lại nhiều lần (bấm "Thanh toán" hai lần) trả về cùng một giao dịch đang chờ.
        """
        order, pending = self.execute(lambda: self._payable_order(user_id, order_id))
        if pending is not None:
            return pending

        intent = self.gateway.create_payment(
            order.id, order.total_amount, f"Thanh toan don ve #{order.id}"
        )
        payment = PaymentData(order.id, intent.payment_id, intent.payment_url,
                              order.total_amount, self.gateway.name)
        raw_log = json.dumps({"event": "payment_created", "payment_url": intent.payment_url,
                              "gateway": intent.raw}, ensure_ascii=False, default=str)
        self.execute(lambda: self._record(payment.order_id, payment.payment_id, "payment",
                                          "pending", payment.amount, raw_log))
        return payment

    def _payable_order(self, user_id, order_id):
        with self.uow_factory() as uow:
            order = uow.orders.get_by_id(order_id)
            if order is None:
                raise NotFound(f"order {order_id}")
            if order.user_id != user_id:
                raise Forbidden("not your order")
            if order.status != "held":
                raise InvalidState(f"order already {order.status}")
            if order.expires_at <= self.clock():
                raise HoldExpired(order.expires_at.isoformat())

            pending = uow.transactions.latest_pending_payment(order_id)
            if pending is not None:
                pending = PaymentData(order_id, pending.payment_id,
                                      json.loads(pending.raw_log)["payment_url"],
                                      pending.amount, pending.provider)
            return OrderData.from_order(order), pending

    def _record(self, order_id, payment_id, type, status, amount, raw_log) -> bool:
        with self.uow_factory() as uow:
            recorded = uow.transactions.record(
                order_id=order_id, payment_id=payment_id, provider=self.gateway.name,
                type=type, status=status, amount=amount, raw_log=raw_log,
            )
            uow.commit()
        return recorded

    # --- Phản hồi từ cổng thanh toán ---------------------------------------

    def handle_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookOutcome:
        """Xử lý phản hồi của bên thứ ba: chốt đơn và xuất vé, hoặc hoàn kho."""
        event = self.gateway.parse_webhook(headers, body)  # ném lỗi nếu sai chữ ký
        outcome = self.execute(lambda: self._apply(event))
        if outcome.result == "refund_pending":
            self._refund(outcome.order.id, event.payment_id, outcome.refund_amount)
        return outcome

    def _apply(self, event: PaymentEvent) -> WebhookOutcome:
        provider = self.gateway.name
        with self.uow_factory() as uow:
            pending = uow.transactions.get_event(provider, event.payment_id, "payment", "pending")
            if pending is None:
                raise NotFound(f"payment {event.payment_id}")
            order_id = pending.order_id

            recorded = uow.transactions.record(
                order_id=order_id, payment_id=event.payment_id, provider=provider,
                type="payment", status="succeeded" if event.succeeded else "failed",
                amount=event.amount, raw_log=event.raw,
            )
            if not recorded:  # webhook gửi lặp: đã xử lý ở lần trước
                return WebhookOutcome(OrderData.from_order(uow.orders.get_by_id(order_id)), "duplicate")

            refund_amount = None
            if not event.succeeded:
                result = "failed"
                if uow.orders.mark_failed(order_id):
                    release_hold(uow, uow.orders.get_by_id(order_id))
            elif event.amount == pending.amount and uow.orders.mark_paid(order_id, self.clock()):
                result = "paid"
                order = uow.orders.get_by_id(order_id)
                tickets = uow.tickets.sell(order_id, self.make_ticket_code)
                if len(tickets) != order.quantity:
                    raise RuntimeError(f"order {order_id}: expected {order.quantity} held tickets, "
                                       f"found {len(tickets)}")
            else:
                # Tiền đã về nhưng không chốt được đơn → không xuất vé, phải hoàn tiền.
                result, refund_amount = "refund_pending", event.amount
                uow.transactions.record(
                    order_id=order_id, payment_id=event.payment_id, provider=provider,
                    type="refund", status="pending", amount=event.amount,
                    raw_log=json.dumps({"event": "refund_requested",
                                        "reason": "payment could not be applied to the order"}),
                )

            outcome = WebhookOutcome(OrderData.from_order(uow.orders.get_by_id(order_id)),
                                     result, refund_amount)
            uow.commit()
        return outcome

    def _refund(self, order_id: int, payment_id: str, amount: Decimal) -> None:
        """Gọi cổng hoàn tiền rồi ghi kết quả. Dòng `refund/pending` không có kết quả
        tương ứng (vd. tiến trình chết giữa chừng) là việc để đối soát xử lý lại."""
        try:
            refund = self.gateway.refund(payment_id, amount, "order could not be confirmed")
        except PaymentGatewayError as exc:
            refund = RefundResult(False, {"error": str(exc)})
        raw_log = json.dumps({"event": "refund_result", "gateway": refund.raw},
                             ensure_ascii=False, default=str)
        self.execute(lambda: self._record(order_id, payment_id, "refund",
                                          "succeeded" if refund.succeeded else "failed",
                                          amount, raw_log))
