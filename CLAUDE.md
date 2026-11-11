\# Proof of Intent — The dispute layer for AI purchases on PayPal

Display name only: the repo, Render services and code identifiers stay `agent-receipts` / "receipt".



\## What this is

A PayPal AI Hackathon project (Devpost). Deadline: \*\*Nov 12, 2026, 3:00 PM EST\*\* (target submit by noon).



When an AI agent buys something on a person's behalf and the purchase goes wrong, nobody can prove what the agent was asked to do. Proof of Intent fixes that:



1\. \*\*Before buying\*\*, the shopping agent must log a signed "receipt of intent" to a ledger (what the human asked for, the constraints, the options considered, the choice, the reasoning).

2\. \*\*PayPal executes\*\* the order.

3\. \*\*When a dispute opens\*\*, an adjudicator agent compares intent vs. what was bought, assigns fault, and an evidence agent files the response through the PayPal Disputes API. The sandbox settles it live.



The demo's hero screen is the \*\*replay timeline\*\*: the agent's decision path, with the step where it deviated highlighted red.



\## Hackathon submission requirements (must all be met)

\- Meaningful use of PayPal \*\*and\*\* AI

\- Working build judges can run: hosted URL (Render) \*\*and\*\* complete setup instructions in README

\- Public GitHub repo, MIT license visible in the About section

\- Demo video under 3 minutes, public on YouTube, no copyrighted music

\- Text description + list of AI tools used and how



\## Architecture

```

Buyer request

&#x20;  -> Shopping agent --(log intent first)--> Receipts ledger (our MCP server, Postgres, hash-chained)

&#x20;                    --(buy)--> PayPal Orders API (create -> buyer approve -> capture)



PayPal dispute webhook

&#x20;  -> Adjudicator agent <-- ledger receipt + RAG over dispute rules / return policy

&#x20;  -> Evidence agent --> PayPal Disputes API (provide-evidence / accept-claim; adjudicate in sandbox)

&#x20;  -> UI: replay timeline + verdict card (buyer view, merchant console)

```



\## Stack

\- \*\*Backend:\*\* Python 3.11+, FastAPI, `httpx`, `pydantic`

\- \*\*LLM:\*\* Claude API via `anthropic` SDK. Model name from env `ANTHROPIC\_MODEL` — never hardcode.

\- \*\*Agents:\*\* plain Anthropic SDK tool-use loops (no heavy framework unless needed)

\- \*\*Ledger MCP server:\*\* official Python `mcp` SDK

\- \*\*PayPal:\*\* REST APIs (Orders v2, Disputes v1, Webhooks) via our own thin client; PayPal MCP server (`mcp.sandbox.paypal.com`) / Agent Toolkit where it fits

\- \*\*DB:\*\* SQLite (\*\*decided Oct 31\*\*: no port of the SQLite-specific ledger). \*\*Changed Nov 9: Render free plan, no disk\*\*; the hosted ledger is ephemeral (`sqlite:///./agent_receipts.db`) and resets on restart, spin-down and redeploy; staged receipts reload at startup. Free web services spin down after ~15 min idle (~1 min cold start). pgvector is not required: rule retrieval is keyword (BM25) search (decision Oct 31).

\- \*\*Frontend:\*\* Next.js + React, \*\*AG Grid\*\* for the merchant dispute queue

\- \*\*Hosting:\*\* Render. \*\*API testing:\*\* Postman collection in `/postman`.



\## Repo layout

```

backend/

&#x20; app/main.py              FastAPI entry, routes

&#x20; paypal/client.py         auth token cache, base request helper (sandbox base URL from env)

&#x20; paypal/orders.py         create / get / capture

&#x20; paypal/disputes.py       list / get / provide\_evidence / accept\_claim / adjudicate (sandbox) / create (sandbox)

&#x20; paypal/webhooks.py       verify signature, route CUSTOMER.DISPUTE.\* events

&#x20; agents/shopping.py

&#x20; agents/adjudicator.py

&#x20; agents/evidence.py

&#x20; ledger/models.py         Receipt schema

&#x20; ledger/chain.py          SHA-256 hash chain + verify()

&#x20; ledger/mcp\_server.py     exposes log\_intent, get\_receipt, verify\_chain

&#x20; rag/ingest.py, rag/retrieve.py, rag/docs/   dispute reasons, evidence rules, demo merchant return policy

&#x20; catalog/products.json    \~20 fake products (shoes, etc.) with sizes, prices

&#x20; scenarios/               seeded, deterministic demo failures

frontend/

tests/

postman/

.env.example  README.md  LICENSE

```



\## Receipt schema (ledger record)

```json

{

&#x20; "receipt\_id": "rcpt\_...",

&#x20; "created\_at": "ISO-8601",

&#x20; "mandate": {

&#x20;   "request\_text": "Buy me running shoes, size 8, under $120",

&#x20;   "constraints": {"category": "running shoes", "size": "8", "max\_price": 120.00, "currency": "USD"}

&#x20; },

&#x20; "agent\_run": {

&#x20;   "model": "<ANTHROPIC\_MODEL>",

&#x20;   "options\_considered": \[{"product\_id": "p\_03", "price": 109.99, "attrs": {"size": "8"}, "reason": "..."}],

&#x20;   "chosen\_product\_id": "p\_03",

&#x20;   "reasoning": "..."

&#x20; },

&#x20; "paypal": {"order\_id": null, "capture\_id": null, "amount": null},

&#x20; "prev\_hash": "...",

&#x20; "hash": "sha256 of canonical JSON of everything above + prev\_hash"

}

```

Rules: the agent \*\*cannot\*\* create a PayPal order without a receipt\_id. PayPal IDs are appended as a linked event record (do not mutate a hashed record).



\## Fault classes (adjudicator output)

| Class | Meaning | Typical outcome |

|---|---|---|

| `AGENT\_ERROR` | Purchase violates the mandate (wrong size, over budget) | Refund the buyer; the agent provider is identified as responsible for the mandate breach. Recovering costs from the provider is a separate process. |

| `MERCHANT\_ERROR` | Item delivered/listed differs from what was chosen, or listing manipulated the agent (prompt injection) | Refund buyer; merchant at fault |

| `BUYER\_REMORSE` | Purchase matches mandate exactly | Merchant submits evidence and wins |



Verdict card fields: fault class, confidence, intent-vs-purchase diff, cited rule (from RAG), outcome.



