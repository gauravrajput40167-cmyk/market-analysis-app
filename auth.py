import hashlib
import hmac
import secrets
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from database import (
    get_connection,
    initialize_database,
    log_activity,
    log_security_event
)
# =========================================================
# PASSWORD SECURITY & CRYPTOGRAPHY
# =========================================================
def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 600,000 iterations and a random salt."""
    if not isinstance(password, str):
        password = str(password)
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        600000
    ).hex()
    return f"pbkdf2_sha256${salt}${hashed}"
def verify_password(password: str, stored_password: str) -> bool:
    """Verify password against stored PBKDF2 hash, with automatic legacy SHA-256 fallback."""
    if not isinstance(password, str) or not isinstance(stored_password, str):
        return False
    # Legacy SHA-256 support
    if re.fullmatch(r"[0-9a-f]{64}", stored_password):
        legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy_hash, stored_password)
    parts = stored_password.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    _, salt, expected_hash = parts
    actual_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        600000
    ).hex()
    return hmac.compare_digest(actual_hash, expected_hash)
def validate_password(password: str) -> bool:
    """Validate password strength (minimum 8 characters)."""
    return isinstance(password, str) and len(password) >= 8

DISPOSABLE_EMAIL_DOMAINS = {
    "tempmail.com", "mailinator.com", "10minutemail.com", "guerrillamail.com",
    "yopmail.com", "trashmail.com", "fakeinbox.com", "sharklasers.com",
    "throwawaymail.com", "getnada.com", "dispostable.com", "maildrop.cc",
    "temp-mail.org", "mohmal.com", "example.com", "test.com"
}

def is_valid_real_email(email: str) -> bool:
    if not re.fullmatch(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", email):
        return False
    domain = email.split("@", 1)[1].lower()
    if domain in DISPOSABLE_EMAIL_DOMAINS:
        return False
    return True

def is_valid_real_mobile(mobile: str) -> bool:
    digits = re.sub(r"\D", "", mobile)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10:
        return False
    if digits[0] not in "6789":
        return False
    if len(set(digits)) <= 2:
        return False
    if digits in {"1234567890", "0123456789", "9876543210"}:
        return False
    return True

def _ensure_sessions_table(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            session_token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

def create_device_session(username: str, max_devices: int = 2):
    token = secrets.token_hex(16)
    with get_connection() as connection:
        _ensure_sessions_table(connection)
        rows = connection.execute(
            "SELECT id FROM user_sessions WHERE username = ? ORDER BY id ASC",
            (username,)
        ).fetchall()
        if len(rows) >= max_devices:
            return None
        connection.execute(
            "INSERT INTO user_sessions (username, session_token) VALUES (?, ?)",
            (username, token)
        )
        connection.commit()
    return token

def remove_device_session(username: str, token: str):
    if not username or not token:
        return
    try:
        with get_connection() as connection:
            _ensure_sessions_table(connection)
            connection.execute(
                "DELETE FROM user_sessions WHERE username = ? AND session_token = ?",
                (username, token)
            )
            connection.commit()
    except Exception:
        pass

# =========================================================
# DEMO USER & IN-MEMORY CACHE
# =========================================================
USERS = {}
USER_DATA = {}
PAYMENT_RECORDS = {}
WITHDRAWAL_RECORDS = {}
def _parse_timestamp(ts_str):
    """Safely parse UTC timestamp strings."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        cleaned = ts_str.strip().replace("T", " ")
        if len(cleaned) == 19:
            return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None
