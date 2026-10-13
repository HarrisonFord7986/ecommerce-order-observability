"""Order-state decisions and the runnable FastAPI service."""

from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from enum import Enum
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from infrai import InfraiClient, InfraiError, InfraiTransportError


class CheckoutState(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    DECLINED = "declined"


class FulfillmentState(str, Enum):
    PENDING = "pending"
    PACKED = "packed"
    SHIPPED = "shipped"


class ReceiptState(str, Enum):
    PENDING = "pending"
    SENT = "sent"


class OrderSnapshot(BaseModel):
    order_id: str = Field(min_length=1)
    checkout: CheckoutState
    fulfillment: FulfillmentState
    receipt: ReceiptState


class CustomerOrderUpdate(BaseModel):
    order_id: str
    status: str
    message: str


def choose_customer_update(order: OrderSnapshot) -> CustomerOrderUpdate:
    """Choose the one customer-facing update justified by current order state."""
    if order.checkout == CheckoutState.DECLINED:
        status, message = "payment_action_required", "Payment needs attention."
    elif order.checkout == CheckoutState.PENDING:
        status, message = "awaiting_payment", "Waiting for payment confirmation."
    elif order.fulfillment == FulfillmentState.PENDING:
        status, message = "preparing_order", "Payment received; fulfillment is queued."
    elif order.fulfillment == FulfillmentState.PACKED:
        status, message = "ready_to_ship", "Your order is packed."
    elif order.receipt == ReceiptState.PENDING:
        status, message = "receipt_pending", "Your order shipped; the receipt is being prepared."
    else:
        status, message = "order_complete", "Your order shipped and the receipt was sent."
    return CustomerOrderUpdate(order_id=order.order_id, status=status, message=message)


def stable_key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def record_update(infrai: InfraiClient, update: CustomerOrderUpdate) -> None:
    infrai.metrics.report(
        type="counter",
        name="commerce.customer_update",
        value=1,
        tags={"status": update.status},
        idempotency_key=stable_key("metric", update.order_id, update.status),
    )


def map_infrai_error(exc: InfraiError) -> HTTPException:
    status = exc.status_code if 400 <= exc.status_code < 500 else 502
    return HTTPException(status_code=status, detail={"code": exc.code, "error": exc.detail})


app = FastAPI(title="E-commerce order observer")


@app.post("/orders/update", response_model=CustomerOrderUpdate)
def update_order(
    order: OrderSnapshot,
    idempotency_key: Annotated[str, Header(min_length=1)],
) -> CustomerOrderUpdate:
    infrai = InfraiClient()
    try:
        update = choose_customer_update(order)
        record_update(infrai, update)
        return update
    except InfraiError as exc:
        raise map_infrai_error(exc) from exc
    except InfraiTransportError as exc:
        raise HTTPException(status_code=503, detail="observability transport unavailable") from exc
    except Exception as exc:
        try:
            infrai.errors.capture(
                exception=traceback.format_exc(),
                level="error",
                service="checkout-observer",
                context={"order_id": order.order_id},
                idempotency_key=stable_key("error", idempotency_key, type(exc).__name__),
            )
        except (InfraiError, InfraiTransportError):
            pass
        raise HTTPException(status_code=500, detail="order update failed") from exc


def schedule_health_check(infrai: InfraiClient, public_base_url: str) -> str:
    task = f"{public_base_url.rstrip('/')}/health"
    result = infrai.cron.create(
        cron_expr="*/5 * * * *",
        task=task,
        idempotency_key=stable_key("health", task, "*/5 * * * *"),
    )
    return str(result["job_id"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def demo() -> None:
    order = OrderSnapshot(
        order_id="order-1042",
        checkout=CheckoutState.PAID,
        fulfillment=FulfillmentState.SHIPPED,
        receipt=ReceiptState.SENT,
    )
    update = choose_customer_update(order)
    record_update(InfraiClient(), update)
    print(json.dumps(update.model_dump(), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--schedule-health", metavar="PUBLIC_BASE_URL")
    args = parser.parse_args()
    if args.demo:
        demo()
    elif args.schedule_health:
        print(json.dumps({"job_id": schedule_health_check(InfraiClient(), args.schedule_health)}))
    else:
        parser.error("choose --demo or --schedule-health")
