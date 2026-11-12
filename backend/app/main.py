"""FastAPI entry point."""
import logging
from contextlib import asynccontextmanager

from typing import Annotated

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict

from backend import checkout, jobs, ledger, mandates, resolve, scenarios, shop
from backend.catalog import load_products
from backend.agents.adjudicator import AdjudicationError, listing_injection
from backend.agents import mandate
from backend.agents.evidence import EvidenceError
from backend.agents.llm import RefusalError
from backend.agents.shopping import ShoppingError
from backend.config import get_settings
from backend.ledger import LedgerError
from backend.paypal import webhooks
from backend.paypal.client import PayPalClient, PayPalError, UnsafePayPalPath
from backend.ratelimit import demo_limit, demo_limit_parse

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
    """Only the id of a mandate the buyer confirmed: free text or constraints are rejected (422)."""
    model_config = ConfigDict(extra="forbid")
    mandate_id: str
    confirm_token: str | None = None  # from /mandate/parse; checked in code so a missing one is 403, not 422
    background: bool = False  # true: start the run, answer at once with a job_id to poll


class ParseRequest(BaseModel):
    request_text: str


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_token: str | None = None


@app.post("/mandate/parse", dependencies=[Depends(demo_limit_parse)])
def mandate_parse(req: ParseRequest) -> dict:
    """Read a shopping request into constraints {category, size, max_price, currency} for the buyer
    to confirm. Nothing is logged or bought."""
    try:
        constraints = mandate.parse(req.request_text)
    except (mandate.MandateError, RefusalError) as exc:
        raise HTTPException(422, str(exc))
    return mandates.create(req.request_text, constraints)


@app.post("/mandate/{mandate_id}/confirm")
def mandate_confirm(mandate_id: str, req: ConfirmRequest | None = None) -> dict:
    """The buyer confirms the parsed constraints, presenting the confirm_token /mandate/parse returned.
    The server stamps the time and locks them; confirming again returns the same record."""
    try:
        return mandates.confirm(mandate_id, req.confirm_token if req else None)
    except mandates.MandateNotFound as exc:
        raise HTTPException(404, str(exc))
    except mandates.MandateForbidden as exc:
        raise HTTPException(403, str(exc))


def _paypal_error(exc: PayPalError) -> HTTPException:
    return HTTPException(502, {"paypal_status": exc.status, "debug_id": exc.debug_id, "body": exc.body})


@app.post("/shop", dependencies=[Depends(demo_limit)])
def shop_route(req: ShopRequest) -> dict:
    """Run the shopping agent on a confirmed mandate, log its intent, and create the PayPal order.
    One mandate is one purchase."""
    try:
        paypal = PayPalClient()
    except ValueError as exc:
        raise HTTPException(503, str(exc))
    try:
        confirmed = mandates.claim(req.mandate_id, req.confirm_token)
    except mandates.MandateNotFound as exc:
        raise HTTPException(404, str(exc))
    except mandates.MandateForbidden as exc:
        raise HTTPException(403, str(exc))
    except mandates.MandateNotConfirmed as exc:
        raise HTTPException(422, str(exc))
    except mandates.MandateUsed as exc:
        raise HTTPException(409, str(exc))

    def run(progress=None):
        ordering = {"started": False}

        def track(event: dict) -> None:
            if event.get("kind") == "ordering":
                ordering["started"] = True
            if progress:
                progress(event)

        try:
            result = shop.buy(confirmed["request_text"], paypal, progress=track, constraints=confirmed["constraints"],
                              confirmed_at=confirmed["confirmed_by_buyer_at"], guard=True)
            if isinstance(result, dict) and result.get("no_match"):
                mandates.release(confirmed["mandate_id"], order_may_exist=False)  # nothing happened: adjust and retry
            return result
        except Exception as exc:
            # Failed before any PayPal call could have been made (the agent, the ledger, or checkout's own
            # pre-checks): the buyer may retry with the same mandate. Once ordering began, an order may
            # exist, so the mandate stays used.
            mandates.release(confirmed["mandate_id"],
                             order_may_exist=ordering["started"] and not isinstance(exc, checkout.CheckoutError))
            raise

    if req.background:
        return {"job_id": jobs.start(run, _describe_shop_error)}
    try:
        return run()
    except RefusalError:
        raise HTTPException(422, SHOP_REFUSED)
    except (ShoppingError, ledger.InvalidReceipt, checkout.CheckoutError) as exc:
        raise HTTPException(422, str(exc))
    except PayPalError as exc:
        raise _paypal_error(exc)


