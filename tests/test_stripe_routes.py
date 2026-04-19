"""Tests for stripe_routes — checkout sessions + webhook handler."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from agentgate.server.app import app
from agentgate.server.auth_routes import _make_session, hash_password

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_org(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "name": "test-org",
        "email": "test@example.com",
        "password_hash": hash_password("testpass123"),
        "oauth_provider": None,
        "oauth_id": None,
        "api_key_hash": "fakehash",
        "tier": "free",
        "balance": 5.0,
        "rate_limit": 10.0,
        "rate_burst": 20,
        "cost_per_invocation": 0.001,
        "billing_alert_threshold": None,
        "secondary_api_key_hash": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    org = MagicMock()
    for k, v in defaults.items():
        setattr(org, k, v)
    return org


def _session_cookie(org):
    return _make_session(str(org.id), org.email or "")


def _mock_db_returning(value):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = value
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar.return_value = 0
    mock_result.one.return_value = MagicMock(
        total=0, errors=0, total_spent=0, total_earned=0,
        total_fees=0, tx_count=0,
    )
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.get = AsyncMock(return_value=value)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_ctx), mock_session


def _mock_stripe_checkout_session(**kwargs):
    s = MagicMock()
    s.url = "https://checkout.stripe.com/test_session"
    s.id = "cs_test_123"
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# /stripe/create-topup-session
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
def test_create_topup_session(mock_db, mock_stripe, mock_settings):
    org = _make_fake_org()
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.base_url = "https://agentgate.sh"
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value
    mock_stripe.checkout.Session.create.return_value = _mock_stripe_checkout_session()

    response = client.post(
        "/stripe/create-topup-session",
        json={"org_id": str(org.id), "amount": 10.0},
    )
    assert response.status_code == 200
    data = response.json()
    assert "checkout_url" in data
    assert data["checkout_url"] == "https://checkout.stripe.com/test_session"
    assert data["session_id"] == "cs_test_123"


@patch("agentgate.server.stripe_routes.settings")
def test_create_topup_session_no_stripe_key(mock_settings):
    mock_settings.stripe_secret_key = ""
    response = client.post(
        "/stripe/create-topup-session",
        json={"org_id": str(uuid.uuid4()), "amount": 10.0},
    )
    assert response.status_code == 503


def test_create_topup_session_no_org_id():
    with patch("agentgate.server.stripe_routes.settings") as mock_settings:
        mock_settings.stripe_secret_key = "sk_test_123"
        response = client.post(
            "/stripe/create-topup-session",
            json={"amount": 10.0},
        )
        assert response.status_code == 400


def test_create_topup_session_low_amount():
    with patch("agentgate.server.stripe_routes.settings") as mock_settings:
        mock_settings.stripe_secret_key = "sk_test_123"
        response = client.post(
            "/stripe/create-topup-session",
            json={"org_id": str(uuid.uuid4()), "amount": 2.0},
        )
        assert response.status_code == 400
        assert "Minimum" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /stripe/create-pro-session
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
def test_create_pro_session(mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(tier="free")
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_pro_price_id = "price_test_123"
    mock_settings.base_url = "https://agentgate.sh"
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value
    mock_stripe.checkout.Session.create.return_value = _mock_stripe_checkout_session()

    response = client.post(
        "/stripe/create-pro-session",
        json={"org_id": str(org.id)},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["checkout_url"] == "https://checkout.stripe.com/test_session"


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
def test_create_pro_session_already_pro(mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(tier="pro")
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_pro_price_id = "price_test_123"
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    response = client.post(
        "/stripe/create-pro-session",
        json={"org_id": str(org.id)},
    )
    assert response.status_code == 400
    assert "Already on Pro" in response.json()["detail"]


@patch("agentgate.server.stripe_routes.settings")
def test_create_pro_session_no_price_id(mock_settings):
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_pro_price_id = ""
    with patch("agentgate.server.stripe_routes.async_session") as mock_db:
        org = _make_fake_org(tier="free")
        db_factory, _ = _mock_db_returning(org)
        mock_db.side_effect = db_factory.side_effect
        mock_db.return_value = db_factory.return_value
        response = client.post(
            "/stripe/create-pro-session",
            json={"org_id": str(org.id)},
        )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# /stripe/webhook
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.mark_event_processed", new_callable=AsyncMock)
@patch("agentgate.server.stripe_routes.is_event_processed", new_callable=AsyncMock)
@patch("agentgate.server.stripe_routes.credit_wallet", new_callable=AsyncMock)
@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
def test_webhook_topup_completed(
    mock_db, mock_stripe, mock_settings, mock_credit, mock_is_proc, mock_mark_proc,
):
    org = _make_fake_org(balance=10.0)
    mock_settings.stripe_webhook_secret = "whsec_test"
    mock_settings.stripe_secret_key = "sk_test_123"
    db_factory, mock_session = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value
    mock_is_proc.return_value = False

    event_data = {
        "id": "evt_test_topup_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {
                    "type": "topup",
                    "org_id": str(org.id),
                    "amount_usd": "25.0",
                },
            },
        },
    }
    payload = json.dumps(event_data).encode()

    mock_stripe.Webhook.construct_event.return_value = event_data

    response = client.post(
        "/stripe/webhook",
        content=payload,
        headers={"stripe-signature": "t=123,v1=abc"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    mock_credit.assert_awaited_once_with(org.id, 25.0)
    mock_mark_proc.assert_awaited_once()


@patch("agentgate.server.stripe_routes.mark_event_processed", new_callable=AsyncMock)
@patch("agentgate.server.stripe_routes.is_event_processed", new_callable=AsyncMock)
@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
def test_webhook_pro_subscription(
    mock_db, mock_stripe, mock_settings, mock_is_proc, mock_mark_proc,
):
    org = _make_fake_org(tier="free")
    mock_settings.stripe_webhook_secret = "whsec_test"
    mock_settings.stripe_secret_key = "sk_test_123"
    db_factory, mock_session = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value
    mock_is_proc.return_value = False

    event_data = {
        "id": "evt_test_pro_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "subscription": "sub_test_123",
                "metadata": {
                    "type": "pro_subscription",
                    "org_id": str(org.id),
                },
            },
        },
    }
    payload = json.dumps(event_data).encode()
    mock_stripe.Webhook.construct_event.return_value = event_data

    response = client.post(
        "/stripe/webhook",
        content=payload,
        headers={"stripe-signature": "t=123,v1=abc"},
    )
    assert response.status_code == 200
    assert org.tier == "pro"
    mock_session.commit.assert_called()
    mock_mark_proc.assert_awaited_once()


@patch("agentgate.server.stripe_routes.mark_event_processed", new_callable=AsyncMock)
@patch("agentgate.server.stripe_routes.is_event_processed", new_callable=AsyncMock)
@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
def test_webhook_subscription_deleted(
    mock_db, mock_stripe, mock_settings, mock_is_proc, mock_mark_proc,
):
    org = _make_fake_org(tier="pro")
    mock_settings.stripe_webhook_secret = "whsec_test"
    mock_settings.stripe_secret_key = "sk_test_123"
    db_factory, mock_session = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value
    mock_is_proc.return_value = False

    event_data = {
        "id": "evt_test_sub_del_1",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "metadata": {
                    "org_id": str(org.id),
                },
            },
        },
    }
    payload = json.dumps(event_data).encode()
    mock_stripe.Webhook.construct_event.return_value = event_data

    response = client.post(
        "/stripe/webhook",
        content=payload,
        headers={"stripe-signature": "t=123,v1=abc"},
    )
    assert response.status_code == 200
    assert org.tier == "free"
    mock_session.commit.assert_called()
    mock_mark_proc.assert_awaited_once()


@patch("agentgate.server.stripe_routes.settings")
def test_webhook_no_secret(mock_settings):
    mock_settings.stripe_webhook_secret = ""
    response = client.post(
        "/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "t=123,v1=abc"},
    )
    assert response.status_code == 503


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
def test_webhook_invalid_signature(mock_stripe, mock_settings):
    mock_settings.stripe_webhook_secret = "whsec_test"
    mock_stripe.Webhook.construct_event.side_effect = mock_stripe.error.SignatureVerificationError
    exc_cls = type("SignatureVerificationError", (Exception,), {})
    mock_stripe.error.SignatureVerificationError = exc_cls
    mock_stripe.Webhook.construct_event.side_effect = mock_stripe.error.SignatureVerificationError()

    response = client.post(
        "/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "t=123,v1=bad"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /account/api/topup — session-authed topup
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_account_topup(mock_get_user, mock_stripe_db, mock_stripe, mock_settings):
    org = _make_fake_org()
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.base_url = "https://agentgate.sh"

    db_factory, _ = _mock_db_returning(org)
    mock_stripe_db.side_effect = db_factory.side_effect
    mock_stripe_db.return_value = db_factory.return_value
    mock_stripe.checkout.Session.create.return_value = _mock_stripe_checkout_session()

    cookie = _session_cookie(org)
    response = client.post(
        "/account/api/topup",
        json={"amount": 20.0},
        cookies={"session": cookie},
    )
    assert response.status_code == 200
    assert "checkout_url" in response.json()


def test_account_topup_no_auth():
    response = client.post("/account/api/topup", json={"amount": 10.0})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# /account/api/subscribe-pro — session-authed Pro subscription
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_account_subscribe_pro(mock_get_user, mock_stripe_db, mock_stripe, mock_settings):
    org = _make_fake_org(tier="free")
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_pro_price_id = "price_test_123"
    mock_settings.base_url = "https://agentgate.sh"

    db_factory, _ = _mock_db_returning(org)
    mock_stripe_db.side_effect = db_factory.side_effect
    mock_stripe_db.return_value = db_factory.return_value
    mock_stripe.checkout.Session.create.return_value = _mock_stripe_checkout_session()

    cookie = _session_cookie(org)
    response = client.post(
        "/account/api/subscribe-pro",
        json={},
        cookies={"session": cookie},
    )
    assert response.status_code == 200
    assert "checkout_url" in response.json()


def test_account_subscribe_pro_no_auth():
    response = client.post("/account/api/subscribe-pro", json={})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Stripe Connect — onboarding
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_connect_onboard_new(mock_get_user, mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(stripe_connect_id=None)
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.base_url = "https://agentgate.sh"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, mock_session = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    mock_account = MagicMock()
    mock_account.id = "acct_test_123"
    mock_stripe.Account.create.return_value = mock_account

    mock_link = MagicMock()
    mock_link.url = "https://connect.stripe.com/setup/test"
    mock_stripe.AccountLink.create.return_value = mock_link

    cookie = _session_cookie(org)
    response = client.post(
        "/account/api/connect-onboard",
        cookies={"session": cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["onboarding_url"] == "https://connect.stripe.com/setup/test"
    assert data["connect_id"] == "acct_test_123"


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_connect_onboard_existing(mock_get_user, mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(stripe_connect_id="acct_existing_123")
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.base_url = "https://agentgate.sh"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    mock_link = MagicMock()
    mock_link.url = "https://connect.stripe.com/setup/existing"
    mock_stripe.AccountLink.create.return_value = mock_link

    cookie = _session_cookie(org)
    response = client.post(
        "/account/api/connect-onboard",
        cookies={"session": cookie},
    )
    assert response.status_code == 200
    assert response.json()["onboarding_url"] == "https://connect.stripe.com/setup/existing"


def test_connect_onboard_no_auth():
    response = client.post("/account/api/connect-onboard")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Stripe Connect — status
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_connect_status_connected(mock_get_user, mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(stripe_connect_id="acct_test_456")
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    mock_account = MagicMock()
    mock_account.charges_enabled = True
    mock_account.payouts_enabled = True
    mock_account.details_submitted = True
    mock_stripe.Account.retrieve.return_value = mock_account

    cookie = _session_cookie(org)
    response = client.get(
        "/account/api/connect-status",
        cookies={"session": cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["connected"] is True
    assert data["payouts_enabled"] is True


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_connect_status_not_connected(mock_get_user, mock_db, mock_settings):
    org = _make_fake_org(stripe_connect_id=None)
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    cookie = _session_cookie(org)
    response = client.get(
        "/account/api/connect-status",
        cookies={"session": cookie},
    )
    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_connect_status_no_auth():
    response = client.get("/account/api/connect-status")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Stripe Connect — withdrawal
# ---------------------------------------------------------------------------


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_withdraw_success(mock_get_user, mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(balance=50.0, stripe_connect_id="acct_test_789")
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, mock_session = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    mock_account = MagicMock()
    mock_account.payouts_enabled = True
    mock_stripe.Account.retrieve.return_value = mock_account

    mock_transfer = MagicMock()
    mock_transfer.id = "tr_test_123"
    mock_stripe.Transfer.create.return_value = mock_transfer

    mock_balance = MagicMock()
    mock_balance.available = [MagicMock(currency="usd")]
    mock_stripe.Balance.retrieve.return_value = mock_balance

    cookie = _session_cookie(org)
    with patch(
        "agentgate.server.stripe_routes.process_withdrawal",
        new_callable=AsyncMock,
    ) as mock_proc:
        mock_proc.return_value = (True, None)
        response = client.post(
            "/account/api/withdraw",
            json={"amount": 20.0},
            cookies={"session": cookie},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["transfer_id"] == "tr_test_123"
    assert data["gross_amount"] == 20.0
    assert data["fee"] == 0.6  # 3% of 20
    assert data["net_amount"] == 19.4
    mock_proc.assert_awaited_once()
    mock_session.add.assert_called()
    mock_session.commit.assert_called()


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_withdraw_insufficient_balance(mock_get_user, mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(balance=5.0, stripe_connect_id="acct_test_789")
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    mock_account = MagicMock()
    mock_account.payouts_enabled = True
    mock_stripe.Account.retrieve.return_value = mock_account

    cookie = _session_cookie(org)
    with patch(
        "agentgate.server.stripe_routes.process_withdrawal",
        new_callable=AsyncMock,
    ) as mock_proc:
        mock_proc.return_value = (False, "Insufficient balance: 5.0000 < 20.0000")
        response = client.post(
            "/account/api/withdraw",
            json={"amount": 20.0},
            cookies={"session": cookie},
        )
    assert response.status_code == 400
    assert "Insufficient" in response.json()["detail"]


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.stripe")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_withdraw_no_connect(mock_get_user, mock_db, mock_stripe, mock_settings):
    org = _make_fake_org(balance=50.0, stripe_connect_id=None)
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    cookie = _session_cookie(org)
    response = client.post(
        "/account/api/withdraw",
        json={"amount": 20.0},
        cookies={"session": cookie},
    )
    assert response.status_code == 400
    assert "No Stripe account" in response.json()["detail"]


@patch("agentgate.server.stripe_routes.settings")
@patch("agentgate.server.stripe_routes.async_session")
@patch("agentgate.server.account_routes.get_current_user")
def test_withdraw_below_minimum(mock_get_user, mock_db, mock_settings):
    org = _make_fake_org(balance=50.0, stripe_connect_id="acct_test")
    mock_get_user.return_value = org
    mock_settings.stripe_secret_key = "sk_test_123"
    mock_settings.stripe_connect_withdrawal_fee_pct = 0.03
    mock_settings.stripe_connect_min_withdrawal = 10.0
    db_factory, _ = _mock_db_returning(org)
    mock_db.side_effect = db_factory.side_effect
    mock_db.return_value = db_factory.return_value

    cookie = _session_cookie(org)
    response = client.post(
        "/account/api/withdraw",
        json={"amount": 5.0},
        cookies={"session": cookie},
    )
    assert response.status_code == 400
    assert "Minimum" in response.json()["detail"]


def test_withdraw_no_auth():
    response = client.post("/account/api/withdraw", json={"amount": 20.0})
    assert response.status_code == 401
