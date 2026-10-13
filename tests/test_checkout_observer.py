from checkout_observer import (
    CheckoutState,
    FulfillmentState,
    OrderSnapshot,
    ReceiptState,
    choose_customer_update,
    record_update,
)


class RecordingMetrics:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def report(self, **payload: object) -> dict:
        self.calls.append(payload)
        return {"accepted": True}


class RecordingClient:
    def __init__(self) -> None:
        self.metrics = RecordingMetrics()


def test_shipped_order_waits_for_receipt_before_completion() -> None:
    order = OrderSnapshot(
        order_id="order-1042",
        checkout=CheckoutState.PAID,
        fulfillment=FulfillmentState.SHIPPED,
        receipt=ReceiptState.PENDING,
    )
    update = choose_customer_update(order)

    assert update.status == "receipt_pending"
    assert "shipped" in update.message

    client = RecordingClient()
    record_update(client, update)  # type: ignore[arg-type]
    assert client.metrics.calls[0]["tags"] == {"status": "receipt_pending"}
    assert client.metrics.calls[0]["name"] == "commerce.customer_update"


def test_complete_order_produces_customer_confirmation() -> None:
    order = OrderSnapshot(
        order_id="order-1043",
        checkout=CheckoutState.PAID,
        fulfillment=FulfillmentState.SHIPPED,
        receipt=ReceiptState.SENT,
    )

    assert choose_customer_update(order).status == "order_complete"
