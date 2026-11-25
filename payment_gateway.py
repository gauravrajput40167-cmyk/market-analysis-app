"""
Secure, TEST-MODE-ONLY checkout integration for Market Prediction App.

Important:
- This module refuses live Razorpay keys and live payment mode.
- It never creates users or reactivates disabled accounts.
- A subscription is activated only after signature, order, amount, currency,
  captured status, database user status, and pending-order checks succeed.
"""

import os
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

from database import (
    get_connection,
    initialize_database,
    log_activity,
)

try:
    from database import log_security_event
except ImportError:
    log_security_event = None

import auth


# =========================================================
# ENVIRONMENT CONFIGURATION — TEST MODE ONLY
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Do not read LIVE-specific variables. A live key must never be used here.
_TEST_KEY_ID = (os.getenv("RAZORPAY_TEST_KEY_ID") or "").strip()
_TEST_KEY_SECRET = (os.getenv("RAZORPAY_TEST_KEY_SECRET") or "").strip()
_GENERIC_KEY_ID = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
_GENERIC_KEY_SECRET = (os.getenv("RAZORPAY_KEY_SECRET") or "").strip()

# Never combine a test key ID with a generic/live secret (or vice versa).
if _TEST_KEY_ID or _TEST_KEY_SECRET:
    RAZORPAY_KEY_ID = _TEST_KEY_ID
    RAZORPAY_KEY_SECRET = _TEST_KEY_SECRET
else:
    RAZORPAY_KEY_ID = _GENERIC_KEY_ID
    RAZORPAY_KEY_SECRET = _GENERIC_KEY_SECRET

PAYMENT_MODE = "test"
RAZORPAY_WEBHOOK_SECRET = ""  # Webhooks are not enabled in this module.


# =========================================================
# FIXED TEST PLAN
# =========================================================

PLAN_NAME = "VIP 5-Day Pass"
PLAN_PRICE_RUPEES = 2
PLAN_PRICE_PAISE = 200
PLAN_DURATION_DAYS = 5
CURRENCY = "INR"


def get_single_plan():
    """Return the fixed test plan shown by the dashboard."""
    return {
        "id": "vip_pass",
        "name": PLAN_NAME,
        "amount": PLAN_PRICE_RUPEES,
        "amount_paise": PLAN_PRICE_PAISE,
        "duration_days": PLAN_DURATION_DAYS,
        "original_price": 49,
        "discount_percent": 96,
        "currency": CURRENCY,
        "mode": PAYMENT_MODE,
    }


def is_live_mode() -> bool:
    """Live checkout is deliberately disabled."""
    return False


# =========================================================
# DATABASE COMPATIBILITY MIGRATION
# =========================================================

def _ensure_payment_columns(connection):
    """
    Add only the optional columns this module needs.
    Existing records are preserved; no database file is replaced.
    """
    required = {
        "payments": {
            "plan_name": "TEXT",
            "currency": "TEXT",
            "live_mode": "INTEGER NOT NULL DEFAULT 0",
        },
        "users": {
            "subscription_plan": "TEXT",
            "subscription_start": "TEXT",
            "subscription_expires_at": "TEXT",
        },
    }

    for table_name, columns in required.items():
        existing = {
            row["name"]
            for row in connection.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        }

        if not existing:
            raise RuntimeError(
                f"Database table '{table_name}' is missing. "
                "Run the project's database initialization first."
            )

        for column_name, column_type in columns.items():
            if column_name not in existing:
                connection.execute(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {column_name} {column_type}"
                )