def load_users_from_database():
    """Load all registered users from SQLite into memory cache with subscription lifecycle."""
    try:
        initialize_database()
        with get_connection() as connection:
            rows = connection.execute("""
                SELECT
                    username,
                    email,
                    mobile,
                    password,
                    role,
                    subscription,
                    active,
                    subscription_plan,
                    subscription_start,
                    subscription_expires_at
                FROM users
            """).fetchall()
        USER_DATA.clear()
        for row in rows:
            keys = row.keys()
            USER_DATA[row["username"]] = {
                "email": row["email"],
                "mobile": row["mobile"],
                "password": row["password"],
                "role": row["role"],
                "subscription": bool(row["subscription"]),
                "active": bool(row["active"]),
                "subscription_plan": row["subscription_plan"] if "subscription_plan" in keys else "Free",
                "subscription_start": row["subscription_start"] if "subscription_start" in keys else None,
                "subscription_expires_at": row["subscription_expires_at"] if "subscription_expires_at" in keys else None,
            }
    except Exception as err:
        print(f"[Auth] load_users_from_database error: {err}")
# Safe initial user loading
try:
    load_users_from_database()
except Exception:
    pass
# =========================================================
# ACTIVITY & SECURITY AUDIT HELPERS
# =========================================================
def record_security_event(username, event_type, details=None, severity="INFO"):
    """Write security audit events without interrupting the authentication flow."""
    try:
        log_security_event(username, event_type, details, severity)
    except Exception:
        pass
def record_user_activity(username, action, details=None):
    """Save user activity records safely."""
    try:
        log_activity(username=username, action=action, details=details)
    except Exception:
        pass
# =========================================================
# AUTHENTICATION & LOGIN
# =========================================================
def login(username, password):
    """Authenticate user credentials against SQLite records and demo accounts."""
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    username = username.strip()
    if not username or not password:
        return False
    if username not in USER_DATA:
        load_users_from_database()
    # Registered SQLite users
    if username in USER_DATA:
        user = USER_DATA[username]
        if not user.get("active", False):
            record_security_event(username, "LOGIN_BLOCKED_INACTIVE", "Login blocked because account is disabled.", "WARNING")
            record_user_activity(username, "LOGIN_FAILED", "Disabled account access attempt.")
            return False
        stored_password = user.get("password", "")
        if verify_password(password, stored_password):
            # Upgrade legacy SHA-256 hash to PBKDF2 automatically
            if re.fullmatch(r"[0-9a-f]{64}", stored_password):
                upgraded_hash = hash_password(password)
                try:
                    with get_connection() as connection:
                        connection.execute("""
                            UPDATE users
                            SET password = ?
                            WHERE username = ?
                        """, (upgraded_hash, username))
                    user["password"] = upgraded_hash
                except Exception as db_err:
                    print(f"[Auth] Password upgrade error: {db_err}")
            # Check and enforce subscription expiry
            check_subscription(username)
            action = "ADMIN_LOGIN" if user.get("role") == "admin" else "USER_LOGIN"
            record_user_activity(username, action, "Authentication successful.")
            record_security_event(username, f"{action}_SUCCESS", "User session verified.")
            return True
        record_security_event(username, "LOGIN_FAILED", "Invalid password provided.", "WARNING")
        record_user_activity(username, "LOGIN_FAILED", "Invalid password.")
        return False
    # Built-in demo user
    if username == "user" and username in USERS:
        if verify_password(password, USERS[username]):
            record_user_activity(username, "USER_LOGIN", "Demo user logged in successfully.")
            record_security_event(username, "USER_LOGIN_SUCCESS", "Demo account authentication successful.")
            return True
        record_security_event(username, "LOGIN_FAILED", "Invalid demo account password.", "WARNING")
        record_user_activity(username, "LOGIN_FAILED", "Invalid demo password.")
        return False
    record_security_event(username or "Unknown", "LOGIN_FAILED", "Non-existent username.", "WARNING")
    record_user_activity(username or "Unknown", "LOGIN_FAILED", "Unknown username.")
    return False
def authenticate_user(username, password):
    """Authenticate and return comprehensive session payload."""
    if not isinstance(username, str):
        username = ""
    username = username.strip()
    if login(username, password):
        return {
            "username": username,
            "role": get_role(username),
            "logged_in": True,
            "subscription": check_subscription(username)
        }
    return {
        "username": "",
        "role": "",
        "logged_in": False,
        "subscription": False
    }
