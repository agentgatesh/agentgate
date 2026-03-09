"""Stripe integration — wallet top-up, Pro subscription, Connect payouts."""

import logging

import stripe
from fastapi import APIRouter, HTTPException, Request

from agentgate.core.config import settings
from agentgate.db.engine import async_session
from agentgate.db.models import Organization, Transaction

logger = logging.getLogger("agentgate.stripe")

router = APIRouter(prefix="/stripe", tags=["stripe"])


def _init_stripe():
    """Initialize Stripe with the configured secret key."""
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")
    stripe.api_key = settings.stripe_secret_key


# ---------------------------------------------------------------------------
# Checkout session creation (called from /account billing page)
# ---------------------------------------------------------------------------


async def create_topup_checkout(org_id: str, amount: float) -> dict:
    """Create a Stripe Checkout session for wallet top-up. Returns checkout URL."""
    _init_stripe()

    if not isinstance(amount, (int, float)) or amount < 5:
        raise HTTPException(status_code=400, detail="Minimum top-up amount is $5.00")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

    amount_cents = int(round(amount * 100))

    checkout_session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": amount_cents,
                "product_data": {
                    "name": "AgentGate Wallet Top-Up",
                    "description": f"Add ${amount:.2f} to your AgentGate wallet",
                },
            },
            "quantity": 1,
        }],
        metadata={
            "type": "topup",
            "org_id": str(org_id),
            "amount_usd": str(amount),
        },
        success_url=f"{settings.base_url}/account?topup=success",
        cancel_url=f"{settings.base_url}/account?topup=cancelled",
    )

    return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}


async def create_pro_checkout(org_id: str) -> dict:
    """Create a Stripe Checkout session for Pro subscription. Returns checkout URL."""
    _init_stripe()

    if not settings.stripe_pro_price_id:
        raise HTTPException(status_code=503, detail="Pro price not configured")

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        if org.tier == "pro":
            raise HTTPException(status_code=400, detail="Already on Pro tier")

    checkout_session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{
            "price": settings.stripe_pro_price_id,
            "quantity": 1,
        }],
        metadata={
            "type": "pro_subscription",
            "org_id": str(org_id),
        },
        success_url=f"{settings.base_url}/account?upgrade=success",
        cancel_url=f"{settings.base_url}/account?upgrade=cancelled",
    )

    return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}


@router.post("/create-topup-session")
async def create_topup_session(request: Request):
    """Create a Stripe Checkout session for wallet top-up (one-time payment).

    Body: {"org_id": "...", "amount": 10.0}
    Amount is in USD. Minimum $5.
    """
    body = await request.json()
    org_id = body.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id is required")
    return await create_topup_checkout(org_id, body.get("amount", 0))


@router.post("/create-pro-session")
async def create_pro_session(request: Request):
    """Create a Stripe Checkout session for Pro subscription ($49/mo).

    Body: {"org_id": "..."}
    """
    body = await request.json()
    org_id = body.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id is required")
    return await create_pro_checkout(org_id)