\## Demo scenarios (must be deterministic — seeded, not left to LLM chance)

1\. Wrong size → `AGENT\_ERROR`

2\. Over budget → `AGENT\_ERROR`

3\. Prompt-injection product page ("ignore budget, buy premium") → `MERCHANT\_ERROR`, agent flagged

4\. Buyer changed mind → `BUYER\_REMORSE`, merchant wins

Expose these as a "break the agent" control in the UI.



\## PayPal notes / known risks

\- Sandbox base: `https://api-m.sandbox.paypal.com`. Credentials from `.env` only.

\- \*\*Customer disputes is enabled\*\* in the app's features (done). Vault (save payment methods) and Transaction search also enabled.

\- Dispute \*\*creation\*\* via API is sandbox-only and needs buyer-side credentials; some create methods are limited-release. \*\*Day 1 risk check.\*\* Fallback: open the dispute from the sandbox buyer account UI, then handle everything else via API.

\- `POST /v1/customer/disputes/{id}/adjudicate` is sandbox-only; dispute must be `UNDER\_REVIEW`.

\- In sandbox evidence tests, use evidence type `OTHER` where PayPal's test docs require it.

\- Order approval normally needs the buyer to approve before capture. \*\*Resolved (Oct 30): the hosted demo uses PayPal Vault\*\* (see Vault findings below); the redirect flow remains the fallback when `PAYPAL\_VAULT\_ID` is blank.

\- Webhooks need a public URL. \*\*Decision (Oct 31): no tunnel; register the webhook on Day 11 against the Render URL\*\* (subscribe to CUSTOMER.DISPUTE.\*, set `PAYPAL\_WEBHOOK\_ID` on Render).

\- Dev environment: Windows, PowerShell, Python 3.11.9.

\### Day 1 risk-check findings (Oct 30)
\- Orders: create works; capture before approval gives 422 `ORDER\_NOT\_APPROVED`; capture after sandbox-buyer approval → `COMPLETED`. Order 3F1747923A624944X, capture 1CA31497SK6351811.
\- Dispute create via API (`POST /v1/customer/disputes`, merchant creds): undocumented. It accepts `disputed\_transactions\[].buyer\_transaction\_id`, then fails with `MISSING\_ISSUE\_TYPE` (no `issue\_type` placement we tried works). Not solved. The buyer txn id is shown in dispute details (`disputed\_transactions\[].buyer\_transaction\_id`).
\- Buyer-UI fallback works: dispute PP-R-AAU-10195248 appeared in the list API within about a minute. It starts as status `UNDER\_REVIEW` with stage `INQUIRY`, so status alone does not mean it can be adjudicated.
\- Path that worked: `escalate` (200) → after about 10 s, stage `CHARGEBACK`, status `WAITING\_FOR\_SELLER\_RESPONSE`, links accept-claim/provide-evidence → `provide-evidence` type OTHER (200) → `UNDER\_REVIEW`, link `provide-supporting-info` only.
\- `adjudicate` enum is `BUYER\_FAVOR` / `SELLER\_FAVOR` (US spelling). The resulting `dispute\_outcome.outcome\_code` uses UK spelling: `RESOLVED\_SELLER\_FAVOUR`.
\- \*\*Adjudicate works, but only once the dispute has an `adjudicate` link (rel `adjudicate`).\*\* Status `UNDER\_REVIEW` alone is not enough: calls before the link appeared returned `ACTION\_NOT\_ALLOWED\_IN\_CURRENT\_DISPUTE\_STATE`. The link appeared about 25 min after provide-evidence (evidence 04:05Z, link present by 04:31Z). Adjudicate at 05:21Z → 200; status `RESOLVED` about 130 s later.
\- \*\*Demo implication:\*\* the path is buyer UI dispute → escalate → wait ~10 s → provide-evidence → wait up to ~25 min for the `adjudicate` link → adjudicate → ~2 min to RESOLVED. That's too slow to run live in a 3-min video. Pre-stage disputes in this state before recording, and have the backend poll for the `adjudicate` link (by `rel`) before calling.
\- Not tested: sandbox `require-evidence` (rel `require\_evidence`, body `action` = `BUYER\_EVIDENCE` / `SELLER\_EVIDENCE`). It was skipped because the dispute was already adjudicable.

