# ruff: noqa: E501

"""Transactional email via Resend.

Wraps Resend so the rest of the codebase doesn't know about the provider.
If `settings.resend_api_key` is empty, send_email() is a no-op that logs
a warning — useful for local dev and for deployments that haven't
configured email yet. Auth flows that depend on email MUST gate on
`email_is_configured()` before claiming a send succeeded.
"""

from __future__ import annotations

import logging
from typing import Any

import resend

from agentgate.core.config import settings

logger = logging.getLogger("agentgate.email")


def email_is_configured() -> bool:
    return bool(settings.resend_api_key)


def _client_ready() -> bool:
    if not settings.resend_api_key:
        return False
    resend.api_key = settings.resend_api_key
    return True


def send_email(
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
) -> dict[str, Any] | None:
    """Send a transactional email. Returns the Resend response dict or None
    if email isn't configured."""
    if not _client_ready():
        logger.warning("send_email skipped: RESEND_API_KEY not set (to=%s)", to)
        return None

    params: dict[str, Any] = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        params["text"] = text
    try:
        resp = resend.Emails.send(params)
        logger.info("Email sent to %s (subject=%r id=%s)", to, subject, resp.get("id"))
        return resp
    except Exception:
        logger.exception("Resend send failed for %s", to)
        return None


# -- Templates --------------------------------------------------------------


def send_verification_email(to: str, verify_url: str) -> bool:
    html = f"""\
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;color:#e5e5e5;background:#0a0a0a;">
  <h1 style="font-size:22px;margin:0 0 16px;">Confirm your AgentGate email</h1>
  <p style="color:#aaa;line-height:1.6;">Click the button below to verify <strong style="color:#fff;">{to}</strong> and finish setting up your AgentGate account.</p>
  <p style="margin:28px 0;">
    <a href="{verify_url}" style="display:inline-block;background:#3b82f6;color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600;">Verify email</a>
  </p>
  <p style="color:#777;font-size:13px;">Or paste this link into your browser: <br><span style="color:#aaa;word-break:break-all;">{verify_url}</span></p>
  <p style="color:#666;font-size:12px;margin-top:32px;">If you didn't sign up for AgentGate, just ignore this email. The link expires in 24 hours.</p>
</div>
"""
    text = (
        f"Confirm your AgentGate email: {verify_url}\n\n"
        "If you didn't sign up, ignore this. The link expires in 24 hours."
    )
    return send_email(to, "Verify your AgentGate email", html, text) is not None


def send_password_reset_email(to: str, reset_url: str) -> bool:
    html = f"""\
<div style="font-family:system-ui,-apple-system,sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;color:#e5e5e5;background:#0a0a0a;">
  <h1 style="font-size:22px;margin:0 0 16px;">Reset your AgentGate password</h1>
  <p style="color:#aaa;line-height:1.6;">Someone (hopefully you) asked to reset the password for <strong style="color:#fff;">{to}</strong>. Click the button to set a new one.</p>
  <p style="margin:28px 0;">
    <a href="{reset_url}" style="display:inline-block;background:#3b82f6;color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600;">Reset password</a>
  </p>
  <p style="color:#777;font-size:13px;">Or paste this link into your browser: <br><span style="color:#aaa;word-break:break-all;">{reset_url}</span></p>
  <p style="color:#666;font-size:12px;margin-top:32px;">If you didn't ask for this, ignore the email &mdash; your password won't change. The link expires in 1 hour.</p>
</div>
"""
    text = (
        f"Reset your AgentGate password: {reset_url}\n\n"
        "If you didn't ask for this, ignore the email. The link expires in 1 hour."
    )
    return send_email(to, "Reset your AgentGate password", html, text) is not None
