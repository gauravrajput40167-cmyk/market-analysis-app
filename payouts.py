"""
Withdrawal payout rail.

Real money movement stays off until business authorization, provider approval,
KYC/AML review, and financial safeguards are explicitly configured. Until then
every payout is a labelled demo disbursement with an HMAC reference that can
be checked again later. This module never calls a bank or payout API.
"""

import hashlib
import hmac
import os
import secrets

from database import get_app_configurations, get_connection, initialize_database


DEMO_PAYOUT_CONFIG_KEY = "demo_payout_secret"


def live_payouts_enabled() -> bool:
    """True only when an operator explicitly arms live payouts. Default is off."""
    flag = (os.getenv("PAYOUT_LIVE_ENABLED") or "").strip().lower()
    provider = (os.getenv("PAYOUT_PROVIDER") or "").strip().lower()
    kyc = (os.getenv("PAYOUT_KYC_AML_APPROVED") or "").strip().lower()
    safeguards = (os.getenv("PAYOUT_FINANCIAL_SAFEGUARDS") or "").strip().lower()
    truthy = {"1", "true", "yes"}
    return (
        flag in truthy
        and provider in {"razorpayx", "authorized"}
        and kyc in truthy
        and safeguards in truthy
    )


def payout_mode_label() -> str:
    if live_payouts_enabled():
        return "live-blocked"
    return "demo"


def live_payout_block_reason() -> str:
    return (
        "Live payouts are disabled. A real disbursement requires business "
        "authorization, payout-provider approval, KYC/AML clearance, and "
        "financial safeguards. This build will not send funds."
    )


def get_demo_payout_secret() -> str:
    """Return the server-side demo HMAC secret, creating it once if needed."""
    env_secret = (os.getenv("DEMO_PAYOUT_SECRET") or "").strip()
    if env_secret:
        return env_secret
    initialize_database()
    existing = get_app_configurations().get(DEMO_PAYOUT_CONFIG_KEY, {})
    current = str(existing.get("config_value") or "").strip()
    if current:
        return current
    generated = secrets.token_hex(32)
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO app_configurations(config_key, config_value, updated_by, updated_at)
            VALUES (?, ?, 'system', CURRENT_TIMESTAMP)
            ON CONFLICT(config_key) DO NOTHING
            """,
            (DEMO_PAYOUT_CONFIG_KEY, generated),
        )
        connection.commit()
    finally:
        connection.close()
    stored = get_app_configurations().get(DEMO_PAYOUT_CONFIG_KEY, {})
    return str(stored.get("config_value") or generated)


def build_demo_reference(withdrawal_id, username, amount) -> str:
    message = f"{int(withdrawal_id)}|{str(username).strip()}|{amount}|demo"
    digest = hmac.new(
        get_demo_payout_secret().encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"DEMO-{digest[:24]}"


def verify_demo_reference(withdrawal_id, username, amount, reference) -> bool:
    if not isinstance(reference, str) or not reference.startswith("DEMO-"):
        return False
    expected = build_demo_reference(withdrawal_id, username, amount)
    return hmac.compare_digest(expected, reference)


def authorized_payout(withdrawal_id, username, amount) -> dict:
    """
    Produce a verified payout reference.

    When live flags are present this still refuses to move money. The demo
    path returns a reference that must be stored with the withdrawal.
    """
    if live_payouts_enabled():
        return {
            "ok": False,
            "mode": "live-blocked",
            "message": live_payout_block_reason(),
        }
    reference = build_demo_reference(withdrawal_id, username, amount)
    if not verify_demo_reference(withdrawal_id, username, amount, reference):
        return {"ok": False, "mode": "demo", "message": "Demo payout reference could not be verified."}
    return {
        "ok": True,
        "mode": "demo",
        "reference": reference,
        "message": "Demo payout reference verified. No real funds were sent.",
    }
