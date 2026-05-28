"""
app/api/routes/billing.py
──────────────────────────────────────────────────────────────────────────────
Stripe billing endpoints.

These endpoints intentionally bypass the is_suspended check in get_clinic()
so that clinic owners can always access billing — even when their subscription
has lapsed and every other endpoint returns 403.
"""

from __future__ import annotations

from typing import Annotated, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, Role
from app.core.config import settings
from app.core.limiter import limiter
from app.models.clinic import Clinic

router = APIRouter(prefix="/billing", tags=["Billing"])


# ── Stripe helpers ────────────────────────────────────────────────────────────

def _require_stripe() -> None:
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe no está configurado. Contacta al administrador.",
        )
    stripe.api_key = settings.STRIPE_SECRET_KEY


_PRICE_MAP: dict[tuple[str, str], str] = {}


def _get_price_id(tier: str, period: str) -> str:
    mapping = {
        ("starter",    "monthly"): settings.STRIPE_PRICE_STARTER_MONTHLY,
        ("starter",    "annual"):  settings.STRIPE_PRICE_STARTER_ANNUAL,
        ("growth",     "monthly"): settings.STRIPE_PRICE_GROWTH_MONTHLY,
        ("growth",     "annual"):  settings.STRIPE_PRICE_GROWTH_ANNUAL,
        ("enterprise", "monthly"): settings.STRIPE_PRICE_ENTERPRISE_MONTHLY,
        ("enterprise", "annual"):  settings.STRIPE_PRICE_ENTERPRISE_ANNUAL,
    }
    price_id = mapping.get((tier, period), "")
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Precio no configurado para '{tier}' ({period}). Contacta al administrador.",
        )
    return price_id


# ── Auth gate (bypasses is_suspended) ────────────────────────────────────────

async def _billing_gate(
    user: Annotated[object, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> tuple[Clinic, AsyncSession]:
    """Returns (clinic, db). Requires owner role. Does NOT check is_suspended."""
    if user.role != Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el dueño de la clínica puede gestionar la suscripción.",
        )
    result = await db.execute(select(Clinic).where(Clinic.id == user.clinic_id))
    clinic = result.scalar_one_or_none()
    if clinic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinic not found.")
    return clinic, db


BillingCtx = Annotated[tuple[Clinic, AsyncSession], Depends(_billing_gate)]


# ── Schemas ───────────────────────────────────────────────────────────────────

class CheckoutCreate(BaseModel):
    tier: str = "starter"    # starter | growth | enterprise
    period: str = "monthly"  # monthly | annual


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class BillingStatusResponse(BaseModel):
    plan_tier: str
    subscription_status: str
    stripe_subscription_id: Optional[str] = None
    trial_ends_at: Optional[str] = None
    past_due_since: Optional[str] = None
    is_suspended: bool
    grace_days_remaining: Optional[int] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/checkout", response_model=CheckoutResponse)
@limiter.limit("10/minute")
async def create_checkout(
    request: Request,
    body: CheckoutCreate,
    billing: BillingCtx,
):
    """
    Create a Stripe Checkout session and return its URL.
    The client should redirect the user to checkout_url.
    """
    _require_stripe()
    clinic, db = billing

    if body.tier not in ("starter", "growth", "enterprise"):
        raise HTTPException(status_code=422, detail="tier debe ser starter, growth o enterprise.")
    if body.period not in ("monthly", "annual"):
        raise HTTPException(status_code=422, detail="period debe ser monthly o annual.")

    price_id = _get_price_id(body.tier, body.period)

    # Lazily create the Stripe customer the first time
    if not clinic.stripe_customer_id:
        customer = stripe.Customer.create(
            metadata={"clinic_id": str(clinic.id), "clinic_slug": clinic.slug},
        )
        clinic.stripe_customer_id = customer.id
        await db.commit()

    session = stripe.checkout.Session.create(
        customer=clinic.stripe_customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=settings.APP_BASE_URL + "/?checkout=success",
        cancel_url=settings.APP_BASE_URL + "/?checkout=cancel",
        metadata={"clinic_id": str(clinic.id)},
        subscription_data={
            "metadata": {"clinic_id": str(clinic.id), "plan_tier": body.tier},
        },
        allow_promotion_codes=True,
    )

    return CheckoutResponse(checkout_url=session.url)


@router.get("/portal", response_model=PortalResponse)
@limiter.limit("10/minute")
async def get_billing_portal(
    request: Request,
    billing: BillingCtx,
):
    """
    Create a Stripe Customer Portal session and return its URL.
    The portal lets the owner update payment methods, download invoices, and
    cancel or change their plan.
    """
    _require_stripe()
    clinic, _ = billing

    if not clinic.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No hay suscripción activa. Contrata un plan primero.",
        )

    portal = stripe.billing_portal.Session.create(
        customer=clinic.stripe_customer_id,
        return_url=settings.APP_BASE_URL + "/",
    )

    return PortalResponse(portal_url=portal.url)


@router.get("/status", response_model=BillingStatusResponse)
@limiter.limit("30/minute")
async def get_billing_status(
    request: Request,
    billing: BillingCtx,
):
    """Return the clinic's current subscription state."""
    clinic, _ = billing
    return BillingStatusResponse(
        plan_tier=clinic.plan_tier,
        subscription_status=clinic.subscription_status,
        stripe_subscription_id=clinic.stripe_subscription_id,
        trial_ends_at=clinic.trial_ends_at.isoformat() if clinic.trial_ends_at else None,
        past_due_since=clinic.past_due_since.isoformat() if clinic.past_due_since else None,
        is_suspended=clinic.is_suspended,
        grace_days_remaining=clinic.grace_days_remaining,
    )
