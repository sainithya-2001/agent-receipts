"""Receipt schema. The ledger validates every intent against these models before hashing it.

Extra keys are kept (agent_run carries chosen_size and the tool-call steps for the replay
timeline), so a receipt holds everything the agent reported, not just the fields named here.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

RECEIPT_ID_PATTERN = r"^rcpt_[0-9a-f]{16}$"
HASH_PATTERN = r"^[0-9a-f]{64}$"


class Constraints(BaseModel):
    model_config = ConfigDict(extra="allow")

    category: str | None = None
    size: str | None = None
    max_price: float | None = Field(default=None, ge=0)
    currency: str = "USD"


class Mandate(BaseModel):
    """What the human asked for."""

    request_text: str = Field(min_length=1)
    constraints: Constraints


class OptionConsidered(BaseModel):
    model_config = ConfigDict(extra="allow")

    product_id: str = Field(min_length=1)
    price: float = Field(ge=0)
    attrs: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class AgentRun(BaseModel):
    """What the agent did: the options it weighed, its choice, and why."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    options_considered: list[OptionConsidered]
    chosen_product_id: str | None
    chosen_size: str | None = None
    reasoning: str
    steps: list[dict[str, Any]] = Field(default_factory=list)


class PayPalRefs(BaseModel):
    """Always null in the hashed intent: it is logged before any PayPal call. The IDs arrive
    later as linked event records, so the intent is never changed."""

    order_id: str | None = None
    capture_id: str | None = None
    amount: str | None = None


class Receipt(BaseModel):
    """A hashed intent record as stored in the ledger."""

    receipt_id: str = Field(pattern=RECEIPT_ID_PATTERN)
    created_at: str
    mandate: Mandate
    agent_run: AgentRun
    paypal: PayPalRefs
    prev_hash: str = Field(pattern=HASH_PATTERN)
    hash: str = Field(pattern=HASH_PATTERN)