# =========================================================
# REGISTRATION & USER PROVISIONING
# =========================================================
def register_user(username, email, mobile, password):
    """Register a new user account with strict format validation and password hashing."""
    if not all(isinstance(val, str) for val in (username, email, mobile, password)):
        return False
    username = username.strip()
    email = email.strip().lower()
    mobile = mobile.strip()
    if not username or not email or not mobile:
        return False
    load_users_from_database()
    if username in USERS or username in USER_DATA:
        return False
    if not is_valid_real_email(email):
        return False
    if not is_valid_real_mobile(mobile):
        return False
    if not validate_password(password):
        return False
    for user in USER_DATA.values():
        if user["email"].lower() == email:
            return False
        if user["mobile"] == mobile:
            return False
    password_hash = hash_password(password)
    try:
        with get_connection() as connection:
            connection.execute("""
                INSERT INTO users (
                    username,
                    email,
                    mobile,
                    password,
                    role,
                    subscription,
                    active,
                    subscription_plan
                )
                VALUES (?, ?, ?, ?, 'user', 0, 1, 'Free')
            """, (
                username,
                email,
                mobile,
                password_hash
            ))
    except (sqlite3.IntegrityError, Exception):
        return False
    USER_DATA[username] = {
        "email": email,
        "mobile": mobile,
        "password": password_hash,
        "role": "user",
        "subscription": False,
        "active": True,
        "subscription_plan": "Free",
        "subscription_start": None,
        "subscription_expires_at": None
    }
    record_user_activity(username, "USER_REGISTER", "New user account registered successfully.")
    record_security_event(username, "USER_REGISTERED", "New account provisioned in SQLite.")
    return True
# =========================================================
# ROLE & STATUS MANAGEMENT
# =========================================================
def get_role(username):
    """Return user role ('user', 'admin', or None)."""
    if not isinstance(username, str):
        return None
    username = username.strip()
    if username not in USER_DATA:
        load_users_from_database()
    user = USER_DATA.get(username)
    if user:
        if not user.get("active", False):
            return None
        return user.get("role", "user")
    if username == "user" and username in USERS:
        return "user"
    return None
def get_user_role(username):
    return get_role(username)
def is_admin(username):
    return get_role(username) == "admin"
def get_user_status(username):
    """Check if account is active."""
    if username not in USER_DATA:
        load_users_from_database()
    if username in USER_DATA:
        return USER_DATA[username]["active"]
    return username == "user" and username in USERS
def is_user_active(username):
    return get_user_status(username)
def deactivate_user(username):
    """Disable user account access."""
    if username not in USER_DATA:
        load_users_from_database()
        if username not in USER_DATA:
            return False
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE users
            SET active = 0
            WHERE username = ?
        """, (username,))
        if cursor.rowcount != 1:
            return False
    USER_DATA[username]["active"] = False
    record_user_activity(username, "ACCOUNT_DEACTIVATED", "Account access disabled by administrator.")
    record_security_event(username, "ACCOUNT_DEACTIVATED", "Account access disabled.", "WARNING")
    return True
def activate_user(username):
    """Reactivate user account."""
    if username not in USER_DATA:
        load_users_from_database()
        if username not in USER_DATA:
            return False
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE users
            SET active = 1
            WHERE username = ?
        """, (username,))
        if cursor.rowcount != 1:
            return False
    USER_DATA[username]["active"] = True
    record_user_activity(username, "ACCOUNT_ACTIVATED", "Account activated.")
    record_security_event(username, "ACCOUNT_ACTIVATED", "Account access enabled.")
    return True
def get_user_counts():
    """Return counts of active vs deactivated accounts."""
    load_users_from_database()
    active = sum(1 for u in USER_DATA.values() if u["active"])
    deactivated = sum(1 for u in USER_DATA.values() if not u["active"])
    return {"active": active, "deactivated": deactivated}
