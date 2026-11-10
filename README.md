# agent-receipts
The black box for AI shoppers: tamper-evident purchase receipts and AI dispute resolution for agentic commerce, built on PayPal.

## Receipts ledger

Before the shopping agent buys anything, it logs a receipt of intent: what the person asked for,
the options it weighed, its choice and why. Each record is SHA-256 hash-chained to the one before,
so any edit, deletion or reordering shows up in `verify_chain`. PayPal order and capture IDs are
appended as linked events; the intent itself is never changed. Checkout refuses to create a PayPal
order without a receipt, or if the chain fails verification.

```
python -m scripts.ledger verify               # recheck every hash and link
python -m scripts.ledger show rcpt_...        # a receipt and its PayPal events
python -m backend.ledger.mcp_server           # MCP server (stdio): log_intent, get_receipt, verify_chain
```

API: `GET /receipts/{receipt_id}`, `GET /ledger/verify`. Receipts saved in
`backend/scenarios/staged/` are loaded under their original IDs when the API or MCP server starts.

## Frontend

Next.js app in `frontend/`. The browser calls `/api/*`, which Next forwards to the FastAPI backend
(`BACKEND_URL`, default `http://localhost:8001`), so no CORS setup is needed.

- `/`: merchant console. AG Grid dispute queue (ledger verdicts merged with PayPal's live dispute
  list; disputes on payments with no receipt in the ledger are left out) and the "break the agent"
  controls. Each scenario run logs its receipt and buys the item for real (sandbox, vaulted, receipt
  hash anchored as invoice_id, needs `PAYPAL_VAULT_ID`); only the dispute is simulated, and it is never
  filed. The fifth card, "Tamper with a receipt", rewrites a fresh scenario receipt and rehashes the
  chain: `verify_chain` still passes, the PayPal anchor check fails, and the verdict goes to manual review.
- `/receipts/{id}?dispute={dispute_id}`: the replay timeline (mandate, each agent step, the signed
  receipt, PayPal and dispute events, with the deviating step in red), then the buyer and merchant
  side by side, with the verdict card and the next PayPal step.

```
# terminal 1: backend (from the repo root, with .env filled in from .env.example)
pip install -r requirements.txt
python -m uvicorn backend.app.main:app --port 8001

# terminal 2: frontend (Node 20+)
cd frontend
npm install
npm run dev          # http://localhost:3000
```

API used by the frontend: `GET /disputes`, `GET /catalog`, `GET /receipts/{id}`, `GET /ledger/verify`,
`GET /scenarios`, `POST /scenarios/{name}/run`, `POST /disputes/{id}/resolve?file=`.

## Deploy (Render)

`render.yaml` is a Render Blueprint for two services:

| Service | What | Plan |
|---|---|---|
| `agent-receipts-api` | FastAPI backend. The ledger is SQLite on a 1 GB persistent disk at `/var/data`, so it survives deploys and restarts. | Starter (a disk needs a paid instance) |
| `agent-receipts-web` | Next.js frontend; `/api/*` is forwarded to the API over Render's private network. | Starter (no spin-down) |

Create it from the Render dashboard: **New → Blueprint**, pick this repo, and fill in the secrets
it asks for (`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_VAULT_ID`, `ANTHROPIC_API_KEY`,
`ANTHROPIC_MODEL`; leave `PAYPAL_WEBHOOK_ID` empty until the webhook is registered).

What the hosted demo does and doesn't do:
- **Never files with PayPal.** `FILING_ENABLED=false` and `DISPUTE_AUTO_FILE=false`: the merchant
  pane shows "Would file: <action>", and the webhook only adjudicates.
- **Rate-limited.** Scenario runs, `/shop` and dispute resolve call Claude (and the first two place
  sandbox orders), so each visitor gets `RATE_LIMIT_PER_MINUTE` runs per minute and everyone shares
  `RATE_LIMIT_PER_DAY` runs per UTC day. Over the limit, the API answers 429 and the UI links to a
  completed case instead.
- **Staged receipts** in `backend/scenarios/staged/` load at every startup (skipped if present), so
  the pre-staged dispute's receipt is there even on a fresh disk.

PayPal webhook: in developer.paypal.com → Apps & Credentials → Sandbox → your app → Webhooks, add
`https://<api-service>.onrender.com/paypal/webhook` with the Customer Dispute events, then set its
Webhook ID as `PAYPAL_WEBHOOK_ID` on the API service.

Versions: Python 3.11.9 (`.python-version`), Node 20.9–24 (`frontend/package.json` engines; Render uses 24).
