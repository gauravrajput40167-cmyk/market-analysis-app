"""
Razorpay checkout for the Market Analysis App.

Keys are read from environment variables or Streamlit secrets. They are never
hard-coded. Live mode is used only when RAZORPAY_MODE=live, RAZORPAY_LIVE_ENABLED
is true, and the key id starts with rzp_live_. Otherwise checkout stays in test mode.

A VIP subscription is activated only after the signature, order, amount,
currency, captured status, and active-account checks all succeed.
"""

import hashlib
import hmac
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv

try:
    import razorpay
    RAZORPAY_AVAILABLE = True
except ImportError:
    razorpay = None
    RAZORPAY_AVAILABLE = False

import auth
from database import get_connection, initialize_database, log_activity, log_security_event


logger = logging.getLogger("marketapp.payments")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

PLAN_NAME = "VIP 5-Day Pass"
PLAN_PRICE_RUPEES = 2
PLAN_PRICE_PAISE = 200
PLAN_DURATION_DAYS = 5
CURRENCY = "INR"
DEFAULT_PLAN_ID = "vip_pass"


def _read_setting(key):
    """Read a secret from the environment first, then Streamlit secrets."""
    value = (os.getenv(key) or "").strip()
    if value:
        return value
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        return ""
    return ""


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_gateway_config():
    """Resolve the effective Razorpay mode without exposing the key secret."""
    requested = (_read_setting("RAZORPAY_MODE") or "test").strip().lower()
    live_enabled = _truthy(_read_setting("RAZORPAY_LIVE_ENABLED"))
    test_key_id = _read_setting("RAZORPAY_TEST_KEY_ID")
    test_key_secret = _read_setting("RAZORPAY_TEST_KEY_SECRET")
    live_key_id = _read_setting("RAZORPAY_LIVE_KEY_ID")
    live_key_secret = _read_setting("RAZORPAY_LIVE_KEY_SECRET")
    generic_key_id = _read_setting("RAZORPAY_KEY_ID")
    generic_key_secret = _read_setting("RAZORPAY_KEY_SECRET")

    if not test_key_id and generic_key_id.startswith("rzp_test_"):
        test_key_id = generic_key_id
        test_key_secret = generic_key_secret
    if not live_key_id and generic_key_id.startswith("rzp_live_"):
        live_key_id = generic_key_id
        live_key_secret = generic_key_secret

    live_requested = requested == "live"
    live_ready = (
        live_requested
        and live_enabled
        and live_key_id.startswith("rzp_live_")
        and bool(live_key_secret)
    )
    if live_ready:
        return {
            "mode": "live",
            "live_blocked": False,
            "key_id": live_key_id,
            "key_secret": live_key_secret,
            "webhook_secret": _read_setting("RAZORPAY_WEBHOOK_SECRET"),
            "configured": True,
        }
    return {
        "mode": "test",
        "live_blocked": live_requested and not live_ready,
        "key_id": test_key_id,
        "key_secret": test_key_secret,
        "webhook_secret": _read_setting("RAZORPAY_WEBHOOK_SECRET"),
        "configured": bool(test_key_id and test_key_secret),
    }


def get_single_plan(plan_id=DEFAULT_PLAN_ID):
    """Return the server-controlled plan. Client prices are ignored."""
    initialize_database()
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT plan_id, name, amount_rupees, duration_days, currency, active, description
            FROM subscription_plans
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
    finally:
        connection.close()
    if row and int(row["active"] or 0) == 1:
        amount = Decimal(str(row["amount_rupees"])).quantize(Decimal("0.01"))
        paise = int(amount * 100)
        return {
            "id": row["plan_id"],
            "name": row["name"],
            "amount": amount,
            "amount_paise": paise,
            "duration_days": int(row["duration_days"]),
            "currency": (row["currency"] or CURRENCY).upper(),
            "mode": get_gateway_config()["mode"],
            "description": row["description"] or "",
        }
    if plan_id != DEFAULT_PLAN_ID:
        raise ValueError("That subscription plan is not available.")
    return {
        "id": DEFAULT_PLAN_ID,
        "name": PLAN_NAME,
        "amount": Decimal(PLAN_PRICE_RUPEES),
        "amount_paise": PLAN_PRICE_PAISE,
        "duration_days": PLAN_DURATION_DAYS,
        "currency": CURRENCY,
        "mode": get_gateway_config()["mode"],
        "description": "",
    }