def get_total_users():
    return len(USER_DATA)
# =========================================================
# SUBSCRIPTION LIFECYCLE & EXPIRY ENGINE
# =========================================================
def check_subscription(username: str) -> bool:
    """
    Verify subscription active state and enforce real-time expiration.
    If expiry timestamp has passed, automatically revoke subscription.
    """
    if not isinstance(username, str) or not username.strip():
        return False
    username = username.strip()
    with get_connection() as connection:
        row = connection.execute("""
            SELECT subscription, subscription_plan, subscription_start, subscription_expires_at
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()
    if row is None:
        return False
    is_subscribed = bool(row["subscription"])
    expires_at_str = row["subscription_expires_at"] if "subscription_expires_at" in row.keys() else None
    # Enforce real-time expiration check
    if is_subscribed and expires_at_str:
        exp_dt = _parse_timestamp(expires_at_str)
        now_dt = datetime.now(timezone.utc)
        if exp_dt and exp_dt < now_dt:
            # Subscription has expired! Revoke access
            with get_connection() as conn:
                conn.execute("""
                    UPDATE users
                    SET subscription = 0,
                        subscription_plan = 'Expired'
                    WHERE username = ?
                """, (username,))
            is_subscribed = False
            if username in USER_DATA:
                USER_DATA[username]["subscription"] = False
                USER_DATA[username]["subscription_plan"] = "Expired"
            record_user_activity(username, "SUBSCRIPTION_EXPIRED", f"Plan expired at {expires_at_str}.")
            return False
    if username in USER_DATA:
        USER_DATA[username]["subscription"] = is_subscribed
    return is_subscribed
def get_subscription_status(username):
    return check_subscription(username)
def get_user_subscription_info(username: str) -> dict:
    """
    Return comprehensive subscription telemetry:
    Plan name, start timestamp, expiry timestamp, days remaining, and status.
    """
    if not isinstance(username, str) or not username.strip():
        return {"status": "Inactive", "plan": "None", "is_active": False}
    username = username.strip()
    is_active = check_subscription(username)
    with get_connection() as connection:
        row = connection.execute("""
            SELECT subscription, subscription_plan, subscription_start, subscription_expires_at
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()
    if not row:
        return {"status": "Inactive", "plan": "None", "is_active": False}
    plan_name = row["subscription_plan"] or ("Pro Plan" if is_active else "Free")
    start_str = row["subscription_start"] or "N/A"
    exp_str = row["subscription_expires_at"]
    days_remaining = 0
    if exp_str:
        exp_dt = _parse_timestamp(exp_str)
        if exp_dt:
            diff = exp_dt - datetime.now(timezone.utc)
            days_remaining = max(0, diff.days)
    status_label = "Active" if is_active else ("Expired" if plan_name == "Expired" else "Inactive")
    return {
        "is_active": is_active,
        "plan": plan_name,
        "start_date": start_str,
        "expires_at": exp_str or "N/A",
        "days_remaining": days_remaining,
        "status": status_label
    }
def activate_subscription(username: str, plan_name: str = "Pro Monthly Plan", duration_days: int = 30):
    """Activate subscription with start and expiry timestamps in SQLite and cache."""
    if not isinstance(username, str) or not username.strip():
        return False
    username = username.strip()
    now_utc = datetime.now(timezone.utc)
    start_time = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    expiry_time = (now_utc + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE users
            SET subscription = 1,
                subscription_plan = ?,
                subscription_start = ?,
                subscription_expires_at = ?
            WHERE username = ?
        """, (plan_name, start_time, expiry_time, username))
        if cursor.rowcount != 1:
            return False
    if username in USER_DATA:
        USER_DATA[username]["subscription"] = True
        USER_DATA[username]["subscription_plan"] = plan_name
        USER_DATA[username]["subscription_start"] = start_time
        USER_DATA[username]["subscription_expires_at"] = expiry_time
    record_user_activity(username, "SUBSCRIPTION_ACTIVATED", f"Plan '{plan_name}' activated for {duration_days} days.")
    record_security_event(username, "SUBSCRIPTION_ACTIVATED", f"Active until {expiry_time}.")
    return True
def deactivate_subscription(username: str):
    """Deactivate subscription for user in SQLite and update cache."""
    if not isinstance(username, str) or not username.strip():
        return False
    username = username.strip()
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE users
            SET subscription = 0,
                subscription_plan = 'Cancelled'
            WHERE username = ?
        """, (username,))
        if cursor.rowcount != 1:
            return False
    if username in USER_DATA:
        USER_DATA[username]["subscription"] = False
        USER_DATA[username]["subscription_plan"] = "Cancelled"
    record_user_activity(username, "SUBSCRIPTION_DEACTIVATED", "Subscription manually cancelled.")
    record_security_event(username, "SUBSCRIPTION_DEACTIVATED", "Subscription access revoked.")
    return True
def get_user_account_summary(username):
    sub_info = get_user_subscription_info(username)
    return {
        "active": is_user_active(username),
        "subscription": sub_info["is_active"],
        "plan": sub_info["plan"],
        "days_remaining": sub_info["days_remaining"],
        "expires_at": sub_info["expires_at"]
    }
# =========================================================
# PAYMENT TRANSACTION RECORDS
# =========================================================
def _latest_payment_row(username):
    with get_connection() as connection:
        return connection.execute("""
            SELECT *
            FROM payments
            WHERE username = ?
            ORDER BY id DESC
            LIMIT 1
        """, (username,)).fetchone()
def _payment_row_to_dict(row):
    if row is None:
        return None
    keys = row.keys()
    record = {
        "id": row["id"],
        "payment_status": row["payment_status"],
        "subscription": bool(USER_DATA.get(row["username"], {}).get("subscription", False)),
        "amount": Decimal(str(row["amount"])) if row["amount"] else Decimal("0"),
        "plan_name": row["plan_name"] if "plan_name" in keys else "Monthly",
        "live_mode": bool(row["live_mode"]) if "live_mode" in keys else False,
    }
    if "transaction_id" in keys and row["transaction_id"]:
        record["transaction_id"] = row["transaction_id"]
    if "verified_at" in keys and row["verified_at"]:
        record["verified_at"] = row["verified_at"]
    return record
def create_payment_record(username, amount="0", razorpay_order_id=None, plan_name="Monthly"):
    """Create a new payment record in pending state."""
    if username not in USER_DATA:
        load_users_from_database()
        if username not in USER_DATA:
            return False
    latest = _latest_payment_row(username)
    if latest and latest["payment_status"] == "pending":
        return False
    with get_connection() as connection:
        connection.execute("""
            INSERT INTO payments (
                username,
                payment_status,
                amount,
                razorpay_order_id,
                plan_name
            )
            VALUES (?, 'pending', ?, ?, ?)
        """, (username, str(amount), razorpay_order_id, plan_name))
    PAYMENT_RECORDS.pop(username, None)
    return True
def update_payment_status(username, status, transaction_id=None, razorpay_payment_id=None):
    """Update payment status with validation. Allows success, failed, pending, cancelled."""
    allowed_statuses = {"pending", "success", "failed", "cancelled"}
    if status not in allowed_statuses:
        return False
    row = _latest_payment_row(username)
    if row is None:
        return False
    if row["payment_status"] in {"success", "cancelled"} and status != row["payment_status"]:
        return False
    with get_connection() as connection:
        if status == "success":
            # Status updates alone must not grant VIP. Activation happens only
            # after payment signature and provider checks, or an admin action.
            connection.execute("""
                UPDATE payments
                SET payment_status = ?,
                    transaction_id = COALESCE(?, transaction_id),
                    razorpay_payment_id = COALESCE(?, razorpay_payment_id),
                    verified_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, transaction_id, razorpay_payment_id, row["id"]))
        else:
            connection.execute("""
                UPDATE payments
                SET payment_status = ?,
                    transaction_id = COALESCE(?, transaction_id),
                    razorpay_payment_id = COALESCE(?, razorpay_payment_id)
                WHERE id = ?
            """, (status, transaction_id, razorpay_payment_id, row["id"]))
    PAYMENT_RECORDS.pop(username, None)
    return True
def set_payment_amount(username, amount):
    """Set transaction amount for the latest pending payment."""
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        return False
    if not amount.is_finite() or amount <= 0:
        return False
    row = _latest_payment_row(username)
    if row is None or row["payment_status"] != "pending":
        return False
    with get_connection() as connection:
        connection.execute("""
            UPDATE payments
            SET amount = ?
            WHERE id = ?
        """, (str(amount), row["id"]))
    return True
def set_transaction_id(username, transaction_id):
    """Attach transaction ID to pending payment."""
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        return False
    row = _latest_payment_row(username)
    if row is None or row["payment_status"] != "pending":
        return False
    with get_connection() as connection:
        connection.execute("""
            UPDATE payments
            SET transaction_id = ?
            WHERE id = ?
        """, (transaction_id.strip(), row["id"]))
    return True
def get_payment_count():
    with get_connection() as connection:
        row = connection.execute("""
            SELECT COUNT(*) AS total
            FROM payments
            WHERE payment_status = 'success'
        """).fetchone()
    return int(row["total"] or 0)
def get_total_collected():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT amount
            FROM payments
            WHERE payment_status = 'success'
        """).fetchall()
    total = Decimal("0")
    for r in rows:
        try:
            total += Decimal(str(r["amount"]))
        except Exception:
            pass
    return total
def get_payment_record(username):
    return _payment_row_to_dict(_latest_payment_row(username))
def get_user_payment_amount(username):
    row = _latest_payment_row(username)
    if row and row["payment_status"] == "success":
        try:
            return Decimal(str(row["amount"]))
        except Exception:
            return Decimal("0")
    return Decimal("0")
def cancel_payment(username):
    return update_payment_status(username, "cancelled")
def get_payment_summary():
    return {
        "total_transactions": get_payment_count(),
        "total_collected": get_total_collected()
    }
# =========================================================
# WITHDRAWAL MANAGEMENT
# =========================================================
def _latest_withdrawal_row(username):
    with get_connection() as connection:
        return connection.execute("""
            SELECT *
            FROM withdrawals
            WHERE username = ?
            ORDER BY id DESC
            LIMIT 1
        """, (username,)).fetchone()
def _withdrawal_row_to_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "amount": Decimal(str(row["amount"])),
        "status": row["status"]
    }
