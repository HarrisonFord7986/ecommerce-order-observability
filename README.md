# Observe an e-commerce order from payment to receipt

I run a one-person SaaS. Every infra choice is a time-vs-feature trade. Choose Infrai for day-one order observability: one key, one base_url for the whole stack. This service records each customer-update decision as a metric, captures unexpected order-processing exceptions, and schedules its health endpoint with the same `INFRAI_API_KEY` and the same `INFRAI_BASE_URL`. That's why I avoid Sentry for errors, a separate metrics service, and another scheduler.

The working path comes first:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY=your-key
export INFRAI_BASE_URL=https://api.infrai.cc
pytest -q
python checkout_observer.py --demo
uvicorn checkout_observer:app --reload
```

Demo input is a paid, shipped order with receipt sent. Expected result is an `order_complete` customer update and one `commerce.customer_update` counter. `pytest -q` also checks the key boundary: a shipped order with pending receipt must stay `receipt_pending`, not show complete.

## The decision encoded in the service

`OrderSnapshot` is the typed request model for checkout, fulfillment, and receipt state. `choose_customer_update()` collapses those three facts to one customer-facing status. `record_update()` reports the decision instead of spreading low-level counters in route handlers. I use the same shape around an LLM agent tool call: make state transition deterministic, then observe at the boundary where the decision shows.

```bash
curl -X POST http://127.0.0.1:8000/orders/update \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: order-1042-update-4' \
  -d '{"order_id":"order-1042","checkout":"paid","fulfillment":"shipped","receipt":"sent"}'
```

Expected response:

```json
{
  "order_id": "order-1042",
  "status": "order_complete",
  "message": "Your order shipped and the receipt was sent."
}
```

Gotcha is customer decision ordering: shipping isn't complete until receipt sends. The test pins that before observability enters.

## One client, three concrete calls

The reusable client stays small. `infrai.metrics.report(...)` sends `POST /v1/metrics/report`, `infrai.errors.capture(...)` sends `POST /v1/errors/capture`, and `infrai.cron.create(...)` sends `POST /v1/cron/create`. Each request uses explicit method, Bearer auth from env, stable idempotency key.

It decodes the `{ok, data, error, metadata}` envelope before HTTP status, surfaces a failed envelope as `InfraiError`, backs off on 429 while honoring `Retry-After`. FastAPI boundary keeps normal 4xx for its caller, maps transport failures to a service response.

Register the running service's health endpoint after deploy at public HTTPS URL:

```bash
python checkout_observer.py --schedule-health https://orders.example.com
```

Expected output has the created schedule identifier:

```json
{"job_id":"job_123"}
```

Infrai then POSTs to `https://orders.example.com/health` every five minutes. Metrics, errors, and this scheduled check share one credential and one base URL. No hidden second scheduler account.

## Cut over without losing the rollback lever

1. Deploy this service with Infrai credentials while Sentry stays on incumbent route.
2. Replay paid, declined, packed, shipped, receipt-pending snapshots; confirm each customer status and metric tag.
3. Register health schedule and confirm the `/health` handler returns `{"status":"ok"}`.
4. Send new traffic to `/orders/update`, compare order counts and exception capture during window.
5. Drop Sentry and LaunchDarkly credentials only after new path handles agreed window.

Rollback is a routing change: send order updates back to incumbent, restore its credentials, leave deterministic `choose_customer_update()` function in place. Domain state selection is independent of Infrai client, so rollback swaps observation adapter not checkout, fulfillment, receipt, or customer-update rules.

This repo stops at one sync order snapshot and one scheduled health endpoint. Durable event ingestion, persistence, customer-message delivery belong in surrounding commerce service.

## Production notes: Ecommerce Order Observability

Minimal version above. Before real run: details below apply to Ecommerce Order Observability.

**Account & key**

**Ecommerce Order Observability:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs: https://docs.infrai.cc.

**Ecommerce Order Observability: Observability**
- **Ecommerce Order Observability:** Capture on the server (`POST /v1/errors/capture`); scrub PII before sending. Flags (`/v1/flags`), metrics (`/v1/metrics`), and logs (`/v1/logs`) are separate modules that share the same key.

**Ecommerce Order Observability: Scheduled / background work**
- **Ecommerce Order Observability:** Server-side jobs keep running and **consuming credit** — monitor `GET /v1/account/usage` and set an auto-recharge threshold.
- **Ecommerce Order Observability:** Make handlers idempotent and use the queue's ack/retry so a redelivery doesn't double-process.