def _initialize_payment_schema():
    initialize_database()
    connection = get_connection()
    try:
        _ensure_payment_columns(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# =========================================================
# SAFE LOGGING
# =========================================================

def _safe_log_activity(username, action, details):
    try:
        log_activity(username, action, details)
    except Exception:
        # A logging outage must not reverse a verified provider payment.
        print(f"[PaymentGateway] Activity log unavailable: {action}")


def _safe_log_security_event(username, event_type, details):
    if not callable(log_security_event):
        return
    try:
        log_security_event(username, event_type, details)
    except Exception:
        print(f"[PaymentGateway] Security log unavailable: {event_type}")


# =========================================================
# RAZORPAY CLIENT — TEST CREDENTIALS ONLY
# =========================================================

def get_razorpay_client():
    """Return a Razorpay client only when test credentials are configured."""
    if not RAZORPAY_AVAILABLE:
        raise RuntimeError(
            "Razorpay SDK is not installed. Install it with: pip install razorpay"
        )

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError(
            "Razorpay TEST credentials are missing. Configure "
            "RAZORPAY_TEST_KEY_ID and RAZORPAY_TEST_KEY_SECRET in local .env "
            "or the hosting platform's secrets settings."
        )

    if RAZORPAY_KEY_ID.startswith("rzp_live_"):
        raise RuntimeError(
            "Live Razorpay credentials are blocked. This project is configured "
            "for test mode only."
        )

    if not RAZORPAY_KEY_ID.startswith("rzp_test_"):
        raise RuntimeError(
            "Invalid Razorpay key. A test key beginning with 'rzp_test_' is required."
        )

    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# =========================================================
# USER VALIDATION — NO AUTO-CREATION OR AUTO-REACTIVATION
# =========================================================

def ensure_user_in_database(connection, username: str) -> str:
    """
    Backward-compatible helper name.
    It now only validates an existing active account.
    It never inserts users or changes account status.
    """
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


# =========================================================
# CREATE CHECKOUT ORDER — TEST MODE
# =========================================================

def create_checkout_order(
    username: str,
    plan_id: str = "vip_pass",
    amount_rupees: int = None,
) -> dict:
    """
    Create the fixed ₹2 Razorpay TEST order.
    The server controls the amount; client-supplied amounts are not trusted.
    """
    connection = None

    try:
        if plan_id != "vip_pass":
            raise ValueError("Invalid subscription plan.")

        if amount_rupees is not None:
            try:
                supplied_amount = Decimal(str(amount_rupees))
            except (InvalidOperation, ValueError):
                raise ValueError("Invalid amount.")
            if supplied_amount != Decimal(PLAN_PRICE_RUPEES):
                raise ValueError("The requested amount does not match the fixed plan.")

        _initialize_payment_schema()
        client = get_razorpay_client()

        connection = get_connection()
        active_username = ensure_user_in_database(connection, username)

        receipt_id = f"test_vip_{uuid.uuid4().hex[:16]}"
        order_payload = {
            "amount": PLAN_PRICE_PAISE,
            "currency": CURRENCY,
            "receipt": receipt_id,
            "notes": {
                "username": active_username,
                "plan": PLAN_NAME,
                "duration": f"{PLAN_DURATION_DAYS} days",
                "environment": "test",
            },
        }

        order = client.order.create(order_payload)
        order_id = str(order.get("id") or "").strip()

        if not order_id:
            raise RuntimeError("Razorpay did not return a valid order ID.")

        cursor = connection.execute(
            """
            INSERT INTO payments (
                username,
                payment_status,
                amount,
                razorpay_order_id,
                plan_name,
                currency,
                live_mode
            )
            VALUES (?, 'pending', ?, ?, ?, ?, 0)
            """,
            (
                active_username,
                str(PLAN_PRICE_RUPEES),
                order_id,
                PLAN_NAME,
                CURRENCY,
            ),
        )

        payment_record_id = cursor.lastrowid
        connection.commit()

        _safe_log_activity(
            active_username,
            "TEST_PAYMENT_ORDER_CREATED",
            f"Test order {order_id} created for the fixed plan.",
        )

        return {
            "success": True,
            "key_id": RAZORPAY_KEY_ID,
            "order_id": order_id,
            "amount": PLAN_PRICE_PAISE,
            "amount_rupees": PLAN_PRICE_RUPEES,
            "currency": CURRENCY,
            "plan_name": PLAN_NAME,
            "duration_days": PLAN_DURATION_DAYS,
            "payment_record_id": payment_record_id,
            "mode": "test",
        }

    except Exception as error:
        if connection:
            connection.rollback()
        print(f"[PaymentGateway] create_checkout_order failed: {type(error).__name__}")
        return {
            "success": False,
            "message": str(error) if isinstance(error, (ValueError, RuntimeError)) else
                       "Unable to create a test checkout order. Check server logs and test credentials.",
        }

    finally:
        if connection:
            connection.close()


# =========================================================
# VERIFY CHECKOUT PAYMENT
# =========================================================

def verify_checkout_payment(
    username: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    plan_id: str = "vip_pass",
) -> dict:
    """
    Verify the Razorpay signature and provider-side payment details.
    Only captured payments with exact amount/currency can activate access.
    """
    connection = None

    try:
        if plan_id != "vip_pass":
            raise ValueError("Invalid subscription plan.")

        username = str(username or "").strip()
        order_id = str(razorpay_order_id or "").strip()
        payment_id = str(razorpay_payment_id or "").strip()
        signature = str(razorpay_signature or "").strip()

        if not all((username, order_id, payment_id, signature)):
            raise ValueError("Incomplete payment verification details.")

        _initialize_payment_schema()
        client = get_razorpay_client()

        connection = get_connection()

        payment_record = connection.execute(
            """
            SELECT id, username, amount, payment_status, razorpay_payment_id
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
                return {
                    "success": True,
                    "message": "This payment was already verified.",
                }
            raise ValueError("This order has already been verified with another payment ID.")

        if payment_record["payment_status"] != "pending":
            raise ValueError("This order is not pending and cannot be verified.")

        # Verify the account remains active before checking out.
        user_row = connection.execute(
            """
            SELECT username, active
            FROM users
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
            """,
            (username,),
        ).fetchone()

        if not user_row or int(user_row["active"] or 0) != 1:
            raise ValueError("The account does not exist or is disabled.")

        # Check that this payment ID has not been attached to another order.
        reused_payment = connection.execute(
            """
            SELECT id
            FROM payments
            WHERE razorpay_payment_id = ?
              AND id != ?
            LIMIT 1
            """,
            (payment_id, payment_record["id"]),
        ).fetchone()

        if reused_payment:
            raise ValueError("This payment ID is already linked to another order.")

        # 1) Cryptographic signature verification.
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            }
        )

        # 2) Verify the provider's payment record.
        provider_payment = client.payment.fetch(payment_id)

        if str(provider_payment.get("order_id") or "") != order_id:
            raise ValueError("The provider payment does not belong to this order.")

        if str(provider_payment.get("status") or "").lower() != "captured":
            raise ValueError("Payment is not captured. Subscription was not activated.")

        try:
            provider_amount = int(provider_payment.get("amount"))
        except (TypeError, ValueError):
            raise ValueError("The provider returned an invalid payment amount.")

        if provider_amount != PLAN_PRICE_PAISE:
            raise ValueError("Payment amount does not match the fixed plan amount.")

        if str(provider_payment.get("currency") or "").upper() != CURRENCY:
            raise ValueError("Payment currency does not match the plan currency.")

        try:
            stored_amount_paise = int(
                Decimal(str(payment_record["amount"])) * Decimal("100")
            )
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("The stored order amount is invalid.")

        if stored_amount_paise != PLAN_PRICE_PAISE:
            raise ValueError("The stored order amount does not match the fixed plan.")

        # 3) Activate access atomically after all remote checks passed.
        now_utc = datetime.now(timezone.utc)
        start_time_iso = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        expiry_time_iso = (
            now_utc + timedelta(days=PLAN_DURATION_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")

        connection.execute("BEGIN IMMEDIATE")

        current_payment = connection.execute(
            """
            SELECT payment_status, razorpay_payment_id
            FROM payments
            WHERE id = ?
            """,
            (payment_record["id"],),
        ).fetchone()

        if not current_payment or current_payment["payment_status"] != "pending":
            raise ValueError("This order was already processed. Refresh and check its status.")

        current_user = connection.execute(
            """
            SELECT active
            FROM users
            WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
            """,
            (username,),
        ).fetchone()

        if not current_user or int(current_user["active"] or 0) != 1:
            raise ValueError("The account is no longer active.")

        connection.execute(
            """
            INSERT OR IGNORE INTO payment_attempts (
                payment_id,
                razorpay_payment_id,
                status,
                updated_at
            )
            VALUES (?, ?, 'captured', ?)
            """,
            (payment_record["id"], payment_id, start_time_iso),
        )

        payment_update = connection.execute(
            """
            UPDATE payments
            SET payment_status = 'success',
                transaction_id = ?,
                razorpay_payment_id = ?,
                verified_at = ?
            WHERE id = ?
              AND payment_status = 'pending'
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
            (PLAN_NAME, start_time_iso, expiry_time_iso, username),
        )

        if user_update.rowcount != 1:
            raise ValueError("Active account could not be updated.")

        connection.commit()

        # Update only an existing in-memory cache entry.
        if username in getattr(auth, "USER_DATA", {}):
            auth.USER_DATA[username]["subscription"] = True
            auth.USER_DATA[username]["subscription_plan"] = PLAN_NAME
            auth.USER_DATA[username]["subscription_expires_at"] = expiry_time_iso

        _safe_log_activity(
            username,
            "TEST_SUBSCRIPTION_ACTIVATED",
            f"Test payment verified. Access expires at {expiry_time_iso} UTC.",
        )
        _safe_log_security_event(
            username,
            "TEST_PAYMENT_VERIFIED_SUCCESS",
            "Signature, order, amount, currency, captured status, and account status verified.",
        )

        return {
            "success": True,
            "message": (
                "Test payment verified. Test subscription activated for 5 days "
                f"(expires: {expiry_time_iso[:10]} UTC)."
            ),
            "start_time": start_time_iso,
            "expiry_time": expiry_time_iso,
        }

    except Exception as error:
        if connection:
            connection.rollback()
        print(f"[PaymentGateway] verify_checkout_payment failed: {type(error).__name__}")
        return {
            "success": False,
            "message": str(error) if isinstance(error, (ValueError, RuntimeError)) else
                       "Payment verification failed. No subscription was activated.",
        }

    finally:
        if connection:
            connection.close()


# =========================================================
# RECORD CHECKOUT FAILURE
# =========================================================

def mark_checkout_payment_failed(
    username: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
) -> dict:
    """Record a failed checkout attempt for an existing pending order."""
    connection = None

    try:
        username = str(username or "").strip()
        order_id = str(razorpay_order_id or "").strip()
        payment_id = str(razorpay_payment_id or "").strip() or None

        if not username or not order_id:
            raise ValueError("Username and order ID are required.")

        _initialize_payment_schema()
        connection = get_connection()

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
            raise ValueError("A successful payment cannot be marked as failed.")

        failed_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        connection.execute(
            """
            INSERT OR IGNORE INTO payment_attempts (
                payment_id,
                razorpay_payment_id,
                status,
                failure_reason,
                updated_at
            )
            VALUES (?, ?, 'failed', 'Checkout did not complete', ?)
            """,
            (record["id"], payment_id, failed_time),
        )

        connection.commit()

        _safe_log_activity(
            username,
            "TEST_PAYMENT_ATTEMPT_FAILED",
            f"Test checkout attempt recorded for order {order_id}.",
        )

        return {"success": True, "message": "Failed test checkout attempt recorded."}

    except Exception as error:
        if connection:
            connection.rollback()
        return {
            "success": False,
            "message": str(error) if isinstance(error, (ValueError, RuntimeError)) else
                       "Could not record the failed checkout attempt.",
        }

    finally:
        if connection:
            connection.close()