def create_withdrawal_request(username, amount):
    """Create a withdrawal request against available balance. Blocks duplicates."""
    from finance import request_withdrawal
    result = request_withdrawal(username, amount, destination="", idempotency_key=None)
    return bool(result.get("ok"))
def get_withdrawal_request(username):
    return _withdrawal_row_to_dict(_latest_withdrawal_row(username))
def update_withdrawal_status(username, status):
    """Update the latest open withdrawal. Completed payouts stay on the demo rail unless live payouts are authorized."""
    from finance import update_latest_withdrawal_status
    return update_latest_withdrawal_status(username, status)
def get_withdrawal_count():
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM withdrawals").fetchone()
    return int(row["total"] or 0)
def get_pending_withdrawals():
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM withdrawals WHERE status = 'pending'").fetchone()
    return int(row["total"] or 0)
def get_completed_withdrawals():
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM withdrawals WHERE status = 'completed'").fetchone()
    return int(row["total"] or 0)
def get_total_withdrawal_amount():
    with get_connection() as connection:
        rows = connection.execute("SELECT amount FROM withdrawals").fetchall()
    total = Decimal("0")
    for r in rows:
        try:
            total += Decimal(str(r["amount"]))
        except Exception:
            pass
    return total
def get_pending_withdrawal_amount():
    with get_connection() as connection:
        rows = connection.execute("SELECT amount FROM withdrawals WHERE status = 'pending'").fetchall()
    total = Decimal("0")
    for r in rows:
        try:
            total += Decimal(str(r["amount"]))
        except Exception:
            pass
    return total