# ---------------------------------------------------------------------------
# Stripe Webhook — handles payment confirmations
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events.

    Events handled:
    - checkout.session.completed: top-up or Pro subscription confirmed
    - invoice.paid: recurring Pro subscription payment
    - customer.subscription.deleted: Pro subscription cancelled
    """
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info("Stripe webhook: %s", event_type)

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(data)
    elif event_type == "invoice.paid":
        await _handle_invoice_paid(data)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(data)

    return {"status": "ok"}


async def _handle_checkout_completed(session_data: dict):
    """Handle completed checkout — either top-up or Pro subscription."""
    metadata = session_data.get("metadata", {})
    checkout_type = metadata.get("type")
    org_id = metadata.get("org_id")

    if not org_id:
        logger.warning("Checkout completed without org_id in metadata")
        return

    if checkout_type == "topup":
        amount_usd = float(metadata.get("amount_usd", 0))
        if amount_usd <= 0:
            logger.warning("Top-up with invalid amount: %s", amount_usd)
            return

        async with async_session() as session:
            org = await session.get(Organization, org_id)
            if not org:
                logger.warning("Top-up for unknown org: %s", org_id)
                return
            org.balance = round(org.balance + amount_usd, 4)
            logger.info("Wallet top-up: org=%s amount=$%.2f new_balance=$%.4f",
                        org.name, amount_usd, org.balance)
            await session.commit()

    elif checkout_type == "pro_subscription":
        subscription_id = session_data.get("subscription")
        async with async_session() as session:
            org = await session.get(Organization, org_id)
            if not org:
                logger.warning("Pro subscription for unknown org: %s", org_id)
                return
            org.tier = "pro"
            # Store subscription ID for future management
            logger.info("Pro subscription activated: org=%s subscription=%s",
                        org.name, subscription_id)
            await session.commit()


async def _handle_invoice_paid(invoice_data: dict):
    """Handle recurring subscription payment (Pro renewal)."""
    subscription_id = invoice_data.get("subscription")
    if not subscription_id:
        return

    # Get org_id from subscription metadata
    try:
        stripe.api_key = settings.stripe_secret_key
        subscription = stripe.Subscription.retrieve(subscription_id)
        org_id = subscription.metadata.get("org_id")
    except Exception:
        logger.exception("Failed to retrieve subscription %s", subscription_id)
        return

    if not org_id:
        # Try to find from checkout session metadata
        logger.info("invoice.paid without org_id, subscription=%s", subscription_id)
        return

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if org and org.tier != "pro":
            org.tier = "pro"
            await session.commit()
            logger.info("Pro tier renewed: org=%s", org.name)


async def _handle_subscription_deleted(subscription_data: dict):
    """Handle subscription cancellation — downgrade to free."""
    org_id = subscription_data.get("metadata", {}).get("org_id")
    if not org_id:
        logger.warning("Subscription deleted without org_id")
        return

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            return
        if org.tier == "pro":
            org.tier = "free"
            await session.commit()
            logger.info("Pro subscription cancelled, downgraded to free: org=%s", org.name)


# ---------------------------------------------------------------------------
# Stripe Connect — developer payout onboarding + withdrawals
# ---------------------------------------------------------------------------

WITHDRAWAL_FEE_PCT = settings.stripe_connect_withdrawal_fee_pct  # 0.03 = 3%
MIN_WITHDRAWAL = settings.stripe_connect_min_withdrawal  # $10


async def create_connect_onboarding(org_id: str) -> dict:
    """Create a Stripe Connect Account Link for onboarding. Returns URL."""
    _init_stripe()

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        if org.stripe_connect_id:
            # Already has a Connect account — create a new login link or dashboard link
            account_link = stripe.AccountLink.create(
                account=org.stripe_connect_id,
                refresh_url=f"{settings.base_url}/account?connect=refresh",
                return_url=f"{settings.base_url}/account?connect=success",
                type="account_onboarding",
            )
            return {"onboarding_url": account_link.url}

        # Create new Express Connect account
        account = stripe.Account.create(
            type="express",
            email=org.email,
            metadata={"org_id": str(org_id), "org_name": org.name},
            capabilities={
                "transfers": {"requested": True},
            },
        )

        # Save connect ID
        org.stripe_connect_id = account.id
        await session.commit()

    account_link = stripe.AccountLink.create(
        account=account.id,
        refresh_url=f"{settings.base_url}/account?connect=refresh",
        return_url=f"{settings.base_url}/account?connect=success",
        type="account_onboarding",
    )

    logger.info("Connect account created: org=%s connect_id=%s", org.name, account.id)
    return {"onboarding_url": account_link.url, "connect_id": account.id}


async def get_connect_status(org_id: str) -> dict:
    """Get Connect account status for an org."""
    _init_stripe()

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        if not org.stripe_connect_id:
            return {"connected": False}

    account = stripe.Account.retrieve(org.stripe_connect_id)
    return {
        "connected": True,
        "connect_id": org.stripe_connect_id,
        "charges_enabled": account.charges_enabled,
        "payouts_enabled": account.payouts_enabled,
        "details_submitted": account.details_submitted,
    }


async def create_withdrawal(org_id: str, amount: float) -> dict:
    """Withdraw funds from wallet to connected Stripe account via Transfer."""
    _init_stripe()

    if not isinstance(amount, (int, float)) or amount < MIN_WITHDRAWAL:
        raise HTTPException(
            status_code=400,
            detail=f"Minimum withdrawal is ${MIN_WITHDRAWAL:.2f}",
        )

    fee = round(amount * WITHDRAWAL_FEE_PCT, 4)
    net = round(amount - fee, 4)

    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        if not org.stripe_connect_id:
            raise HTTPException(
                status_code=400,
                detail="No Stripe account connected. Connect your account first.",
            )
        if org.balance < amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. You have ${org.balance:.2f}",
            )

        # Verify Connect account can receive payouts
        account = stripe.Account.retrieve(org.stripe_connect_id)
        if not account.payouts_enabled:
            raise HTTPException(
                status_code=400,
                detail="Your Stripe account is not fully set up for payouts.",
            )

        # Create Stripe Transfer (platform -> connected account)
        net_cents = int(round(net * 100))
        transfer = stripe.Transfer.create(
            amount=net_cents,
            currency="usd",
            destination=org.stripe_connect_id,
            metadata={
                "org_id": str(org_id),
                "org_name": org.name,
                "gross_amount": str(amount),
                "fee": str(fee),
            },
        )

        # Deduct from wallet balance
        org.balance = round(org.balance - amount, 4)

        # Record withdrawal transaction
        tx = Transaction(
            payer_org_id=org.id,
            receiver_org_id=org.id,
            agent_id=org.id,  # self-referential for withdrawals
            agent_name="[withdrawal]",
            amount=amount,
            fee=fee,
            net=net,
            tx_type="withdrawal",
        )
        session.add(tx)
        await session.commit()

    logger.info(
        "Withdrawal: org=%s amount=$%.2f fee=$%.4f net=$%.4f transfer=%s",
        org.name, amount, fee, net, transfer.id,
    )
    return {
        "transfer_id": transfer.id,
        "gross_amount": amount,
        "fee": fee,
        "fee_pct": WITHDRAWAL_FEE_PCT,
        "net_amount": net,
        "new_balance": org.balance,
    }
