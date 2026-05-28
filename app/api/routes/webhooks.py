"""
app/api/routes/webhooks.py
──────────────────────────────────────────────────────────────────────────────
Stripe webhook receiver.

Security model: this endpoint is NOT behind JWT authentication.
Instead it uses stripe.Webhook.construct_event() which verifies the
Stripe-Signature header using STRIPE_WEBHOOK_SECRET. Any request that fails
this check is rejected with 400 — Stripe will retry failed events for up to
72 hours.

Critical: payload must be read as raw bytes BEFORE any JSON parsing.
FastAPI's request.body() satisfies this requirement.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.models.clinic import Clinic, PlanTier, SubscriptionStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

DBSession = Annotated[AsyncSession, Depends(get_db)]

# ── Stripe status → our SubscriptionStatus ────────────────────────────────────

_STATUS_MAP: dict[str, str] = {
    "active":             SubscriptionStatus.ACTIVE,
    "trialing":           SubscriptionStatus.TRIALING,
    "past_due":           SubscriptionStatus.PAST_DUE,
    "incomplete":         SubscriptionStatus.PAST_DUE,
    "incomplete_expired": SubscriptionStatus.CANCELED,
    "canceled":           SubscriptionStatus.CANCELED,
    "unpaid":             SubscriptionStatus.SUSPENDED,
    "paused":             SubscriptionStatus.SUSPENDED,
}

# ── Internal helpers ──────────────────────────────────────────────────────────

async def _find_clinic_by_customer(customer_id: str, db: AsyncSession) -> Clinic | None:
    result = await db.execute(
        select(Clinic).where(Clinic.stripe_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


async def _find_clinic_by_id(clinic_id_str: str, db: AsyncSession) -> Clinic | None:
    try:
        clinic_id = uuid.UUID(clinic_id_str)
    except (ValueError, AttributeError):
        return None
    result = await db.execute(select(Clinic).where(Clinic.id == clinic_id))
    return result.scalar_one_or_none()


async def _handle_checkout_completed(obj: dict, db: AsyncSession) -> None:
    """Link stripe_customer_id and stripe_subscription_id to the clinic."""
    clinic_id_str = (obj.get("metadata") or {}).get("clinic_id", "")
    clinic = await _find_clinic_by_id(clinic_id_str, db)
    if clinic is None:
        log.warning("checkout.session.completed: clinic %s not found", clinic_id_str)
        return

    clinic.stripe_customer_id = obj.get("customer") or clinic.stripe_customer_id
    clinic.stripe_subscription_id = obj.get("subscription") or clinic.stripe_subscription_id
    await db.commit()
    log.info("checkout.session.completed: clinic=%s linked to customer=%s", clinic_id_str, clinic.stripe_customer_id)


async def _handle_subscription_updated(obj: dict, db: AsyncSession) -> None:
    """Sync subscription_status, plan_tier, and past_due_since from Stripe."""
    customer_id = obj.get("customer", "")
    sub_id = obj.get("id", "")

    # Try metadata first (more reliable), then fall back to customer ID
    clinic_id_str = (obj.get("metadata") or {}).get("clinic_id", "")
    if clinic_id_str:
        clinic = await _find_clinic_by_id(clinic_id_str, db)
    else:
        clinic = await _find_clinic_by_customer(customer_id, db)

    if clinic is None:
        log.warning("subscription.updated: no clinic found for customer=%s sub=%s", customer_id, sub_id)
        return

    # Update subscription ID
    clinic.stripe_subscription_id = sub_id

    # Map status
    stripe_status = obj.get("status", "")
    new_status = _STATUS_MAP.get(stripe_status, SubscriptionStatus.PAST_DUE)
    old_status = clinic.subscription_status
    clinic.subscription_status = new_status

    # Track grace period start for past_due
    if new_status == SubscriptionStatus.PAST_DUE and old_status != SubscriptionStatus.PAST_DUE:
        clinic.past_due_since = datetime.now(timezone.utc)
    elif new_status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
        clinic.past_due_since = None  # payment recovered

    # Update plan_tier from subscription metadata (set in create_checkout)
    sub_metadata = obj.get("metadata") or {}
    tier_from_meta = sub_metadata.get("plan_tier", "")
    if tier_from_meta in (PlanTier.STARTER, PlanTier.GROWTH, PlanTier.ENTERPRISE):
        clinic.plan_tier = tier_from_meta

    await db.commit()
    log.info(
        "subscription.updated: clinic=%s status %s→%s plan=%s",
        clinic.id, old_status, new_status, clinic.plan_tier,
    )


async def _handle_subscription_deleted(obj: dict, db: AsyncSession) -> None:
    """Mark subscription as canceled when Stripe deletes it."""
    customer_id = obj.get("customer", "")
    clinic_id_str = (obj.get("metadata") or {}).get("clinic_id", "")

    clinic = (
        await _find_clinic_by_id(clinic_id_str, db)
        if clinic_id_str
        else await _find_clinic_by_customer(customer_id, db)
    )
    if clinic is None:
        log.warning("subscription.deleted: no clinic found for customer=%s", customer_id)
        return

    clinic.subscription_status = SubscriptionStatus.CANCELED
    clinic.stripe_subscription_id = None
    clinic.past_due_since = None
    await db.commit()
    log.info("subscription.deleted: clinic=%s → canceled", clinic.id)


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, db: DBSession):
    """
    Receive and process Stripe events.

    Stripe expects a 2xx response within 30 s; if it doesn't receive one it
    retries the event for up to 72 h. We return 200 immediately after
    dispatching the handler so Stripe doesn't see timeouts.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook not configured.",
        )

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        log.warning("stripe_webhook: invalid signature")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature.")
    except Exception as exc:
        log.warning("stripe_webhook: malformed payload — %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload.")

    event_type: str = event["type"]
    obj: dict = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(obj, db)
        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(obj, db)
        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(obj, db)
        else:
            log.debug("stripe_webhook: unhandled event type %s", event_type)
    except Exception:
        log.error("stripe_webhook: handler error for %s", event_type, exc_info=True)
        # Still return 200 — if we return 4xx/5xx Stripe will retry the event
        # endlessly. Log the error; fix in code; replay from Stripe dashboard.

    return {"received": True}