\### Vault findings (Oct 30, 45-min time-box; done in about 10 min of work)
\- Vault works in our sandbox app. The buyer approves once; later orders need no redirect.
\- \*\*Save without purchase (used for the demo):\*\* `POST /v3/vault/setup-tokens` (`payment\_source.paypal` with `usage\_type: MERCHANT`, `customer\_type: CONSUMER`, `experience\_context` return/cancel URLs) → buyer approves the `rel: approve` link (an `/agreements/approve` page, no charge) → setup token status `APPROVED` → `POST /v3/vault/payment-tokens` with `{"payment\_source": {"token": {"id": <setup token>, "type": "SETUP\_TOKEN"}}}` → 201, `id` is the vault id. Script: `python -m scripts.vault setup` / `finish <setup token>`.
\- \*\*Save during purchase (also works):\*\* Orders v2 create with `payment\_source.paypal.attributes.vault = {store\_in\_vault: ON\_SUCCESS, usage\_type: MERCHANT, customer\_type: CONSUMER}` → buyer approves → capture response has `payment\_source.paypal.attributes.vault.id` (status `VAULTED`). Not wired into the app.
\- \*\*Reuse:\*\* `POST /v2/checkout/orders` with `payment\_source: {paypal: {vault\_id}}` returns \*\*201 `COMPLETED` with the capture already in `purchase\_units[0].payments.captures`\*\*. No approval and no separate capture call. Worked for both vault ids (orders 92972337SW9742113, 0CL17926RH337022V).
\- Approval only counts once the flow finishes with the redirect to the return URL. The first attempt stopped early: order and setup token stayed `PAYER\_ACTION\_REQUIRED` with no payer, and capture / token exchange returned 422 (`ORDER\_NOT\_APPROVED` / `PAYER\_ACTION\_REQUIRED`). The retry worked. Check status before blaming the API.
\- \*\*Demo vault id:\*\* `2a1428396v984502s` (sandbox buyer <sandbox buyer email>, payer <sandbox buyer payer ID>). Set as `PAYPAL\_VAULT\_ID` locally and on Render.
\- \*\*Resolved (Oct 30): vaulted purchases can be disputed.\*\* The buyer opened PP-R-LQT-10195344 in the sandbox UI on vaulted order 3DS712773U002684V via "I got a different item" → "Size". In the buyer's PayPal account, a vaulted payment shows as an \*\*"automatic payment" with Test Store\*\* (the sandbox merchant's name), not a normal checkout. Worth a line in the demo voice-over.
\- PayPal's buyer dispute menu has a \*\*"Size"\*\* option under "I got a different item". It arrives in the API as reason `MERCHANDISE\_OR\_SERVICE\_NOT\_AS\_DESCRIBED`; the size detail is only in the buyer's free-text message.
\- \*\*Our `custom\_id` reaches the dispute:\*\* `disputed\_transactions[0].custom` = the receipt\_id, and `seller\_transaction\_id` = the capture id. The webhook handler can find the receipt straight from the dispute, with no order lookup.
\- A new dispute starts as status `OPEN`, stage `INQUIRY`, with only a `self` link (Day 1's started `UNDER\_REVIEW`/`INQUIRY`). Use links by `rel`, not status, to decide what to call next.



\## Conventions

\- Never commit secrets. Keep `.env.example` current.

\- Small working steps; commit after each one with a clear message.
\- Push the branch to GitHub right after every commit.
\- \*\*Merging `day-2-3` into `main`:\*\* squash-merge (one commit on `main`), then delete `day-2-3` on GitHub (`git push origin --delete day-2-3`) and locally. Do not rewrite history. Reason: commit f56ec0d on `day-2-3` contains the sandbox buyer email and payer ID, and the squash keeps it out of `main`'s history. \*\*Done Oct 31\*\* (checked first that neither value is in the final tree).

\- Every PayPal call goes through `paypal/client.py` and is logged (request id, status) for debugging.

\- Write a pytest test for: hash chain verify, receipt validation, adjudicator fault classification on the 4 scenarios (with LLM mocked), PayPal client (with HTTP mocked).

\- Ugly-but-working end to end by end of Day 7 before any UI polish.

\- Cut order if behind: AG Grid -> prompt-injection scenario -> third outcome. Never cut the replay timeline.



\## Current status (update each session)
\- \*\*Branch:\*\* `day-12` (from `main`; `day-11` was fast-forwarded into `main`, then deleted locally and on GitHub on Nov 11). Tests: 169 passing (`.venv/Scripts/python -m pytest -q`). Frontend typecheck: `cd frontend && npx tsc --noEmit`.
\- \*\*Done:\*\* Day 1 risk check. Days 2–3: shopping agent (`backend/agents/shopping.py`), intent log (`backend/intents.py`, in-memory ledger stand-in), checkout (`backend/checkout.py`), routes `POST /shop`, `GET /paypal/return` (captures), `GET /paypal/cancel`, `GET /receipts/{id}`. CLI: `python -m scripts.shop "<request>"`. Vault: `backend/paypal/vault.py`, `orders.create\_vaulted\_order`, checkout uses it when `PAYPAL\_VAULT\_ID` is set.
\- \*\*Real run (Oct 30), passed end to end:\*\* "running shoes, size 8, under $120" → agent chose p\_03 at $109.99 → receipt rcpt\_218dfea26c7046c5 → order 4HW04778352491241 → sandbox buyer approved → `/paypal/return` captured → COMPLETED, capture 31F56295M9287835W, linked to the receipt as an event. Prompt cache read on turn 2 (1434 tokens).
\- \*\*Real vaulted run (Oct 30), passed:\*\* same request with `PAYPAL\_VAULT\_ID=2a1428396v984502s` → receipt rcpt\_627e333bfb7045c1 → one PayPal call → order 3DS712773U002684V `COMPLETED`, capture 9V56960783661725H. No redirect.
\- \*\*Gotcha: refusals.\*\* With `claude-sonnet-5-5`, a tool input field named `reasoning` triggered `stop\_reason: refusal` (category `reasoning\_extraction`) in 3/3 runs; renamed to `decision\_summary` / `note` → 3/3 OK. The server-side refusal fallback did not rescue it (it has since been removed). Avoid a field called "reasoning" in agent tool schemas (the adjudicator too); map to receipt names in code.
\- \*\*Other notes:\*\* `anthropic` SDK bumped to 0.125.0 (0.42 can't parse thinking blocks). Something else on this machine listens on port 8000; run the server on 8001 with `APP\_BASE\_URL=http://localhost:8001`.
\- \*\*Pre-staged dispute (keep open; do NOT escalate or settle before Days 6–7):\*\* PP-R-LQT-10195344, OPEN/INQUIRY, $109.99, on vaulted order 3DS712773U002684V (capture 9V56960783661725H), `custom` = rcpt\_627e333bfb7045c1. Buyer message: ordered size 8 via a shopping assistant, received size 9, wants a refund. The agent chose size 8 correctly, so this is the \*\*`MERCHANT\_ERROR`\*\* case (delivered item differs from what was chosen), not `AGENT\_ERROR`. Its receipt contents are saved in `backend/scenarios/staged/rcpt\_627e333bfb7045c1.json` (the in-memory log lost them); load it into the ledger on Days 4–5 under the same receipt\_id.
\- \*\*Days 4–5 ledger (done Oct 30):\*\* `backend/ledger/` replaced `backend/intents.py` with the same interface (`ledger.log\_intent`, `append\_event`, `get\_receipt`, `find\_by\_order`) plus `verify\_chain`. One SQLite table of records (intents and events) in a single SHA-256 chain; the hash covers canonical JSON (sorted keys, no spaces) including `prev\_hash`; genesis `prev\_hash` is 64 zeros. Triggers block UPDATE/DELETE. Intents are validated by `ledger/models.py` before hashing (bad input → `InvalidReceipt`, 422 on `/shop`). `checkout.start` refuses to order if `verify\_chain()` fails. Routes: `GET /receipts/{id}` (now with `hash`/`prev\_hash`, events carry `seq`/`hash`), `GET /ledger/verify`. CLI: `python -m scripts.ledger verify | show <id> | load-staged`.
\- \*\*MCP server:\*\* `python -m backend.ledger.mcp\_server` (stdio) exposes `log\_intent`, `get\_receipt`, `verify\_chain` over the same database. Its `log\_intent` takes `decision\_summary` (stored as `reasoning`; see the refusal gotcha). The FastAPI app calls the ledger in-process, not through MCP.
\- \*\*Staged receipt loaded:\*\* rcpt\_627e333bfb7045c1 is in the local `agent\_receipts.db` under its original ID, with events `paypal\_order\_created`, `paypal\_captured` (PayPal's own timestamps) and `ledger\_import`. `created\_at` is approximate (the order's create\_time; the real intent time was lost). The API and MCP server load `backend/scenarios/staged/` on startup (idempotent), so a fresh database (e.g. Render) gets it too.
\- \*\*Gotchas (ledger):\*\* `mcp` is 2.x: `FastMCP` was renamed `MCPServer` (`from mcp.server.mcpserver import MCPServer`); tests drive it in-process with `mcp.Client(server)`. Installing it pulled starlette 1.x, which broke fastapi 0.115, so fastapi is now 0.162.0 and pydantic 2.14.2. `DATABASE\_URL` accepts `sqlite:///` only; relative paths resolve against the repo root. Postgres + pgvector is still to do (Day 6–7 RAG needs pgvector; Render needs Postgres by Day 11). Tests use an in-memory ledger via `tests/conftest.py`.
\- \*\*PayPal anchor (done Oct 30):\*\* `verify\_chain()` alone can't catch someone who controls the DB, edits an intent and recomputes every later hash (reproduced in `tests/test\_anchor.py`). So checkout sends the intent record's hash as `purchase\_units[0].invoice\_id` on both the vaulted and redirect paths (also stored in the `paypal\_order\_created` event). `ledger.verify\_receipt\_against\_paypal(receipt\_id, paypal, order\_id=None)` recomputes the intent hash from the stored body (never trusting the stored hash column), GETs the order, and requires `custom\_id` == receipt\_id and `invoice\_id` == the recomputed hash. Pass `order\_id` from PayPal (the dispute) where possible, because the ledger's own events could be rewritten too. Route: `GET /receipts/{id}/paypal-anchor`. One intent means one order: PayPal may reject a reused invoice\_id, so a retried checkout needs a new intent (not tested).
\- \*\*Real anchored run (Oct 30), passed:\*\* vaulted, receipt rcpt\_252c81defa384283 → order 8KD591807P3316013 `COMPLETED`, capture 0VN50118EA8909453. PayPal accepted the 64-hex invoice\_id. GET order and GET capture both return it, and the anchor check gives `ok: true`.
\- \*\*Staged receipt rcpt\_627e333bfb7045c1 predates anchoring:\*\* its order 3DS712773U002684V has no invoice\_id, so the anchor check returns `ok: false, anchored: false` ("predates anchoring"). The adjudicator must treat that as "not anchored", not as tampering, or the pre-staged dispute PP-R-LQT-10195344 fails. The receipt's `created\_at` is approximate (see above).
\- \*\*Dispute `invoice\_number`: not confirmed, and skipped (Oct 30 decision).\*\* Neither existing dispute has it, but neither order had an invoice\_id, and PayPal omits unset fields, so this proves nothing. The buyer said they opened a case on anchored order 8KD591807P3316013, but the API showed no dispute on capture 0VN50118EA8909453 (by transaction id or by date); polling stopped on request. \*\*Use the capture-id path:\*\* dispute `disputed\_transactions[0].seller\_transaction\_id` (capture id) → `GET /v2/payments/captures/{id}`, which carries both `invoice\_id` (the anchor) and `custom\_id` (confirmed on capture 0VN50118EA8909453). One call, and the id comes from PayPal, not the ledger. If that case appears later, keep it OPEN as the pre-staged `BUYER\_REMORSE` dispute; do not escalate or settle it.
\- \*\*PP-R-LQT-10195344 changed state by itself:\*\* by Oct 31 01:51Z it was `WAITING\_FOR\_SELLER\_RESPONSE`, stage still `INQUIRY` (it was OPEN/INQUIRY). Nothing of ours touched it.
\- \*\*Days 6–7 (built Oct 31; live filing not yet run):\*\*
&#x20; - `backend/resolve.py`: dispute → receipt (`custom`) → anchor check on the capture (`seller\_transaction\_id`) → verdict → next PayPal step. Each stage is a ledger event on the receipt (`dispute\_opened`, `dispute\_verdict`, `dispute\_response`), so the replay timeline can show it. Safe to re-run: adjudicates once per dispute and files each step once. \*\*Files only with `file=True`\*\*, and refuses for simulated disputes.
&#x20; - `agents/adjudicator.py`: code finds the facts (integrity verified/unanchored/failed, field diff, listing injection) and limits the classes (violation + injection → MERCHANT\_ERROR/AL-3; violation → AGENT\_ERROR/AL-2; none → MERCHANT\_ERROR/AL-4 or BUYER\_REMORSE/AL-5). Claude picks among them with `output\_config.format` (json\_schema). A class outside the allowed set is overridden (confidence capped at 0.6), or goes to `needs\_review` when two classes remain. Integrity `failed` → `needs\_review` with no LLM call.
&#x20; - `agents/evidence.py`: next step chosen from the dispute's links by rel (`accept\_claim`; or `escalate` → `provide\_evidence` → `adjudicate` SELLER\_FAVOR). Claude writes the note (≤2000 chars).
&#x20; - `rag/`: 20 rules in `rag/docs/` (ids AL-, DR-, ER-, RP-), BM25 retrieval in pure Python. pgvector waits on Postgres.
&#x20; - `scenarios/`: the 4 demo failures as fixed data in `demo\_scenarios.json`, run as simulated disputes (no PayPal).
&#x20; - Webhook: `POST /paypal/webhook` verifies via `verify-webhook-signature` (needs `PAYPAL\_WEBHOOK\_ID`) and handles disputes in the background. Filing only when `DISPUTE\_AUTO\_FILE=true` (default off).
&#x20; - Routes: `POST /disputes/{id}/resolve?file=`, `GET /scenarios`, `POST /scenarios/{name}/run`. CLI: `python -m scripts.disputes list | resolve ID [--file] | scenario NAME|all`.
\- \*\*Real runs (Oct 31, on a scratch DB, not the demo ledger):\*\* all 4 scenarios gave the expected class with the real model (no overrides, confidence 0.95, rules AL-2/AL-2/AL-3/AL-5; prompt cache read 855 tokens from call 2). PP-R-LQT-10195344 adjudicated without filing → `MERCHANT\_ERROR`, AL-4, integrity `unanchored`, next step `accept\_claim`. The dispute was unchanged afterwards. A drafted accept\_claim note was 1091 chars and accurate; it was not sent.
\- \*\*Not done yet (Days 6–7):\*\* (1) No PayPal response has been filed through this code (accept\_claim and the escalate/evidence path are untested live; Day 1 tested escalate/provide-evidence/adjudicate by hand). (2) Webhook not registered: it needs a public URL (tunnel) and a webhook id in the PayPal dashboard, which is the user's call. (3) The scenarios run simulated; live versions need the sandbox buyer to open disputes in the UI. \*\*Keep PP-R-LQT-10195344 open for the demo; don't run `resolve --file` on it before recording.\*\*
\- \*\*Decisions (Oct 31):\*\* (1) No tunnel; the webhook is registered on Day 11 against the Render URL. (2) No live filing until the recording-prep sitting below. (3) Keep keyword retrieval. Anthropic has no embeddings API, and a local embedding model (sentence-transformers + torch, about 1 GB) isn't cheap for a 20-rule corpus. Postgres vs SQLite-on-disk is decided on Day 11.
\- \*\*Days 8–9 frontend (built Oct 30):\*\* Next.js 15 + React 19 + AG Grid 33 in `frontend/` (TypeScript 5; npm pulled TS 7 by default, pinned back). `/api/\*` is rewritten to `BACKEND\_URL` (default `http://localhost:8001`), so no CORS. Pages: `/` (merchant queue in AG Grid, tally, the 4 scenario "break the agent" buttons) and `/receipts/{id}?dispute=` (replay timeline, then buyer | merchant split with the verdict card and a PayPal step panel: "Check PayPal" is read-only; "File …" asks for confirmation, then calls `resolve?file=true`). Timeline logic is in `frontend/src/api/timeline.ts`: red = `steps\[].deviation`, else verdict violations on `submit\_choice`, else (MERCHANT\_ERROR with no violation) the `dispute\_opened` frame; amber = a search filter the agent dropped, or a listing with injection text. Visual direction: forensic flight-recorder report (paper/ink, IBM Plex Sans Condensed + Plex Mono + Newsreader; orange only for the brand mark, red only for the deviation). `devIndicators: false` keeps the Next badge out of recordings. New backend routes: `GET /disputes?live=` (`resolve.queue`: ledger dispute events merged with PayPal's list; ledger-only with `paypal\_error` if PayPal fails) and `GET /catalog` (`injection` = the listing's injected sentences). Don't create a top-level `lib/` folder: the root `.gitignore` ignores `lib/`.
\- \*\*UI checked (Oct 30, scratch DB, headless Edge screenshots):\*\* all 4 scenarios gave the expected class with the real model (0.93–0.96; AL-2/AL-2/AL-3/AL-5). PP-R-LQT-10195344 adjudicated without filing → MERCHANT\_ERROR AL-4, unanchored, next `accept\_claim`; nothing filed. Screenshot recipe: `msedge --headless=new --window-size=1400,2600 --virtual-time-budget=12000 --screenshot=out.png URL` (the time budget lets the replay animation finish).
\- \*\*FILING\_ENABLED (Oct 30):\*\* master switch, default `false`. `resolve.resolve(file=True)` raises `FilingDisabled`, so the route (403, checked before any PayPal or Claude call), the webhook (auto-file needs both flags) and the CLI all refuse. Resolve results carry `filing\_enabled`; with it off, the merchant pane shows "Would file: <action>" and no button. Checked live: `resolve?file=true` on PP-R-LQT-10195344 → 403, nothing sent.
\- \*\*UI review fixes (Oct 30):\*\* (1) Scenario runs go through `scenarios.run()` (route and CLI): log the receipt, then `checkout.start` with the vault, so every run is a \*\*real sandbox order\*\* anchored by invoice\_id; the dispute stays simulated (`SIM-…`, never filed) but carries the real capture id, so integrity is `verified`. Without `PAYPAL\_VAULT\_ID` or credentials, runs fall back to unanchored. Each click is a real sandbox charge plus one Claude call. (2) 5th scenario `tampered\_receipt` (built on `wrong\_size`): after the real order, `backend/ledger/tamper.py` rewrites the intent (mandate size 8→9, deviation notes dropped), rehashes every later record, and rewrites the ledger's own copy of invoice\_id. `verify\_chain` passes; the anchor check on the capture fails → integrity `failed` (`check: "anchor"`, UI: red "altered after purchase") → `needs\_review`, AL-6, no LLM call. Guards: only receipts with `agent\_run.model == "seeded-scenario"`, never a staged receipt (so never PP-R-LQT's), only when the receipt's records are the chain tail (no other receipt's hash can change), in one write transaction; triggers are recreated. (3) Queue columns all flex from minimums that fit 1270px. (4) `resolve.queue` drops disputes with no receipt in the ledger; unseen live disputes are looked up once (GET dispute → `custom`, cached). (5) Each card has a Run ▸ button. Integrity results now carry `check` ("chain"/"anchor"). Tests: 115.
\- \*\*Real run (Oct 30, scratch DB):\*\* all 5 scenarios live: wrong\_size order 482030303J252990G, over\_budget 3H580825UB576321F, prompt\_injection 04W6666807612005J, buyer\_remorse 5BM57974W96593448 — all `verified`, expected classes (0.96–0.98). tampered\_receipt order 8WT70285BJ1146034 → `needs\_review`, anchor failed, `verify\_chain` ok.
\- \*\*Receipt page fixes (Oct 30):\*\* (1) Verdict diff rows carry `delivered` / `delivered\_ok`: what the buyer says arrived, parsed in code from their message (`adjudicator.delivered\_report`, size only, needs a delivery verb: "received/shipped/arrived … size 9"; "ordered in size 9" doesn't count). The model is told it's the buyer's report, not a fact. The verdict card shows a "Delivered (buyer reports)" column, red on mismatch. Verdicts stored before this have no `delivered` and show "—" (they're stored once per dispute). (2) The buyer box says "Recommended: refund of $X" until a `dispute\_response` with `accept\_claim` exists for the dispute, then "Refunded $X". (3) Its reason comes from the verdict (`frontend/src/api/explain.ts`): "proves" only when integrity is `verified`, otherwise "shows" (so PP-R-LQT's unanchored receipt "shows"). (4) `ledger\_import` frames are dimmed, collapsed system notes and not counted as frames. (5) Records are labelled "chain #N". Tests: 121.
\- \*\*Day 11 build (Oct 31, on `day-11`):\*\* `render.yaml` Blueprint: `agent-receipts-api` (Python, \*\*free plan, no disk\*\* since Nov 9, oregon, `DATABASE\_URL=sqlite:///./agent\_receipts.db`, one uvicorn worker, health `/health`) and `agent-receipts-web` (Node 24, free, `rootDir: frontend`, `BACKEND\_URL` is `sync: false`: set it by hand to the API's public https URL, because free web services can't receive private-network traffic; rewrites are fixed at build, so changing it needs a rebuild). Both deploy from `main`. Secrets are `sync: false`. `APP\_BASE\_URL` falls back to `RENDER\_EXTERNAL\_URL`. `tests/test\_render\_blueprint.py` guards FILING\_ENABLED/DISPUTE\_AUTO\_FILE=false, free plan with no disk, the rate limit and no secret values. \*\*Rate limit\*\* (`backend/ratelimit.py`, in memory): `RATE\_LIMIT\_PER\_MINUTE` per visitor (first X-Forwarded-For entry; spoofable, so the global `RATE\_LIMIT\_PER\_DAY` is the real cost cap), defaults 3 and 150, 0 = off, on `/shop`, `/scenarios/\*/run`, `/disputes/\*/resolve` (including read-only checks). Over the limit: 429 + `Retry-After` + `example` (latest decided verdict's receipt, else the staged receipt); the UI shows an amber "Demo limit reached" note linking to it. Checked live through the Next proxy: 200, then 429. Pins: `.python-version` 3.11.9, `frontend/package.json` engines `>=20.9 <25`. Render's exact build (`npm ci && npm run build`) passes locally. Tests: 132.
  - **Buyer confirmation is enforced by the backend (Nov 11):** `POST /mandate/parse` stores the parsed request in `backend/mandates.py` (in memory) and returns `mandate_id`; `POST /mandate/{id}/confirm` stamps server time and locks it (idempotent); public `POST /shop` accepts only `{mandate_id, background?}` (extra fields → 422; unconfirmed → 422; unknown → 404; reused → 409; one mandate = one purchase). Receipts record `mandate.confirmation`: `buyer` (with `confirmed_by_buyer_at` from the confirm step), `scripted` (scenario runs), `cli` (`scripts.shop`). `/task` calls parse → (Authorize) confirm → shop. Staged hash unchanged (tested).
  - **Live disputes DO carry `invoice_number` = the receipt hash (confirmed Nov 11, PP-R-QHV-10199556):** `disputed_transactions[0].invoice_number` equals the anchored order's `invoice_id`, and `custom` = receipt_id. This replaces the earlier "not confirmed" note (that test used an order with no invoice_id). A receipt can be found from a dispute by hash as well as by `custom`.
  - **Item notes + re-judging (Nov 11):** PayPal's buyer form puts the complaint in `disputed_transactions[].items[].notes` while the thread message can be just "no" (PP-R-QHV-10199556). The adjudicator now reads thread messages and item notes/descriptions together (`buyer_statements`; `delivered_report` parses both; payload has `disputed_item_notes`). A verdict can be superseded: `resolve.rejudge(dispute, reason)` / `python -m scripts.disputes rejudge ID "reason"` appends a `dispute_verdict` event with `supersedes=<old seq>` and `reason` (verdict carries `rejudged`), keeps the old one, never files; `resolve()`, the queue and the UI use the latest and show a "Re-judged" note. No HTTP route for it. PP-R-QHV-10199556 re-judged → MERCHANT_ERROR AL-4 (0.95), integrity verified, PayPal offers `accept_claim` (not filed yet). PP-R-LQT-10195344 re-checked: MERCHANT_ERROR AL-4, unanchored, nothing filed. README "Limitations" is now "Current scope and next steps".
  - **Resolved by PayPal + complaint (Nov 11):** when `resolve()` sees a real dispute `RESOLVED` it appends one read-only `paypal_outcome` event (outcome code/reason, refunded amount, refund id, `resolved_at`, item notes), once per dispute; the timeline ends with a green "Resolved by PayPal" frame. `dispute_opened` events now store `item_notes` (notes only); the buyer box and dispute frame show the item notes as the complaint when the thread message is under 20 chars (`frontend/src/api/complaint.ts`), with the thread message beneath. **PP-R-QHV-10199556 is RESOLVED (buyer favour, $109.99 refunded, refund 0KB87031KB858403M, capture REFUNDED).**
\- \*\*Hosted demo live (Nov 11):\*\* API https://agent-receipts-api.onrender.com, web https://agent-receipts-web.onrender.com (free plan; first call ~60 s). Checked read-only against the hosted API: `POST /disputes/PP-R-LQT-10195344/resolve` → MERCHANT\_ERROR, AL-4, confidence 0.9, integrity `unanchored`, next `accept\_claim`, nothing filed. `POST /scenarios/wrong\_size/run` → order 6H238932P43928318 COMPLETED, receipt rcpt\_061b1002035c4ed3, AGENT\_ERROR AL-2 (0.97), integrity `verified`; `/paypal-anchor` ok (anchored, custom\_id ok); `/ledger/verify` ok. PayPal sandbox webhook registered via REST for `https://agent-receipts-api.onrender.com/paypal/webhook`, events CUSTOMER.DISPUTE.CREATED/UPDATED/RESOLVED (the only dispute events PayPal lists). Its id is not stored in the repo; get it from the PayPal dashboard or `GET /v1/notifications/webhooks`. \*\*Still to do:\*\* set `PAYPAL\_WEBHOOK\_ID` on Render (the user's call; keep `FILING\_ENABLED`/`DISPUTE\_AUTO\_FILE` false).
\- \*\*Nov 11 work (all on `main`):\*\*
&#x20; - \*\*Rebrand:\*\* display name is \*\*Proof of Intent\*\*, tagline "The dispute layer for AI purchases on PayPal" (UI header/title/metadata, README, this file). Repo, Render services and code identifiers keep `agent-receipts` / "receipt". README has Related work (Agent Receipts protocol/Obsigna, Mastercard Verifiable Intent, Google AP2) and Design decisions sections, live URL, repo URL, `docs/screenshots/replay.png`. `<YOUTUBE_URL>` is still a placeholder.
&#x20; - \*\*Wording:\*\* the receipt is hash-chained and PayPal-anchored, \*\*not cryptographically signed\*\*; say "hash-anchored" / "tamper-evident". `tests/test\_wording.py` guards it.
&#x20; - \*\*Code-review fixes:\*\* (1) `agent\_run.chosen\_listing` snapshot stored at intent time and used by `find\_facts`; older receipts (e.g. rcpt\_627e333bfb7045c1, hash unchanged) use `listing\_source="catalog\_fallback"`, shown on the verdict card. Do not declare `chosen\_listing` as a default field on the pydantic model: it would be added to old receipts and change their hashes. (2) AL-3 only when the injection is in the listing the agent saw (steps) and the violated field is one the text told it to override (`facts["injection"]`); injection without that link goes to `needs\_review`. (3) Fail closed: a real dispute with no capture id, no PayPal client or a failed lookup gets integrity `failed`, check `anchor\_unavailable`, no LLM call; it is re-checked on the next `resolve()`. `unanchored` only when the order exists with no invoice\_id. (4) Idempotency: `PayPal-Request-Id` is a uuid5 of (dispute\_id, action); webhook events are deduped by PayPal event id in the `webhook\_events` table (outside the chain; released if handling fails); a per-dispute lock wraps `resolve()`.
&#x20; - \*\*Presentation:\*\* receipt page has a PayPal anchor panel (`AnchorPanel.tsx`; `/receipts/{id}/paypal-anchor` now takes `capture\_id` and returns `custom\_id`; states Match / Mismatch / Not anchored / PayPal check unavailable). The verdict card is split into "Established by code" and "AI's ruling" (overrides noted). `/task` ("Give your agent a task"): parsed-limits authorization sentence; Authorize calls `POST /shop` with `background: true` (returns `job\_id`); the page polls `GET /shop/jobs/{id}` (`backend/jobs.py`, in memory, last 50 runs) for live progress, then redirects to the receipt. The rate limit applies to the start call. The limits shown before Authorize are a regex preview; the agent's own reading is what the receipt logs.
&#x20; - \*\*Review fixes (Nov 11, `day-12`):\*\* (1) `POST /mandate/parse` (Claude, structured output, own looser rate limit) reads the request into constraints; the task page shows those in the authorization sentence (regex preview removed; `/task?q=..&read=1` reads on load), and `/shop` takes the confirmed `constraints`: they are fixed for the agent (`submit\_choice` has no constraints field then) and the receipt's `mandate.constraints` are exactly them, plus `mandate.confirmed\_by\_buyer\_at` (extra key; `Mandate` allows extras, declares none, so old hashes are unchanged; staged hash `d0beb673…5ad9` is tested). (2) Break-the-agent header says scripted decisions with real orders. (3) AGENT\_ERROR outcome: "Refund the buyer; the agent provider is identified as responsible for the mandate breach. Recovering costs from the provider is a separate process." No liability-transfer claim anywhere. (4) Buyer box says "Your verified receipt shows…" / "Your receipt shows…", never "proves"; README Limitations says a receipt is not a buyer signature. Previously stored verdicts keep their old outcome text.
&#x20; - \*\*Still to do:\*\* set `PAYPAL\_WEBHOOK\_ID` on Render (webhook id is not in the repo), re-run the hosted checks after the redeploy, refresh the Postman collection for the new routes, recording-prep sitting, video, Devpost write-up.
&#x20; - \*\*Incident (Nov 11):\*\* `git add -A` committed `agent\_receipts.db-shm` (32 KB, 40 non-zero bytes: the WAL index header, no records) and an empty `-wal` to `origin/day-11` (commit ae3bbdc). It never reached `main`. `day-11` was deleted on GitHub instead of force-pushed. `.gitignore` now covers `*.db`, `*.db-journal`, `*.db-wal`, `*.db-shm`, `*.sqlite*`, `ui*.db`. Stage files by name, not `git add -A`.
\- \*\*Next step: Day 11 deploy.\*\* The user creates the Blueprint in the Render dashboard (`day-11` must be merged into `main` first, since render.yaml deploys `main`), then I verify the hosted URLs, then the user registers the webhook. Then fresh-clone README test, Postman collection.

\#\# Recording-prep sitting (one sitting, after Day 11 deploy, before Day 12)
\*\*Trimmed Oct 30: only 2 new buyer disputes.\*\* (1) a throwaway, for the first live `accept\_claim` through this code, off camera; (2) one `BUYER\_REMORSE` dispute for the escalate → evidence → adjudicate shot. The refund shot uses the existing PP-R-LQT-10195344 (`MERCHANT\_ERROR`, AL-4, integrity `unanchored`), with `accept\_claim` filed on camera. `wrong\_size`, `over\_budget` and `prompt\_injection` stay simulated in the video ("break the agent", no PayPal), so no live-order mode is needed for this sitting. The buyer-remorse dispute is opened first because PayPal's `adjudicate` link takes ~25 min. Budget about 45 min.

\*\*Filing switch:\*\* every filing path (route, webhook, CLI `--file`) is refused unless `FILING\_ENABLED=true` (403 on the route; the UI shows "Would file: <action>" and no File button). \*\*Only the user turns it on\*\*, in the local `.env`, for this sitting and for the on-camera filings; Claude never sets it. Render keeps it `false`, so on-camera filing runs from the local app. Set it back to `false` after recording.

\*\*Before the sitting\*\*
\- \[ ] Render is up and the webhook is registered (Day 11). `DISPUTE\_AUTO\_FILE=false` and `FILING\_ENABLED=false` on Render; every step is driven by hand locally with `python -m scripts.disputes resolve ID [--file]` (or the merchant pane).
\- \[ ] Render is deployed on the free plan, no disk (changed Nov 9; hosted data resets on restart). The sitting runs locally, so its receipts land in the \*\*local\*\* database; Render only gets them via `backend/scenarios/staged/` (see step 7). The local rate limit applies to the merchant pane too (3/min by default); set `RATE\_LIMIT\_PER\_MINUTE=0` in the local `.env` for the sitting if it gets in the way.
\- \[ ] `PAYPAL\_VAULT\_ID` still works. Sandbox buyer is logged in at sandbox.paypal.com.
\- \[ ] The user has set `FILING\_ENABLED=true` in the local `.env` and restarted the local backend.

\*\*1. Create 2 orders (Claude, about 3 min).\*\* Both use the real agent and the vault: `python -m scripts.shop "Buy me running shoes, size 8, under $120"`, run twice. Check each receipt's choice meets the mandate (size 8, ≤ $120). Write down order id → receipt\_id → role (throwaway / buyer\_remorse).
\- \[ ] If the buyer-remorse case on anchored order 8KD591807P3316013 has shown up in the API by then, use it as the buyer-remorse dispute and create only the throwaway order.
\- \[ ] `python -m scripts.ledger verify` → ok, and `GET /receipts/{id}/paypal-anchor` → `ok: true` for each new receipt.

\*\*2. The user opens both disputes (sandbox buyer UI, about 5 min).\*\* Activity → the "automatic payment" to Test Store → Report a problem. Buyer-remorse first.
| Order | Menu path | Buyer message |
|---|---|---|
| buyer\_remorse | I got a different item (or "Other") | "The shoes arrived fine, but I changed my mind and don't want them anymore. Please refund me." |
| Throwaway | I got a different item → Size | "I ordered size 8 through a shopping assistant but received size 9. Please refund me." |
\- \[ ] `python -m scripts.disputes list` shows both (about 1 min each). Note each dispute id next to its order.

\*\*3. Start the 25-minute clock: the buyer-remorse dispute (Claude).\*\*
\- \[ ] `resolve ID` (no file) → verdict `BUYER\_REMORSE`, rule AL-5, integrity `verified`.
\- \[ ] `resolve ID --file` → files `escalate`. Wait about 10 s (stage becomes CHARGEBACK, `provide\_evidence` link appears).
\- \[ ] `resolve ID --file` → files `provide\_evidence`. \*\*Note the time T.\*\* The `adjudicate` link appears around T + 25 min (Day 1: 26 min).

\*\*4. Throwaway: the first live filing through this code (Claude, during the wait).\*\*
\- \[ ] `resolve ID` → expect `MERCHANT\_ERROR` (AL-4), integrity `verified`, next `accept\_claim`. Read the verdict before filing.
\- \[ ] `resolve ID --file` → files `accept\_claim`. Check the dispute reaches RESOLVED and the buyer's refund. If it fails, fix it here; PP-R-LQT-10195344 must not be the first `accept\_claim`.
\- \[ ] The throwaway receipt's replay timeline shows dispute\_opened → dispute\_verdict → dispute\_response, and the merchant pane's "File" flow works end to end.

\*\*5. Refund shot ready: PP-R-LQT-10195344 (Claude).\*\*
\- \[ ] `resolve PP-R-LQT-10195344` (no file) → `MERCHANT\_ERROR`, AL-4, integrity `unanchored`, next `accept\_claim`. Verdict is stored, so the video shows the same one. Leave it open; `accept\_claim` is filed on camera.
\- \[ ] Run the 3 refund scenarios and the tamper scenario once on the demo database so the queue shows them (real vaulted orders, simulated disputes; needs `PAYPAL\_VAULT\_ID`).

\*\*6. After T + 25 min: buyer-remorse dispute ready (Claude).\*\*
\- \[ ] Poll the dispute (`resolve ID`, no file) until `next.action` is `adjudicate`. Do \*\*not\*\* file it: adjudicate is filed on camera, and RESOLVED follows about 2 min later (cut that wait in editing).
\- \[ ] Final check: `scripts.ledger verify` ok; the merchant queue lists both demo disputes; neither is RESOLVED yet. `FILING\_ENABLED` stays on locally until recording is done.

\*\*7. Give Render the new receipts (Claude, after recording).\*\*
\- \[ ] Save both new receipts to `backend/scenarios/staged/` (same format as rcpt\_627e333bfb7045c1, with real `paypal\_times`), commit, merge to `main` (Render redeploys and loads them at startup). Then run `POST /disputes/{id}/resolve` (no file) on Render for each, so the hosted queue shows them with Render's own verdicts.
\- \*\*Deployment:\*\* do no deployment work until Day 11.
\- \*\*Sandbox state:\*\* dispute PP-R-AAU-10195248 is RESOLVED (seller). New disputes need the sandbox buyer to open them in the UI. Vault ids 6ta13924m7343474y and 2a1428396v984502s are live; test captures exist on orders 0JM44515JF3317942 ($1), 92972337SW9742113 ($2), 0CL17926RH337022V ($2), 3DS712773U002684V ($109.99), 8KD591807P3316013 ($109.99, anchored).

\## Build plan (update checkboxes as you go)

\- \[x] \*\*Day 1 (Oct 30):\*\* repo scaffold, `.env.example`, PayPal client with token auth, health route. \*\*Risk check:\*\* create sandbox order + capture; create a sandbox dispute and settle it via adjudicate. Report what worked.

\- \[x] \*\*Days 2–3:\*\* product catalog + shopping agent + PayPal Orders end to end. Catalog: `backend/catalog/`, 20 products; p\_04 = over budget, p\_08 = prompt injection. Real run passed (see status).

\- \[x] \*\*Days 4–5:\*\* ledger (schema, hash chain, MCP server), agent must log before buying. SQLite for now; see status.

\- \[ ] \*\*Days 6–7:\*\* webhook -> adjudicator (RAG) -> evidence agent -> Disputes API; all 4 scenarios. Built and tested (scenarios simulated); live filing and webhook registration remain, see status.

\- \[x] \*\*Days 8–9:\*\* frontend: replay timeline, verdict card, AG Grid merchant queue, split screen. Built Oct 30; see status.

\- \[x] \*\*Day 10:\*\* "break the agent" controls. Done Oct 30: five cards with Run buttons, real vaulted orders per scenario (only the dispute is simulated), the tamper card, and the `FILING\_ENABLED` switch.

\- \[ ] \*\*Day 11:\*\* deploy to Render, fresh-clone README test, Postman collection

\- \[ ] \*\*Recording prep (before Day 12):\*\* the one sitting in the checklist above.

\- \[ ] \*\*Day 12:\*\* demo video

\- \[ ] \*\*Day 13:\*\* Devpost write-up, repo polish

\- \[ ] \*\*Day 14 (Nov 12):\*\* buffer, submit by noon EST