def is_live_mode() -> bool:
    return get_gateway_config()["mode"] == "live"


def _safe_log_activity(username, action, details):
    try:
        log_activity(username, action, details)
    except Exception:
        logger.warning("Activity log unavailable for %s", action)


def _safe_log_security_event(username, event_type, details, severity="INFO"):
    try:
        log_security_event(username, event_type, details, severity)
    except Exception:
        logger.warning("Security log unavailable for %s", event_type)


def verify_checkout_signature(order_id, payment_id, signature, secret=None) -> bool:
    """HMAC-SHA256 of order_id|payment_id using the key secret. Secret is never logged."""
    config = get_gateway_config()
    secret = secret if secret is not None else config["key_secret"]
    if not secret or not order_id or not payment_id or not signature:
        return False
    message = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature).strip())


def verify_webhook_signature(body, signature) -> bool:
    """Verify a Razorpay webhook body. This does not activate a subscription by itself."""
    secret = get_gateway_config().get("webhook_secret") or ""
    if not secret or not signature:
        return False
    if isinstance(body, str):
        body = body.encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature).strip())


def get_razorpay_client():
    """Return a Razorpay client for the effective mode, or raise a clear error."""
    if not RAZORPAY_AVAILABLE:
        raise RuntimeError("Razorpay SDK is not installed. Install it with: pip install razorpay")
    config = get_gateway_config()
    if config["mode"] == "live":
        if not config["key_id"].startswith("rzp_live_"):
            raise RuntimeError("Live mode requires a Razorpay key that starts with rzp_live_.")
    else:
        if config["key_id"].startswith("rzp_live_"):
            raise RuntimeError("Refusing to use a live Razorpay key while checkout is in test mode.")
        if config["key_id"] and not config["key_id"].startswith("rzp_test_"):
            raise RuntimeError("Test mode requires a Razorpay key that starts with rzp_test_.")
    if not config["configured"]:
        raise RuntimeError(
            "Razorpay credentials are missing. Set RAZORPAY_TEST_KEY_ID and "
            "RAZORPAY_TEST_KEY_SECRET in the environment or Streamlit secrets."
        )
    return razorpay.Client(auth=(config["key_id"], config["key_secret"]))


def ensure_user_in_database(connection, username: str) -> str:
    """Validate an existing active account. Never inserts users or reactivates them."""
    clean_username = str(username or "").strip()
    if not clean_username:
        raise ValueError("Please sign in before starting checkout.")
    user_row = connection.execute(
        """
        SELECT username, active
        FROM users
        WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
        """,
        (clean_username,),
    ).fetchone()
    if not user_row:
        raise ValueError("Account not found. Please register or sign in again.")
    if int(user_row["active"] or 0) != 1:
        raise ValueError("This account is disabled. Contact the administrator.")
    return user_row["username"]


def _public_error(error):
    if isinstance(error, (ValueError, RuntimeError)):
        return str(error)
    logger.exception("Payment gateway error")
    return "Payment could not be processed. Check server logs and Razorpay credentials."


