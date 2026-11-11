"""FastAPI entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from backend import checkout, ledger, resolve, scenarios, shop
from backend.catalog import load_products
from backend.agents.adjudicator import AdjudicationError, listing_injection
from backend.agents.evidence import EvidenceError
from backend.agents.llm import RefusalError
from backend.agents.shopping import ShoppingError
from backend.config import get_settings
from backend.ledger import LedgerError
from backend.paypal import webhooks
from backend.paypal.client import PayPalClient, PayPalError
from backend.ratelimit import demo_limit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")



@asynccontextmanager
async def lifespan(_: FastAPI):
    # The pre-staged dispute's receipt must be in the ledger before its webhook arrives.
    loaded = ledger.load_staged()
    if loaded:
        logging.getLogger(__name__).info("Loaded staged receipts: %s", ", ".join(loaded))
    yield


app = FastAPI(title="Agent Receipts", lifespan=lifespan)


@app.get("/health")
def health(deep: bool = False) -> dict:
    """Liveness check. With ?deep=true, also confirms PayPal credentials work."""
    settings = get_settings()
    result: dict = {"status": "ok", "paypal_base_url": settings.paypal_base_url}
    if deep:
        try:
            PayPalClient().access_token()
            result["paypal_auth"] = "ok"
        except (ValueError, PayPalError) as exc:
            result["status"] = "degraded"
            result["paypal_auth"] = str(exc)
    return result


class ShopRequest(BaseModel):
    request_text: str


def _paypal_error(exc: PayPalError) -> HTTPException:
    return HTTPException(502, {"paypal_status": exc.status, "debug_id": exc.debug_id, "body": exc.body})


@app.post("/shop", dependencies=[Depends(demo_limit)])
def shop_route(req: ShopRequest) -> dict:
    """Run the shopping agent, log its intent, and create the PayPal order to approve."""
    try:
        return shop.buy(req.request_text, PayPalClient())
    except (ShoppingError, RefusalError, ledger.InvalidReceipt) as exc:
        raise HTTPException(422, str(exc))
    except PayPalError as exc:
        raise _paypal_error(exc)


@app.get("/paypal/return")
def paypal_return(token: str) -> dict:
    """PayPal sends the buyer here after approval; token is the order id."""
    try:
        return checkout.complete(PayPalClient(), token)
    except PayPalError as exc:
        raise _paypal_error(exc)


@app.get("/paypal/cancel")
def paypal_cancel(token: str) -> dict:
    receipt_id = ledger.find_by_order(token)
    if receipt_id:
        ledger.append_event(receipt_id, "paypal_cancelled", {"order_id": token})
    return {"order_id": token, "status": "CANCELLED_BY_BUYER", "receipt_id": receipt_id}


@app.get("/receipts/{receipt_id}")
def receipt_route(receipt_id: str) -> dict:
    receipt = ledger.get_receipt(receipt_id)
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    return receipt


@app.get("/ledger/verify")
def ledger_verify() -> dict:
    """Recheck every hash and link in the ledger."""
    return ledger.verify_chain()


@app.get("/receipts/{receipt_id}/paypal-anchor")
def receipt_paypal_anchor(receipt_id: str, order_id: str | None = None) -> dict:
    """Compare the receipt's recomputed intent hash with the invoice_id PayPal holds."""
    try:
        return ledger.verify_receipt_against_paypal(receipt_id, PayPalClient(), order_id=order_id)
    except PayPalError as exc:
        raise _paypal_error(exc)


log = logging.getLogger(__name__)


def _handle_dispute(dispute_id: str, file: bool, event_id: str | None = None) -> None:
    try:
        result = resolve.resolve_by_id(PayPalClient(), dispute_id, file=file)
        log.info("dispute %s: %s, next %s", dispute_id, result["status"], result.get("next"))
    except Exception:
        log.exception("dispute %s: handling failed", dispute_id)
        if event_id:
            ledger.release_event(event_id)  # let PayPal's retry of this event be handled


@app.post("/paypal/webhook")
def paypal_webhook(request: Request, background: BackgroundTasks, event: dict = Body(...)) -> dict:
    """PayPal webhook: verify the signature, then handle CUSTOMER.DISPUTE.* in the background."""
    settings = get_settings()
    if not settings.paypal_webhook_id:
        raise HTTPException(503, "PAYPAL_WEBHOOK_ID is not set")
    try:
        verified = webhooks.verify_signature(PayPalClient(), request.headers, event, settings.paypal_webhook_id)
    except PayPalError as exc:
        raise _paypal_error(exc)
    if not verified:
        raise HTTPException(400, "webhook signature not verified")
    dispute_id = webhooks.dispute_id_of(event)
    event_id = event.get("id")
    # PayPal redelivers events; handle each event id once. Checked after the signature, so only
    # genuine events can use up an id.
    if event_id and not ledger.claim_event(event_id):
        return {"received": True, "duplicate": True, "event_type": event.get("event_type"),
                "dispute_id": dispute_id}
    if dispute_id:
        background.add_task(_handle_dispute, dispute_id, settings.dispute_auto_file and settings.filing_enabled,
                            event_id)
    return {"received": True, "event_type": event.get("event_type"), "dispute_id": dispute_id}


@app.get("/disputes")
def dispute_queue(live: bool = True) -> dict:
    """Merchant queue: disputes in the ledger, merged with PayPal's live list when live=true."""
    if not live:
        return resolve.queue()
    try:
        paypal = PayPalClient()
    except ValueError as exc:  # no credentials: show the ledger's view
        return {**resolve.queue(), "paypal_error": str(exc)}
    return resolve.queue(paypal)


@app.get("/catalog")
def catalog() -> list[dict]:
    """The demo catalog. injection is the listing text aimed at shopping agents, if any."""
    return [{**p, "injection": listing_injection(p["description"])} for p in load_products()]


@app.post("/disputes/{dispute_id}/resolve", dependencies=[Depends(demo_limit)])
def dispute_resolve(dispute_id: str, file: bool = False) -> dict:
    """Adjudicate a real dispute. file=true also takes the next PayPal step (needs FILING_ENABLED)."""
    try:
        if file:
            resolve.check_filing_enabled()  # before any PayPal or Claude call
        return resolve.resolve_by_id(PayPalClient(), dispute_id, file=file)
    except resolve.FilingDisabled as exc:
        raise HTTPException(403, str(exc))
    except PayPalError as exc:
        raise _paypal_error(exc)
    except (AdjudicationError, EvidenceError, RefusalError, resolve.ResolveError) as exc:
        raise HTTPException(422, str(exc))


@app.get("/scenarios")
def scenario_list() -> list[dict]:
    return scenarios.list_scenarios()


@app.post("/scenarios/{name}/run", dependencies=[Depends(demo_limit)])
def scenario_run(name: str) -> dict:
    """Seed a demo failure: log its receipt, buy it for real (vaulted, anchored) when the vault
    is set, simulate the dispute and adjudicate it. The dispute is never filed."""
    if scenarios.get_scenario(name) is None:
        raise HTTPException(404, "unknown scenario")
    try:
        paypal = PayPalClient()
    except ValueError:  # no credentials: receipt and dispute only, unanchored
        paypal = None
    try:
        return scenarios.run(name, paypal)
    except PayPalError as exc:
        raise _paypal_error(exc)
    except (AdjudicationError, RefusalError, scenarios.ScenarioError, checkout.CheckoutError, LedgerError) as exc:
        raise HTTPException(422, str(exc))