def get_completed_withdrawal_amount():
    with get_connection() as connection:
        rows = connection.execute("SELECT amount FROM withdrawals WHERE status = 'completed'").fetchall()
    total = Decimal("0")
    for r in rows:
        try:
            total += Decimal(str(r["amount"]))
        except Exception:
            pass
    return total
def get_withdrawal_status(username):
    row = _latest_withdrawal_row(username)
    return row["status"] if row else None
def has_withdrawal_request(username):
    return _latest_withdrawal_row(username) is not None
def is_withdrawal_completed(username):
    return get_withdrawal_status(username) == "completed"
def get_all_withdrawals():
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT username, amount, status
            FROM withdrawals
            ORDER BY id
        """).fetchall()
    result = {}
    for row in rows:
        result[row["username"]] = {
            "amount": Decimal(str(row["amount"])),
            "status": row["status"]
        }
    return result
def get_withdrawal_summary():
    return {
        "total_requests": get_withdrawal_count(),
        "pending_requests": get_pending_withdrawals(),
        "completed_requests": get_completed_withdrawals(),
        "total_amount": get_total_withdrawal_amount(),
        "pending_amount": get_pending_withdrawal_amount(),
        "completed_amount": get_completed_withdrawal_amount()
    }
def get_user_financial_summary(username):
    from finance import get_balance
    balance = get_balance(username)
    return {
        "payment": get_payment_record(username),
        "withdrawal": get_withdrawal_request(username),
        "subscription": get_user_subscription_info(username),
        "balance": balance,
    }
def get_user_withdrawal_amount(username):
    row = _latest_withdrawal_row(username)
    if row is not None:
        try:
            return Decimal(str(row["amount"]))
        except Exception:
            return Decimal("0")
    return Decimal("0")
# =========================================================
# ADMIN GOVERNANCE & TELEMETRY FUNCTIONS
# =========================================================
def verify_admin_access(username):
    """Verify administrator authorization against active SQLite records."""
    if not isinstance(username, str) or not username.strip():
        return False
    username = username.strip()
    with get_connection() as connection:
        row = connection.execute("""
            SELECT role, active
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()
    return bool(row and row["role"] == "admin" and row["active"] == 1)