def create_checkout_order(username: str, plan_id: str = DEFAULT_PLAN_ID, amount_rupees=None) -> dict:
    """Create a Razorpay order. The server chooses the amount from the saved plan."""
    connection = None
    try:
        plan = get_single_plan(plan_id)
        if amount_rupees is not None:
            supplied = Decimal(str(amount_rupees)).quantize(Decimal("0.01"))
            if supplied != Decimal(str(plan["amount"])).quantize(Decimal("0.01")):
                raise ValueError("The requested amount does not match the plan price.")
        config = get_gateway_config()
        client = get_razorpay_client()
        connection = get_connection()
        active_username = ensure_user_in_database(connection, username)
        connection.execute(
            """
            UPDATE payments
            SET payment_status = 'cancelled',
                failure_reason = 'Replaced by a newer checkout order',
                provider_status = 'cancelled'
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
              AND payment_status = 'pending'
            """,
            (active_username,),
        )
        receipt_id = f"{config['mode']}_vip_{uuid.uuid4().hex[:16]}"
        order = client.order.create({
            "amount": int(plan["amount_paise"]),
            "currency": plan["currency"],
            "receipt": receipt_id,
            "notes": {
                "username": active_username,
                "plan": plan["name"],
                "plan_id": plan["id"],
                "environment": config["mode"],
            },
        })
        order_id = str(order.get("id") or "").strip()
        if not order_id:
            raise RuntimeError("Razorpay did not return a valid order ID.")
        cursor = connection.execute(
            """
            INSERT INTO payments (
                username, payment_status, amount, razorpay_order_id,
                plan_name, plan_id, currency, live_mode, provider_status
            ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, 'created')
            """,
            (
                active_username,
                str(plan["amount"]),
                order_id,
                plan["name"],
                plan["id"],
                plan["currency"],
                1 if config["mode"] == "live" else 0,
            ),
        )
        payment_record_id = cursor.lastrowid
        connection.commit()
        _safe_log_activity(
            active_username,
            "PAYMENT_ORDER_CREATED",
            f"{config['mode']} order {order_id} created for {plan['name']}.",
        )
        return {
            "success": True,
            "key_id": config["key_id"],
            "order_id": order_id,
            "amount": int(plan["amount_paise"]),
            "amount_rupees": str(plan["amount"]),
            "currency": plan["currency"],
            "plan_name": plan["name"],
            "plan_id": plan["id"],
            "duration_days": plan["duration_days"],
            "payment_record_id": payment_record_id,
            "mode": config["mode"],
            "live_blocked": config["live_blocked"],
        }
    except Exception as error:
        if connection:
            connection.rollback()
        logger.warning("create_checkout_order failed: %s", type(error).__name__)
        return {"success": False, "message": _public_error(error)}
    finally:
        if connection:
            connection.close()