SHOP_REFUSED = "The agent couldn't complete this request. Nothing was charged; try again or adjust your limits."


def _describe_shop_error(exc: Exception) -> str:
    if isinstance(exc, PayPalError):
        return f"PayPal rejected the order ({exc.status}, debug id {exc.debug_id})."
    if isinstance(exc, RefusalError):
        return SHOP_REFUSED
    if isinstance(exc, (ShoppingError, ledger.InvalidReceipt)):
        return f"{exc} Nothing was charged; you can authorize again."
    if isinstance(exc, checkout.CheckoutError):
        return f"{exc}. Nothing was charged; you can authorize again."
    logging.getLogger(__name__).exception("shop run failed")
    return "The run failed unexpectedly; try again."


@app.get("/shop/jobs/{job_id}")
def shop_job(job_id: str) -> dict:
    """Progress of a background /shop run: status, events so far, and the result when done."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, {"code": "job_unknown", "job_id": job_id,
                                  "message": "No such run. Runs are kept in memory, so the server may have "
                                             "restarted while it was running."})
    return job


# Ids that go into PayPal URLs: letters, digits, "-" and "_" only. Anything else (".", "/", "?", "#", "%")
# could steer a call at another PayPal endpoint, so it is refused with 422 before any PayPal call.
_ID = r"^[A-Za-z0-9_-]{1,64}$"
PayPalId = Annotated[str, Query(pattern=_ID, max_length=64)]


@app.get("/paypal/return")
def paypal_return(token: PayPalId) -> dict:
    """PayPal sends the buyer here after approval; token is the order id."""
    try:
        return checkout.complete(PayPalClient(), token)
    except PayPalError as exc:
        raise _paypal_error(exc)


@app.get("/paypal/cancel")
def paypal_cancel(token: PayPalId) -> dict:
    receipt_id = ledger.find_by_order(token)
    if receipt_id:
        kinds = {e["kind"] for e in ledger.get_receipt(receipt_id)["events"] if e.get("order_id") == token}
        # Anyone can open this URL, so it can't rewrite the history of an order that was already
        # captured, and a repeat doesn't add a second event.
        if not kinds & {"paypal_captured", "paypal_cancelled"}:
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
def receipt_paypal_anchor(receipt_id: str, order_id: Annotated[str | None, Query(pattern=_ID, max_length=64)] = None,
                          capture_id: Annotated[str | None, Query(pattern=_ID, max_length=64)] = None) -> dict:
    """Compare the receipt's recomputed intent hash with the invoice_id PayPal holds."""
    try:
        return ledger.verify_receipt_against_paypal(receipt_id, PayPalClient(), order_id=order_id,
                                                    capture_id=capture_id)
    except PayPalError as exc:
        raise _paypal_error(exc)
    except ValueError as exc:  # no PayPal credentials configured
        raise HTTPException(503, str(exc))


log = logging.getLogger(__name__)


def _handle_dispute(dispute_id: str, file: bool, event_id: str | None = None) -> None:
    try:
        result = resolve.resolve_by_id(PayPalClient(), dispute_id, file=file)
        log.info("dispute %s: %s, next %s", dispute_id, result["status"], result.get("next"))
        if event_id:
            ledger.finish_event(event_id)  # done only now that handling succeeded
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
def dispute_resolve(dispute_id: Annotated[str, Path(pattern=_ID, max_length=64)], file: bool = False) -> dict:
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