def get_all_users(admin_username):
    """Return all registered users with subscription lifecycle for admin review."""
    if not verify_admin_access(admin_username):
        return []
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT
                username,
                email,
                mobile,
                role,
                subscription,
                subscription_plan,
                subscription_expires_at,
                active
            FROM users
            ORDER BY username
        """).fetchall()
    return [dict(row) for row in rows]
def get_all_payments(admin_username):
    """Return all payment transactions with plan and mode for admin review."""
    if not verify_admin_access(admin_username):
        return []
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT
                id,
                username,
                payment_status,
                amount,
                plan_name,
                currency,
                live_mode,
                transaction_id,
                razorpay_order_id,
                verified_at,
                created_at
            FROM payments
            ORDER BY id DESC
        """).fetchall()
    return [dict(row) for row in rows]
def get_all_withdrawals_detailed(admin_username):
    """Return detailed withdrawal records for admin review."""
    if not verify_admin_access(admin_username):
        return []
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT
                id,
                username,
                amount,
                status,
                destination_masked,
                payout_mode,
                payout_reference,
                reviewed_by,
                reviewed_at,
                rejection_reason,
                idempotency_key,
                created_at,
                completed_at
            FROM withdrawals
            ORDER BY id DESC
        """).fetchall()
    return [dict(row) for row in rows]
def get_admin_dashboard_summary(admin_username):
    """Generate high-level telemetry summary for Admin Dashboard with Live/Test separation."""
    if not verify_admin_access(admin_username):
        return None
    with get_connection() as connection:
        users = connection.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN active = 0 THEN 1 ELSE 0 END) AS inactive,
                SUM(CASE WHEN subscription = 1 THEN 1 ELSE 0 END) AS active_subs,
                SUM(CASE WHEN subscription_plan = 'Expired' THEN 1 ELSE 0 END) AS expired_subs
            FROM users
        """).fetchone()
        payments = connection.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN payment_status = 'success' THEN 1 ELSE 0 END) AS successful,
                SUM(CASE WHEN payment_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN live_mode = 1 THEN 1 ELSE 0 END) AS live_count,
                SUM(CASE WHEN live_mode = 0 THEN 1 ELSE 0 END) AS test_count
            FROM payments
        """).fetchone()
        withdrawals = connection.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
            FROM withdrawals
        """).fetchone()
    return {
        "total_users": int(users["total"] or 0),
        "active_users": int(users["active"] or 0),
        "inactive_users": int(users["inactive"] or 0),
        "active_subscriptions": int(users["active_subs"] or 0),
        "expired_subscriptions": int(users["expired_subs"] or 0),
        "total_payments": int(payments["total"] or 0),
        "successful_payments": int(payments["successful"] or 0),
        "pending_payments": int(payments["pending"] or 0),
        "live_payments": int(payments["live_count"] or 0),
        "test_payments": int(payments["test_count"] or 0),
        "total_withdrawals": int(withdrawals["total"] or 0),
        "pending_withdrawals": int(withdrawals["pending"] or 0),
        "approved_withdrawals": int(withdrawals["approved"] or 0),
        "completed_withdrawals": int(withdrawals["completed"] or 0),
        "rejected_withdrawals": int(withdrawals["rejected"] or 0),
    }


