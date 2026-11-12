# Proof of Intent

[![Demo video](https://img.youtube.com/vi/2_8M0kO0pIc/maxresdefault.jpg)](https://youtu.be/2_8M0kO0pIc)

[![CI](https://github.com/sainithya-2001/agent-receipts/actions/workflows/ci.yml/badge.svg)](https://github.com/sainithya-2001/agent-receipts/actions/workflows/ci.yml)

**The dispute layer for AI purchases on PayPal.** When an AI agent buys the wrong thing, Proof of Intent records what it was asked to do before the purchase, uses that record to decide who is at fault, and settles the dispute through PayPal.

The lifecycle is **prevent → record → resolve**: a guard stops a purchase that breaks the buyer's confirmed limits before any money moves; a hash-anchored receipt records what the agent was asked and chose; and when a dispute still happens, the adjudicator settles it from that receipt.

- **Live demo:** https://agent-receipts-web.onrender.com (free hosting: first load can take ~60s)
- **Demo video (under 3 min):** https://youtu.be/2_8M0kO0pIc

![Replay timeline with the deviating step highlighted](docs/screenshots/replay.png)

---

## About this project

I designed and built Proof of Intent myself. I designed the architecture and the trust model: PayPal as an independent integrity witness (the receipt hash travels as the order's `invoice_id`), adjudication that works out the facts in code before any AI is involved and fails closed when integrity can't be checked, buyer-confirmed mandates, and the pre-payment guard. I integrated PayPal Orders, Vault, Captures, Disputes and Webhooks, and built the ledger, the adjudication pipeline, the MCP server and the merchant console.

I validated it with automated tests, three external technical reviews whose findings I triaged and fixed, and live sandbox runs, including a real dispute that PayPal refunded.

---

## The problem

AI agents are starting to shop for people. When one buys the wrong size, overspends, or gets tricked by a product listing, the dispute that follows has no evidence. A normal purchase record shows *what* was bought. It doesn't show *what the agent was asked to do* or *why it chose that item*, and that is exactly what decides who is at fault.

Payment networks and merchants describe liability for agent purchases as an open problem: card-network frameworks for agent transactions leave non-fraud disputes unresolved, and merchants are left holding the loss.

## What Proof of Intent does

1. **Prevent: a guard before payment.** On the public `/shop`, the agent's choice is checked against the limits the buyer confirmed (price and currency, size, category) *before* the PayPal order is created. A violation makes no PayPal call at all: a `purchase_blocked` event goes on the ledger and the replay timeline shows a green "Blocked before payment" frame. The "Break the agent" scenarios run with the guard **off**, as an agent platform without a guard, so the downstream dispute path can be shown; each says so.
2. **Record: receipt before payment.** Before the shopping agent can pay, it must log a *receipt of intent*: the person's request, their limits (size, budget, category), every option the agent considered, its choice, and why. No receipt, no PayPal order.
3. **Tamper-evident ledger.** Every receipt and PayPal event is SHA-256 hash-chained in an append-only ledger.
4. **Anchored in PayPal.** The receipt's hash is sent as the PayPal order's `invoice_id`, so PayPal holds an independent fingerprint from the moment of payment. If anyone later edits the receipt, even rewriting the whole chain, the anchor check fails.
5. **Resolve: AI adjudication.** When the buyer opens a PayPal dispute, an adjudicator agent compares the receipt with what happened and assigns fault:

   | Verdict | Meaning | Outcome |
   |---|---|---|
   | `AGENT_ERROR` | The agent broke the mandate (wrong size, over budget) | Refund the buyer; the agent provider is identified as responsible for the mandate breach. Recovering costs from the provider is a separate process. |
   | `MERCHANT_ERROR` | Wrong item delivered, or the listing manipulated the agent (prompt injection) | Refund the buyer |
   | `BUYER_REMORSE` | The agent did exactly what was asked | Merchant submits evidence and wins |

6. **Evidence agent.** It picks the next step from the actions PayPal currently offers on the dispute (accept claim, escalate, provide evidence) and writes the response. Code appends the intent hash, the verdict event's hash and the ledger head at filing to every note sent to PayPal and records them in the `dispute_response` event, so the evidence PayPal holds is tied to the ledger too.
7. **Replay.** A timeline shows every step the agent took, with the step where things went wrong highlighted in red.

### Why the AI doesn't decide alone

The AI does not decide alone. Code first works out the facts: does the receipt verify against PayPal, which fields differ between the mandate and the purchase, and did the listing try to instruct the agent. Those facts limit which verdicts are possible. Claude then chooses among the allowed verdicts, cites a rule, and explains. If it picks a verdict the facts rule out, the facts win and the override is recorded. If the receipt fails its integrity check, the case goes to manual review with no AI call at all.

## Try it

On the live demo's merchant console, the **Break the agent** cards run seeded failures:

| Card | What happens | Expected verdict |
|---|---|---|
| Wrong size | Agent buys size 9 when 8 was asked | `AGENT_ERROR` |
| Over budget (no guard) | Agent buys $159.99 against a $120 limit on a platform without a pre-payment guard | `AGENT_ERROR` |
| Guard stops an overspend | The same overspend, guard on: blocked before PayPal is called, nothing to dispute | Green "Blocked before payment" frame, no order |
| Prompt-injected listing | A listing tells shopping agents to ignore the budget | `MERCHANT_ERROR`, injected text highlighted |
| Buyer changed their mind | Agent buys exactly what was asked | `BUYER_REMORSE` |
| Tamper with a receipt | A receipt is edited and the whole chain rehashed | Chain still "verifies", but the PayPal anchor check fails → manual review |

Each run (except the guard card, which never reaches PayPal) places a **real PayPal sandbox order** (vaulted, no login) with the receipt hash anchored. Only the dispute is simulated, so judges can try every case instantly. Real PayPal disputes are shown in the queue alongside them.

The hosted demo **never files anything with PayPal** (`FILING_ENABLED=false`) and is rate-limited to protect the API budget.

## Related work

- **Agent Receipts protocol / Obsigna:** cryptographically verifiable, hash-chained records of agent actions, built for audit and accountability.
- **Mastercard Verifiable Intent and Google AP2 (intent and cart mandates):** cryptographically verifiable authorization of what an agent may buy, checked at payment time.

Proof of Intent differs in where the evidence is used. It treats the intent record as evidence once a PayPal dispute opens, rather than only as an audit log or an authorization check. It anchors the receipt hash in the PayPal order itself (`invoice_id`), so PayPal holds an independent fingerprint and later edits to the record are detectable. It decides fault with facts derived by code (integrity, field diff, listing injection) before the model is consulted, and the model can only choose among the verdicts those facts allow. Then it files the response through PayPal's Disputes API.

## How PayPal is used

| PayPal capability | Used for |
|---|---|
| **Orders v2** | Agent purchases; receipt ID in `custom_id`, receipt hash in `invoice_id` |
| **Vault (payment tokens)** | The buyer approves once; the agent then pays with no redirect. PayPal shows it to the buyer as an automatic payment |
| **Payments (captures)** | Anchor check: reads `invoice_id` and `custom_id` from the capture named in the dispute |
| **Disputes v1** | Read disputes; accept claim, escalate, provide evidence, and settle in the sandbox |
| **Webhooks** | `CUSTOMER.DISPUTE.*` events trigger adjudication; signatures verified with PayPal |

## How AI is used

| Tool | How |
|---|---|
| **Claude API** (model set by `ANTHROPIC_MODEL`; built and tested with `claude-sonnet-5-5`) | Three agents: the **shopping agent** (tool-use loop over the catalog), the **adjudicator** (structured JSON verdict constrained by code-derived facts), and the **evidence agent** (writes the PayPal dispute response). A small structured-output call also reads the buyer's request into limits for them to confirm. Prompt caching on system prompts and tools |
| **Model Context Protocol (MCP)** | The receipts ledger is exposed as an MCP server (`log_intent`, `get_receipt`, `verify_chain`) so any agent can log intent before buying |
| **Retrieval (RAG)** | BM25 keyword retrieval over 20 dispute, evidence, liability and return-policy rules; the adjudicator cites the rule it applied |

## Architecture

```mermaid
flowchart LR
    B[Buyer request] --> S[Shopping agent]
    S -- log intent first --> L[(Receipts ledger<br/>hash-chained, MCP server)]
    S -- pay, hash as invoice_id --> O[PayPal Orders + Vault]
    W[PayPal dispute webhook] --> A[Adjudicator agent]
    L --> A
    O -. anchor check .-> A
    R[Dispute rules RAG] --> A
    A --> E[Evidence agent]
    E --> D[PayPal Disputes API]
    A --> U[Replay timeline + verdict card]
```

**Stack:** Python 3.11, FastAPI, SQLite (append-only), Anthropic SDK, MCP Python SDK, Next.js + React, AG Grid, Render.

---

## Run it locally

### 1. Prerequisites
- Python 3.11, Node 20.9–24, Git
- A [PayPal Developer](https://developer.paypal.com) account (free sandbox)
- A [Claude API key](https://console.anthropic.com)

### 2. PayPal sandbox setup
1. developer.paypal.com → **Apps & Credentials** → **Sandbox** → **Create App** (type Merchant).
2. On the app page, under **Features**, enable **Customer disputes** and **Save payment methods (Vault)**. Save.
3. Copy the **Client ID** and **Secret**.
4. **Testing Tools → Sandbox Accounts:** note the **Personal** (buyer) account's email and password.

### 3. Backend
```bash
git clone https://github.com/sainithya-2001/agent-receipts
cd agent-receipts
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
```
Fill in `.env`: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (for example `claude-sonnet-5-5`).

**Optional, for no-login purchases (Vault):**
```bash
python -m scripts.vault setup          # prints an approval link: log in as the sandbox buyer and approve
python -m scripts.vault finish <setup_token>
```
Put the printed vault ID in `.env` as `PAYPAL_VAULT_ID`. Without it, each purchase redirects the buyer to approve.

Start the API:
```bash
python -m uvicorn backend.app.main:app --port 8001
```

### 4. Frontend
```bash
cd frontend
npm install
npm run dev               # http://localhost:3000
```
The browser calls `/api/*`, which Next forwards to `BACKEND_URL` (default `http://localhost:8001`).

### 5. Tests
```bash
python -m pytest -q
```
PayPal and Claude are mocked in tests, so no credentials are needed. The same tests, the frontend typecheck and `next build` run on every push in GitHub Actions (`.github/workflows/ci.yml`).

### Useful commands
```bash
python -m scripts.shop 'running shoes, size 8, under $120'   # run the shopping agent end to end
python -m scripts.disputes scenario all                       # run the four demo scenarios
python -m scripts.disputes list                               # real sandbox disputes
python -m scripts.ledger verify                               # recheck every hash and link
python -m backend.ledger.mcp_server                           # ledger MCP server (stdio)
```
A Postman collection is in [`postman/`](postman/agent-receipts.postman_collection.json).

### Opening a real dispute in the sandbox
PayPal only lets the buyer open a dispute. Log in to `sandbox.paypal.com` as the sandbox **Personal** account → **Activity** → the payment → **Report a problem**. It appears in the merchant queue within about a minute.

## Deploy (Render)

`render.yaml` is a Render Blueprint for two services:

| Service | What | Plan |
|---|---|---|
| `agent-receipts-api` | FastAPI backend; the ledger is SQLite on the instance's ephemeral filesystem | Free |
| `agent-receipts-web` | Next.js frontend; `/api/*` is forwarded to the API's public URL | Free |

Render dashboard → **New → Blueprint** → this repo, then fill in the secrets it asks for. After the first deploy, set `BACKEND_URL` on the web service to the API's public URL (`https://<api-service>.onrender.com`) and redeploy it: free web services can't receive private-network traffic, and the `/api/*` rewrite is fixed at build time. Leave `PAYPAL_WEBHOOK_ID` empty until the webhook is registered: developer.paypal.com → your app → **Webhooks** → add `https://<api-service>.onrender.com/paypal/webhook` with the Customer Dispute events, then set its ID on the API service.

**Free plan caveats:** services spin down after about 15 minutes idle, so the first request takes about a minute; and the ledger is not persistent, so receipts created on the hosted demo reset on every restart, spin-down or redeploy. The staged receipts in `backend/scenarios/staged/` reload at startup, so the pre-staged dispute is always there.

The hosted demo never files with PayPal (`FILING_ENABLED=false`, `DISPUTE_AUTO_FILE=false`), and `/shop`, scenario runs and dispute resolve are rate-limited (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_DAY`).

## Design decisions

- **Log first, then pay.** The agent cannot create a PayPal order without a receipt ID. The intent exists before any money moves, so it can't be written to fit the outcome.
- **Append, never edit.** PayPal order IDs, disputes, verdicts and filings are added as linked events. A hashed record is never changed, and database triggers refuse updates and deletes.
- **Anchor outside the ledger's database.** A hash chain alone can be rebuilt by anyone who controls the database. The receipt hash goes into the PayPal order as `invoice_id`, so PayPal holds a copy the ledger's owner can't rewrite. The adjudicator reads it back from the captured payment named in the dispute.
- **Judge against a snapshot.** The receipt stores the listing the agent chose (name, price, description, attributes), so a later catalog change can't rewrite what the agent saw. Receipts logged before snapshots existed fall back to the catalog, and the verdict card says so.
- **Facts first, model second.** Code works out the integrity result, the field-by-field diff and any injected listing text. Those facts limit the possible verdicts. Claude only chooses among them, cites a rule and explains.
- **Injection must be shown to matter.** A listing that tries to instruct the agent only shifts fault to the merchant if the agent actually saw that listing and the violated field is the one the text told it to override. Otherwise the case goes to a person.
- **Fail closed.** If a real dispute's receipt can't be checked against PayPal (no capture ID, failed lookup), there is no model call and the case goes to manual review. Only an order that exists but predates anchoring counts as "unanchored".
- **Retries.** Stable request IDs make retries idempotent under PayPal's idempotency handling: order creation uses `order-<receipt_id>`, capture `capture-<order_id>`, and dispute filings an ID derived from the dispute and action. Webhook recovery is lightweight: a stuck event is reprocessed when PayPal redelivers it (after 5 minutes in `processing`); there is no background sweeper. Within one process, each dispute is resolved by one caller at a time.
- **Prevent first, and say when it is off.** The guard checks the catalog price that checkout would charge, never a price the agent reports. Scenario runs record `guard: off` on the order event so a demo failure is never mistaken for a guarded platform.
- **Mandates are bookkeeping, not evidence.** They are stored in the database outside the hash chain; the receipt records the confirmed mandate itself. A run that fails before any PayPal order can exist gives the mandate back so the buyer can retry; once ordering began it stays used. A guard-blocked attempt intentionally uses up its mandate too: retrying means confirming again.
- **Re-judging after filing is a deliberate act.** A verdict can be superseded, but not once a response was filed unless forced with a reason, and a forced one is marked on the ledger.
- **Filing is opt-in.** Nothing is sent to PayPal unless `FILING_ENABLED=true`. The hosted demo keeps it off.
- **Keep it small.** Keyword (BM25) retrieval over 20 rules instead of an embedding model, SQLite instead of a database server, and plain Anthropic tool-use loops instead of an agent framework.

## Current scope and next steps

- **Sandbox only.** All payments use PayPal's sandbox. *Next:* live credentials with a merchant partner.
- **Disputes are opened by the buyer.** PayPal doesn't let a merchant app open disputes, so live disputes start in PayPal's buyer interface. The "Break the agent" cards use scripted agent decisions with real sandbox orders, so each failure is reproducible.
- **No user accounts yet.** `/mandate/parse` returns a random confirm token (only its hash is stored), and confirming or spending a mandate requires it, so a leaked mandate id alone is useless. The token is a per-mandate capability, not a buyer identity or account control: whoever holds it can use that mandate, and nothing says who the buyer is. *Next:* real accounts, with the mandate bound to a signed-in buyer.
- **Tamper-evident, not signed.** A receipt shows the record hasn't changed since purchase, checked against PayPal's stored copy. The server records the buyer's confirmation step, but a receipt isn't a buyer signature and doesn't verify who typed the request. *Next:* accept signed authorizations such as AP2 mandates or Mastercard Verifiable Intent.
- **Injection attribution is evidence-based**, not a formal proof: what the agent saw and what the injected text told it to override. Unclear cases go to manual review.
- **Demo rules and catalog.** The 20 dispute rules are written for this demo, not PayPal policy; one sandbox merchant, 20 products.
- **Free hosting.** The first load can take about a minute, and demo data resets when the server restarts.

## Development

Built with AI-assisted development (Claude Code); architecture, trust design and review decisions are my own, validated with automated tests, external reviews and live sandbox runs.

## License

[MIT](LICENSE)

Originally built for the PayPal AI Hackathon (2026).
