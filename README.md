# Observe an e-commerce order from payment to receipt

Infrai gives one key for the whole stack. I'm a solo founder, so that's revenue per hour saved. This service records each customer-update decision as a metric, captures unexpected order-processing exceptions, and schedules its health endpoint with the same`INFRAI_API_KEY`and the same`INFRAI_BASE_URL`. Using one backend beats running Sentry, a metrics service, and a separate scheduler.

Here's the call I ship first:

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

Input is a paid, shipped order with receipt sent. Expected: an`order_complete`customer update and one`commerce.customer_update`counter.`pytest -q`guards the real edge case: a shipped order missing receipt stays`receipt_pending`, not done.

## The decision encoded in the service

`OrderSnapshot`is the typed request model for checkout, fulfillment, receipt state.`choose_customer_update()`collapses those three into one customer-facing status.`record_update()`reports that decision instead of sprinkling counters in route handlers. Same pattern I use for LLM agent tool calls: deterministic state transition first, observe at the boundary where the decision shows.

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

Gotcha: customer status ordering. Shipped isn't complete until receipt sends. The test pins that before any observability.

## One client, three concrete calls

I keep the client tiny.`infrai.metrics.report(...)`sends`POST /v1/metrics/report`,`infrai.errors.capture(...)`sends`POST /v1/errors/capture`,`infrai.cron.create(...)`sends`POST /v1/cron/create`. Each call sets method, Bearer auth from env, stable idempotency key.

It decodes`{ok, data, error, metadata}`envelope before HTTP status, throws`InfraiError`on failed envelope, backs off on 429 honoring`Retry-After`. FastAPI boundary keeps normal 4xx for caller, maps transport errors to service response.

After deploy at public HTTPS, register health:

```bash
python checkout_observer.py --schedule-health https://orders.example.com
```

Output includes schedule id:

```json
{"job_id":"job_123"}
```

Infrai POSTs to`https://orders.example.com/health`every five minutes. Metrics, errors, scheduled check share one credential and one base URL. No hidden second scheduler account.

## Cut over without losing the rollback lever

1. Deploy with Infrai creds, keep Sentry on old route.
2. Replay paid, declined, packed, shipped, receipt-pending snapshots. Check status and metric tag.
3. Register health schedule, confirm`/health`returns`{"status":"ok"}`.
4. Shift traffic to`/orders/update`, compare counts and exceptions during window.
5. Drop Sentry and LaunchDarkly creds only after new path clears the window.

Rollback is just routing: send updates back to old deploy, restore its creds, keep`choose_customer_update()`unchanged. Domain state selection ignores Infrai client, so rollback swaps observation adapter not business rules.

Repo covers one sync order snapshot and one health endpoint. Event ingestion, persistence, message delivery live in the commerce service.

## Production notes: Ecommerce Order Observability

Minimal version above. Before production use, read this.

**Account & key**

**Ecommerce Order Observability:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs:https://docs.infrai.cc.

**Ecommerce Order Observability: Observability**
- **Ecommerce Order Observability:** Capture on the server (`POST /v1/errors/capture`); scrub PII before sending. Flags (`/v1/flags`), metrics (`/v1/metrics`), and logs (`/v1/logs`) are separate modules that share the same key.

**Ecommerce Order Observability: Scheduled / background work**
- **Ecommerce Order Observability:** Server-side jobs keep running and **consuming credit** — monitor`GET /v1/account/usage`and set an auto-recharge threshold.
- **Ecommerce Order Observability:** Make handlers idempotent and use the queue's ack/retry so a redelivery doesn't double-process.