def get_user_profile(username):
    """Return the signed-in user's profile without the password hash."""
    if not isinstance(username, str) or not username.strip():
        return None
    username = username.strip()
    with get_connection() as connection:
        row = connection.execute("""
            SELECT username, email, mobile, role, active, display_name,
                   subscription, subscription_plan, subscription_start, subscription_expires_at
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()
    if row is None:
        return None
    profile = dict(row)
    profile["subscription_info"] = get_user_subscription_info(username)
    return profile


def change_password(username, current_password, new_password):
    """Change a password after verifying the current one. Stores a new PBKDF2 hash."""
    if not isinstance(username, str):
        return False, "Sign in before changing the password."
    username = username.strip()
    if username not in USER_DATA:
        load_users_from_database()
    user = USER_DATA.get(username)
    if not user or not user.get("active", False):
        return False, "Account is unavailable."
    if not verify_password(current_password, user.get("password", "")):
        record_security_event(username, "PASSWORD_CHANGE_FAILED", "Current password did not match.", "WARNING")
        return False, "Current password is incorrect."
    if not validate_password(new_password):
        return False, "New password must be at least 8 characters."
    if current_password == new_password:
        return False, "Choose a password that is different from the current one."
    updated_hash = hash_password(new_password)
    with get_connection() as connection:
        cursor = connection.execute("""
            UPDATE users SET password = ? WHERE username = ? AND active = 1
        """, (updated_hash, username))
        if cursor.rowcount != 1:
            return False, "Password could not be updated."
    USER_DATA[username]["password"] = updated_hash
    record_user_activity(username, "PASSWORD_CHANGED", "Password hash updated.")
    record_security_event(username, "PASSWORD_CHANGED", "Password changed by the account owner.")
    return True, "Password updated."