def verify_checkout_payment(
    username: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    plan_id: str = DEFAULT_PLAN_ID,
) -> dict:
    """Verify signature and provider state. VIP starts only after every check passes."""
    connection = None
    try:
        username = str(username or "").strip()
        order_id = str(razorpay_order_id or "").strip()
        payment_id = str(razorpay_payment_id or "").strip()
        signature = str(razorpay_signature or "").strip()
        if not all((username, order_id, payment_id, signature)):
            raise ValueError("Incomplete payment verification details.")

        config = get_gateway_config()
        if not verify_checkout_signature(order_id, payment_id, signature, config["key_secret"]):
            _safe_log_security_event(username, "PAYMENT_SIGNATURE_REJECTED", f"Order {order_id}", "WARNING")
            raise ValueError("Payment signature verification failed. Subscription was not activated.")

        client = get_razorpay_client()
        try:
            client.utility.verify_payment_signature({
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            })
        except Exception:
            _safe_log_security_event(username, "PAYMENT_SIGNATURE_REJECTED", f"Order {order_id}", "WARNING")
            raise ValueError("Payment signature verification failed. Subscription was not activated.")

        provider_payment = client.payment.fetch(payment_id)
        provider_status = str(provider_payment.get("status") or "").lower()
        if str(provider_payment.get("order_id") or "") != order_id:
            raise ValueError("The provider payment does not belong to this order.")
        if provider_status != "captured":
            if provider_status in {"failed"}:
                mark_checkout_payment_failed(username, order_id, payment_id, "Provider status is failed.")
            raise ValueError("Payment is not captured. Subscription was not activated.")

        connection = get_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass

        payment_record = connection.execute(
            """
            SELECT id, username, amount, currency, payment_status, razorpay_payment_id, plan_name, plan_id
            FROM payments
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
              AND razorpay_order_id = ?
            """,
            (username, order_id),
        ).fetchone()
        if not payment_record:
            raise ValueError("No matching payment order was found for this account.")
        if payment_record["payment_status"] == "success":
            if payment_record["razorpay_payment_id"] == payment_id:
                connection.commit()
                return {"success": True, "message": "This payment was already verified."}
            raise ValueError("This order has already been verified with another payment ID.")
        if payment_record["payment_status"] != "pending":
            raise ValueError("This order is not pending and cannot be verified.")

        user_row = connection.execute(
            """
            SELECT username, active FROM users
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
            """,
            (username,),
        ).fetchone()
        if not user_row or int(user_row["active"] or 0) != 1:
            raise ValueError("The account does not exist or is disabled.")

        reused = connection.execute(
            """
            SELECT id FROM payments
            WHERE razorpay_payment_id = ? AND id != ?
            LIMIT 1
            """,
            (payment_id, payment_record["id"]),
        ).fetchone()
        if reused:
            raise ValueError("This payment ID is already linked to another order.")

        try:
            provider_amount = int(provider_payment.get("amount"))
        except (TypeError, ValueError):
            raise ValueError("The provider returned an invalid payment amount.")
        stored_amount = Decimal(str(payment_record["amount"])).quantize(Decimal("0.01"))
        stored_paise = int(stored_amount * 100)
        if provider_amount != stored_paise or stored_paise <= 0:
            raise ValueError("Payment amount does not match the stored order.")
        stored_currency = str(payment_record["currency"] or CURRENCY).upper()
        if str(provider_payment.get("currency") or "").upper() != stored_currency:
            raise ValueError("Payment currency does not match the order.")

        plan_id_for_order = payment_record["plan_id"] or plan_id or DEFAULT_PLAN_ID
        plan_row = connection.execute(
            "SELECT name, duration_days FROM subscription_plans WHERE plan_id = ?",
            (plan_id_for_order,),
        ).fetchone()
        duration_days = int(plan_row["duration_days"]) if plan_row else PLAN_DURATION_DAYS
        plan_name = payment_record["plan_name"] or (plan_row["name"] if plan_row else PLAN_NAME)

        now_utc = datetime.now(timezone.utc)
        start_time_iso = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        expiry_time_iso = (now_utc + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")

        current_payment = connection.execute(
            "SELECT payment_status, razorpay_payment_id FROM payments WHERE id = ?",
            (payment_record["id"],),
        ).fetchone()
        if not current_payment or current_payment["payment_status"] != "pending":
            raise ValueError("This order was already processed. Refresh and check its status.")

        connection.execute(
            """
            INSERT OR IGNORE INTO payment_attempts (
                payment_id, razorpay_payment_id, status, updated_at
            ) VALUES (?, ?, 'captured', ?)
            """,
            (payment_record["id"], payment_id, start_time_iso),
        )
        payment_update = connection.execute(
            """
            UPDATE payments
            SET payment_status = 'success',
                transaction_id = ?,
                razorpay_payment_id = ?,
                verified_at = ?,
                provider_status = 'captured',
                failure_reason = NULL
            WHERE id = ? AND payment_status = 'pending'
            """,
            (payment_id, payment_id, start_time_iso, payment_record["id"]),
        )
        if payment_update.rowcount != 1:
            raise ValueError("Payment record could not be safely updated.")
        user_update = connection.execute(
            """
            UPDATE users
            SET subscription = 1,
                subscription_plan = ?,
                subscription_start = ?,
                subscription_expires_at = ?
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
              AND active = 1
            """,
            (plan_name, start_time_iso, expiry_time_iso, username),
        )
        if user_update.rowcount != 1:
            raise ValueError("Active account could not be updated.")
        connection.commit()

        canonical = user_row["username"]
        if canonical in getattr(auth, "USER_DATA", {}):
            auth.USER_DATA[canonical]["subscription"] = True
            auth.USER_DATA[canonical]["subscription_plan"] = plan_name
            auth.USER_DATA[canonical]["subscription_expires_at"] = expiry_time_iso

        _safe_log_activity(
            canonical,
            "SUBSCRIPTION_ACTIVATED",
            f"Verified {config['mode']} payment. Access expires at {expiry_time_iso} UTC.",
        )
        _safe_log_security_event(
            canonical,
            "PAYMENT_VERIFIED_SUCCESS",
            "Signature, order, amount, currency, captured status, and account status verified.",
        )
        try:
            from finance import notify
            notify(
                canonical,
                "VIP access activated",
                f"{plan_name} is active until {expiry_time_iso} UTC after a verified payment.",
                "payment",
            )
        except Exception:
            logger.warning("Payment notification was not saved")
        return {
            "success": True,
            "message": f"Payment verified. {plan_name} is active until {expiry_time_iso[:10]} UTC.",
            "start_time": start_time_iso,
            "expiry_time": expiry_time_iso,
        }
    except Exception as error:
        if connection:
            connection.rollback()
        logger.warning("verify_checkout_payment failed: %s", type(error).__name__)
        return {"success": False, "message": _public_error(error)}
    finally:
        if connection:
            connection.close()


def _set_open_order_status(username, razorpay_order_id, status, razorpay_payment_id, reason):
    if status not in {"failed", "cancelled", "pending"}:
        raise ValueError("Unsupported payment status.")
    username = str(username or "").strip()
    order_id = str(razorpay_order_id or "").strip()
    payment_id = str(razorpay_payment_id or "").strip() or None
    if not username or not order_id:
        raise ValueError("Username and order ID are required.")
    initialize_database()
    connection = get_connection()
    try:
        record = connection.execute(
            """
            SELECT id, payment_status
            FROM payments
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
              AND razorpay_order_id = ?
            """,
            (username, order_id),
        ).fetchone()
        if not record:
            raise ValueError("Payment order not found.")
        if record["payment_status"] == "success":
            raise ValueError("A successful payment cannot change to this status.")
        if record["payment_status"] == status:
            return {"success": True, "message": f"Payment is already marked {status}."}
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        attempt_status = "failed" if status in {"failed", "cancelled"} else "pending"
        connection.execute(
            """
            INSERT OR IGNORE INTO payment_attempts (
                payment_id, razorpay_payment_id, status, failure_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (record["id"], payment_id, attempt_status, reason[:300], stamp),
        )
        cursor = connection.execute(
            """
            UPDATE payments
            SET payment_status = ?,
                razorpay_payment_id = COALESCE(?, razorpay_payment_id),
                failure_reason = ?,
                provider_status = ?
            WHERE id = ? AND payment_status = 'pending'
            """,
            (status, payment_id, reason[:300], status, record["id"]),
        )
        if cursor.rowcount != 1 and record["payment_status"] == "pending":
            raise ValueError("Payment status could not be updated.")
        connection.commit()
        _safe_log_activity(username, "PAYMENT_STATUS_UPDATED", f"Order {order_id} set to {status}.")
        return {"success": True, "message": f"Payment marked {status}. VIP access was not activated."}
    except Exception as error:
        connection.rollback()
        return {"success": False, "message": _public_error(error)}
    finally:
        connection.close()


def mark_checkout_payment_failed(username, razorpay_order_id, razorpay_payment_id="", reason="Checkout did not complete"):
    """Record a failed payment. This never activates VIP."""
    return _set_open_order_status(username, razorpay_order_id, "failed", razorpay_payment_id, reason or "Checkout failed")


def mark_checkout_payment_cancelled(username, razorpay_order_id, razorpay_payment_id="", reason="Checkout was cancelled"):
    """Record a cancelled checkout. This never activates VIP."""
    return _set_open_order_status(username, razorpay_order_id, "cancelled", razorpay_payment_id, reason or "Checkout cancelled")


def mark_checkout_payment_pending(username, razorpay_order_id, razorpay_payment_id="", reason="Payment is still pending"):
    """Keep an order pending when the provider has not captured it."""
    username = str(username or "").strip()
    order_id = str(razorpay_order_id or "").strip()
    if not username or not order_id:
        return {"success": False, "message": "Username and order ID are required."}
    initialize_database()
    connection = get_connection()
    try:
        record = connection.execute(
            """
            SELECT payment_status FROM payments
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) AND razorpay_order_id = ?
            """,
            (username, order_id),
        ).fetchone()
        if not record:
            return {"success": False, "message": "Payment order not found."}
        if record["payment_status"] == "success":
            return {"success": True, "message": "This payment is already verified."}
        connection.execute(
            """
            UPDATE payments
            SET provider_status = 'pending', failure_reason = ?
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
              AND razorpay_order_id = ?
              AND payment_status = 'pending'
            """,
            (reason[:300], username, order_id),
        )
        connection.commit()
        return {"success": True, "message": "Payment is pending. VIP access stays inactive until verification."}
    finally:
        connection.close()
