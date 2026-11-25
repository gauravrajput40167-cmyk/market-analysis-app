"""
Balances, subscription plans, withdrawals, notifications, and admin reports.

Withdrawal requests reserve available balance. A payout is recorded only after
the demo provider verifies a reference. Live disbursement is refused.
"""

import logging
import re
import uuid
from decimal import Decimal, InvalidOperation

import payouts
from database import get_connection, initialize_database, log_activity, log_security_event

logger = logging.getLogger("marketapp.finance")

OPEN_STATUSES = ("pending", "approved")
WITHDRAWAL_STATUSES = {"pending", "approved", "rejected", "completed", "cancelled"}
UPI_PATTERN = re.compile(r"^[A-Za-z0-9.\-_]{2,}@[A-Za-z]{2,64}$")
DEMO_PRACTICE_AMOUNT = Decimal("100.00")


def _money(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Enter a valid amount.")
    if not amount.is_finite():
        raise ValueError("Enter a valid amount.")
    return amount


def _sum_rows(rows):
    total = Decimal("0.00")
    for row in rows:
        total += Decimal(str(row["amount"] or "0"))
    return total.quantize(Decimal("0.01"))


def mask_destination(destination: str) -> str:
    text = (destination or "").strip()
    if not text:
        return ""
    if "@" in text:
        local, domain = text.split("@", 1)
        return f"{local[:2]}***@{domain}"
    if len(text) <= 4:
        return "****"
    return f"{text[:2]}***{text[-2:]}"


def notify(username, title, body, category="account"):
    try:
        initialize_database()
        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO notifications (username, title, body, category)
                VALUES (?, ?, ?, ?)
                """,
                (str(username)[:100], str(title)[:140], str(body)[:500], str(category)[:40]),
            )
            connection.commit()
        finally:
            connection.close()
    except Exception as error:
        logger.warning("Notification was not saved: %s", type(error).__name__)


def unread_count(username) -> int:
    initialize_database()
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM notifications
            WHERE username = ? AND is_read = 0
            """,
            (username,),
        ).fetchone()
        return int(row["total"] or 0)
    except Exception:
        return 0
    finally:
        connection.close()


def list_notifications(username, limit=50):
    initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, title, body, category, is_read, created_at
            FROM notifications
            WHERE username = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (username, max(1, min(int(limit), 200))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def mark_notifications_read(username):
    initialize_database()
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE notifications SET is_read = 1 WHERE username = ? AND is_read = 0",
            (username,),
        )
        connection.commit()
        return True
    finally:
        connection.close()


def get_balance(username) -> dict:
    """Available balance is credits minus debits minus open withdrawal reservations."""
    initialize_database()
    connection = get_connection()
    try:
        credits = _sum_rows(connection.execute(
            """
            SELECT amount FROM ledger_entries
            WHERE username = ? AND entry_type IN ('credit', 'demo_credit')
            """,
            (username,),
        ).fetchall())
        debits = _sum_rows(connection.execute(
            """
            SELECT amount FROM ledger_entries
            WHERE username = ? AND entry_type = 'debit'
            """,
            (username,),
        ).fetchall())
        reserved = _sum_rows(connection.execute(
            """
            SELECT amount FROM withdrawals
            WHERE username = ? AND status IN ('pending', 'approved')
            """,
            (username,),
        ).fetchall())
    finally:
        connection.close()
    available = (credits - debits - reserved).quantize(Decimal("0.01"))
    if available < 0:
        available = Decimal("0.00")
    return {
        "currency": "INR",
        "credits": credits,
        "debits": debits,
        "reserved": reserved,
        "available": available,
        "mode": payouts.payout_mode_label(),
        "live_payouts": False,
    }


def list_ledger(username, limit=50):
    initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, entry_type, amount, reference, note, created_at
            FROM ledger_entries
            WHERE username = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (username, max(1, min(int(limit), 200))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def grant_demo_practice_balance(username):
    """Add one clearly labelled practice credit. Never available for live payouts."""
    if payouts.live_payouts_enabled():
        return {"ok": False, "message": payouts.live_payout_block_reason()}
    initialize_database()
    reference = f"demo-practice:{username}"
    connection = get_connection()
    try:
        existing = connection.execute(
            "SELECT id FROM ledger_entries WHERE reference = ?",
            (reference,),
        ).fetchone()
        if existing:
            return {
                "ok": False,
                "message": "Demo practice balance was already added to this account.",
            }
        connection.execute(
            """
            INSERT INTO ledger_entries (username, entry_type, amount, reference, note)
            VALUES (?, 'demo_credit', ?, ?, ?)
            """,
            (
                username,
                str(DEMO_PRACTICE_AMOUNT),
                reference,
                "DEMO ONLY. Practice balance. Not real money and not withdrawable through a live provider.",
            ),
        )
        connection.commit()
    except Exception as error:
        connection.rollback()
        logger.exception("Demo credit failed")
        return {"ok": False, "message": "Demo practice balance could not be added."}
    finally:
        connection.close()
    notify(
        username,
        "Demo practice balance added",
        "₹100.00 practice balance was added. Payouts remain in demo mode and no real money is sent.",
        "wallet",
    )
    log_activity(username, "DEMO_BALANCE_GRANTED", "One-time demo practice balance credited.")
    return {
        "ok": True,
        "message": "₹100.00 demo practice balance added. This is not real money.",
    }


def list_plans(active_only=False):
    initialize_database()
    connection = get_connection()
    try:
        if active_only:
            rows = connection.execute(
                """
                SELECT plan_id, name, amount_rupees, duration_days, currency, active, description
                FROM subscription_plans
                WHERE active = 1
                ORDER BY duration_days, name
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT plan_id, name, amount_rupees, duration_days, currency, active, description
                FROM subscription_plans
                ORDER BY name
                """
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_plan(plan_id):
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
        return dict(row) if row else None
    finally:
        connection.close()


def save_plan(admin_username, plan_id, name, amount_rupees, duration_days, active=True, description=""):
    import auth
    if not auth.verify_admin_access(admin_username):
        return {"ok": False, "message": "Admin access is required."}
    plan_id = str(plan_id or "").strip().lower()
    name = str(name or "").strip()
    if not re.fullmatch(r"[a-z0-9_\-]{3,40}", plan_id):
        return {"ok": False, "message": "Plan id must be 3-40 characters: letters, numbers, _ or -."}
    if not name:
        return {"ok": False, "message": "Plan name is required."}
    try:
        amount = _money(amount_rupees)
        days = int(duration_days)
    except (ValueError, TypeError):
        return {"ok": False, "message": "Amount and duration are invalid."}
    if amount <= 0 or days < 1 or days > 365:
        return {"ok": False, "message": "Amount must be positive and duration must be 1-365 days."}
    initialize_database()
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO subscription_plans (
                plan_id, name, amount_rupees, duration_days, currency, active, description, updated_at
            ) VALUES (?, ?, ?, ?, 'INR', ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(plan_id) DO UPDATE SET
                name = excluded.name,
                amount_rupees = excluded.amount_rupees,
                duration_days = excluded.duration_days,
                active = excluded.active,
                description = excluded.description,
                updated_at = CURRENT_TIMESTAMP
            """,
            (plan_id, name[:80], str(amount), days, 1 if active else 0, str(description or "")[:300]),
        )
        connection.commit()
    finally:
        connection.close()
    log_activity(admin_username, "PLAN_UPDATED", f"Plan {plan_id} saved.")
    return {"ok": True, "message": f"Plan {name} saved."}


def list_payments(username, limit=50):
    initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, payment_status, amount, currency, plan_name, live_mode,
                   razorpay_order_id, transaction_id, provider_status, failure_reason,
                   verified_at, created_at
            FROM payments
            WHERE username = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (username, max(1, min(int(limit), 200))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def list_withdrawals(username, limit=50):
    initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, amount, status, destination_masked, payout_mode, payout_reference,
                   rejection_reason, created_at, reviewed_at, completed_at
            FROM withdrawals
            WHERE username = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (username, max(1, min(int(limit), 200))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def request_withdrawal(username, amount, destination="", idempotency_key=None):
    """Reserve balance for a withdrawal. The same idempotency key cannot create two rows."""
    import auth
    username = str(username or "").strip()
    if not username:
        return {"ok": False, "message": "Sign in before requesting a withdrawal."}
    auth.load_users_from_database()
    if username not in auth.USER_DATA:
        return {"ok": False, "message": "Account not found."}
    if not auth.USER_DATA[username].get("active", False):
        return {"ok": False, "message": "This account is disabled."}
    try:
        amount = _money(amount)
    except ValueError as error:
        return {"ok": False, "message": str(error)}
    if amount <= 0:
        return {"ok": False, "message": "Withdrawal amount must be greater than zero."}

    destination = str(destination or "").strip()
    if destination and not UPI_PATTERN.fullmatch(destination):
        return {"ok": False, "message": "Enter a valid UPI id, for example name@okbank."}
    masked = mask_destination(destination) if destination else "Not provided"
    idempotency_key = str(idempotency_key or "").strip() or uuid.uuid4().hex
    initialize_database()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing_key = connection.execute(
            """
            SELECT id, username, status FROM withdrawals
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if existing_key:
            if existing_key["username"] != username:
                connection.rollback()
                return {"ok": False, "message": "This request reference is already in use."}
            connection.commit()
            return {
                "ok": True,
                "duplicate": True,
                "id": existing_key["id"],
                "message": "This withdrawal request was already recorded.",
            }
        pending = connection.execute(
            """
            SELECT id FROM withdrawals
            WHERE username = ? AND status = 'pending'
            LIMIT 1
            """,
            (username,),
        ).fetchone()
        if pending:
            connection.rollback()
            return {"ok": False, "message": "You already have a pending withdrawal. Wait for a decision before requesting another."}

        credits = _sum_rows(connection.execute(
            "SELECT amount FROM ledger_entries WHERE username = ? AND entry_type IN ('credit', 'demo_credit')",
            (username,),
        ).fetchall())
        debits = _sum_rows(connection.execute(
            "SELECT amount FROM ledger_entries WHERE username = ? AND entry_type = 'debit'",
            (username,),
        ).fetchall())
        reserved = _sum_rows(connection.execute(
            "SELECT amount FROM withdrawals WHERE username = ? AND status IN ('pending', 'approved')",
            (username,),
        ).fetchall())
        available = credits - debits - reserved
        if amount > available:
            connection.rollback()
            return {
                "ok": False,
                "message": f"Amount exceeds available balance (₹{available}).",
            }
        cursor = connection.execute(
            """
            INSERT INTO withdrawals (
                username, amount, status, destination, destination_masked,
                idempotency_key, payout_mode
            ) VALUES (?, ?, 'pending', ?, ?, ?, 'demo')
            """,
            (username, str(amount), destination or None, masked, idempotency_key),
        )
        withdrawal_id = cursor.lastrowid
        connection.commit()
    except Exception as error:
        connection.rollback()
        logger.exception("Withdrawal request failed")
        return {"ok": False, "message": "Withdrawal request could not be saved."}
    finally:
        connection.close()

    notify(
        username,
        "Withdrawal request received",
        f"Demo withdrawal #{withdrawal_id} for ₹{amount} is pending review. No real money has been sent.",
        "withdrawal",
    )
    log_activity(username, "WITHDRAWAL_REQUEST_CREATED", f"Demo withdrawal #{withdrawal_id} for {amount}.")
    return {
        "ok": True,
        "duplicate": False,
        "id": withdrawal_id,
        "message": "Withdrawal request submitted in demo mode. No real funds will be sent.",
    }


def _debit_completed_withdrawal(connection, row):
    reference = f"withdrawal:{row['id']}"
    existing = connection.execute(
        "SELECT id FROM ledger_entries WHERE reference = ?",
        (reference,),
    ).fetchone()
    if existing:
        return
    connection.execute(
        """
        INSERT INTO ledger_entries (username, entry_type, amount, reference, note)
        VALUES (?, 'debit', ?, ?, ?)
        """,
        (
            row["username"],
            str(row["amount"]),
            reference,
            f"Demo payout reservation settled for withdrawal #{row['id']}.",
        ),
    )


def _load_withdrawal(connection, withdrawal_id):
    return connection.execute(
        "SELECT * FROM withdrawals WHERE id = ?",
        (int(withdrawal_id),),
    ).fetchone()


def review_withdrawal(admin_username, withdrawal_id, decision, note=""):
    """Approve or reject an open request. Approval does not send money."""
    import auth
    if not auth.verify_admin_access(admin_username):
        return {"ok": False, "message": "Admin access is required."}
    decision = str(decision or "").strip().lower()
    if decision not in {"approved", "rejected"}:
        return {"ok": False, "message": "Decision must be approved or rejected."}
    note = str(note or "").strip()[:300]
    initialize_database()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = _load_withdrawal(connection, withdrawal_id)
        if row is None:
            connection.rollback()
            return {"ok": False, "message": "Withdrawal request was not found."}
        if row["status"] not in {"pending", "approved"}:
            connection.rollback()
            return {"ok": False, "message": f"This request is already {row['status']}."}
        if decision == "approved" and row["status"] == "approved":
            connection.commit()
            return {"ok": True, "message": "Withdrawal is already approved."}
        user = connection.execute(
            "SELECT active FROM users WHERE username = ?",
            (row["username"],),
        ).fetchone()
        if decision == "approved" and (not user or int(user["active"] or 0) != 1):
            connection.rollback()
            return {"ok": False, "message": "The user account is not active."}
        if decision == "rejected" and not note:
            note = "Rejected by administrator."
        cursor = connection.execute(
            """
            UPDATE withdrawals
            SET status = ?,
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP,
                rejection_reason = CASE WHEN ? = 'rejected' THEN ? ELSE rejection_reason END
            WHERE id = ? AND status IN ('pending', 'approved')
            """,
            (decision, admin_username, decision, note, row["id"]),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return {"ok": False, "message": "Withdrawal could not be updated."}
        connection.execute(
            """
            INSERT INTO payout_events (withdrawal_id, event_type, verified, actor, details)
            VALUES (?, ?, 1, ?, ?)
            """,
            (row["id"], f"review_{decision}", admin_username, note or decision),
        )
        connection.commit()
        owner = row["username"]
        amount = row["amount"]
    except Exception:
        connection.rollback()
        logger.exception("Withdrawal review failed")
        return {"ok": False, "message": "Withdrawal review could not be saved."}
    finally:
        connection.close()

    if decision == "approved":
        notify(owner, "Withdrawal approved", f"Request for ₹{amount} was approved for a demo payout. No real money has been sent.", "withdrawal")
        log_activity(admin_username, "WITHDRAWAL_APPROVED", f"Approved withdrawal #{withdrawal_id}.")
        return {"ok": True, "message": "Withdrawal approved. Complete the demo payout separately. No real funds were sent."}
    notify(owner, "Withdrawal rejected", note or "The withdrawal request was rejected. Reserved demo balance was released.", "withdrawal")
    log_activity(admin_username, "WITHDRAWAL_REJECTED", f"Rejected withdrawal #{withdrawal_id}.")
    log_security_event(admin_username, "WITHDRAWAL_REJECTED", f"Withdrawal #{withdrawal_id} rejected.", "INFO")
    return {"ok": True, "message": "Withdrawal rejected. Reserved balance was released."}


def complete_demo_payout(admin_username, withdrawal_id):
    """Verify a demo reference, then mark the withdrawal completed and debit once."""
    import auth
    if not auth.verify_admin_access(admin_username):
        return {"ok": False, "message": "Admin access is required."}
    if payouts.live_payouts_enabled():
        log_security_event(admin_username, "LIVE_PAYOUT_BLOCKED", payouts.live_payout_block_reason(), "WARNING")
        return {"ok": False, "message": payouts.live_payout_block_reason()}

    initialize_database()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = _load_withdrawal(connection, withdrawal_id)
        if row is None:
            connection.rollback()
            return {"ok": False, "message": "Withdrawal request was not found."}
        if row["status"] == "completed":
            connection.commit()
            return {"ok": True, "duplicate": True, "message": "This payout was already completed."}
        if row["status"] != "approved":
            connection.rollback()
            return {"ok": False, "message": "Only an approved withdrawal can be completed."}

        issued = payouts.authorized_payout(row["id"], row["username"], str(row["amount"]))
        if not issued.get("ok"):
            connection.rollback()
            return issued
        reference = issued["reference"]
        if not payouts.verify_demo_reference(row["id"], row["username"], str(row["amount"]), reference):
            connection.rollback()
            log_security_event(admin_username, "PAYOUT_VERIFICATION_FAILED", f"Withdrawal #{row['id']}", "ERROR")
            return {"ok": False, "message": "Payout verification failed. Nothing was marked completed."}

        cursor = connection.execute(
            """
            UPDATE withdrawals
            SET status = 'completed',
                payout_reference = ?,
                payout_mode = 'demo',
                completed_at = CURRENT_TIMESTAMP,
                reviewed_by = COALESCE(reviewed_by, ?)
            WHERE id = ? AND status = 'approved'
            """,
            (reference, admin_username, row["id"]),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return {"ok": False, "message": "Payout was not completed because the request changed."}
        _debit_completed_withdrawal(connection, row)
        connection.execute(
            """
            INSERT INTO payout_events (withdrawal_id, event_type, reference, verified, actor, details)
            VALUES (?, 'demo_completed', ?, 1, ?, 'Demo payout verified. No real funds sent.')
            """,
            (row["id"], reference, admin_username),
        )
        connection.commit()
        owner = row["username"]
        amount = row["amount"]
    except Exception:
        connection.rollback()
        logger.exception("Demo payout completion failed")
        return {"ok": False, "message": "Demo payout could not be completed."}
    finally:
        connection.close()

    notify(
        owner,
        "Demo payout completed",
        f"Withdrawal of ₹{amount} was marked completed in DEMO mode. Reference {reference}. No real money was sent.",
        "withdrawal",
    )
    log_activity(admin_username, "WITHDRAWAL_COMPLETED", f"Demo payout #{withdrawal_id} reference {reference}.")
    return {
        "ok": True,
        "reference": reference,
        "message": f"Demo payout completed ({reference}). No real funds were sent.",
    }


def update_latest_withdrawal_status(username, status):
    """Backward-compatible status change for the latest open withdrawal."""
    if status not in {"approved", "rejected", "cancelled", "completed"}:
        return False
    initialize_database()
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT * FROM withdrawals
            WHERE username = ? AND status IN ('pending', 'approved')
            ORDER BY id DESC
            LIMIT 1
            """,
            (username,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return False
    if status == "completed":
        if row["status"] == "pending":
            connection = get_connection()
            try:
                cursor = connection.execute(
                    """
                    UPDATE withdrawals
                    SET status = 'approved',
                        reviewed_by = 'legacy',
                        reviewed_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'pending'
                    """,
                    (row["id"],),
                )
                connection.commit()
                if cursor.rowcount != 1:
                    return False
            finally:
                connection.close()
        issued = payouts.authorized_payout(row["id"], row["username"], str(row["amount"]))
        if not issued.get("ok"):
            return False
        return _complete_with_reference(row["id"], issued["reference"])
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            UPDATE withdrawals
            SET status = ?,
                reviewed_at = CURRENT_TIMESTAMP,
                rejection_reason = CASE
                    WHEN ? IN ('rejected', 'cancelled') THEN 'Updated by status API'
                    ELSE rejection_reason
                END
            WHERE id = ? AND status IN ('pending', 'approved')
            """,
            (status, status, row["id"]),
        )
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


def _complete_with_reference(withdrawal_id, reference):
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = _load_withdrawal(connection, withdrawal_id)
        if row is None or row["status"] == "completed":
            connection.rollback()
            return row is not None and row["status"] == "completed"
        if not payouts.verify_demo_reference(row["id"], row["username"], str(row["amount"]), reference):
            connection.rollback()
            return False
        cursor = connection.execute(
            """
            UPDATE withdrawals
            SET status = 'completed',
                payout_reference = ?,
                payout_mode = 'demo',
                completed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'approved'
            """,
            (reference, row["id"]),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return False
        _debit_completed_withdrawal(connection, row)
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        return False
    finally:
        connection.close()


def financial_report(admin_username):
    import auth
    if not auth.verify_admin_access(admin_username):
        return None
    initialize_database()
    connection = get_connection()
    try:
        payments = connection.execute(
            """
            SELECT payment_status,
                   COUNT(*) AS total,
                   COALESCE(SUM(CAST(amount AS REAL)), 0) AS amount,
                   SUM(CASE WHEN live_mode = 1 THEN 1 ELSE 0 END) AS live_count
            FROM payments
            GROUP BY payment_status
            """
        ).fetchall()
        withdrawals = connection.execute(
            """
            SELECT status,
                   COUNT(*) AS total,
                   COALESCE(SUM(CAST(amount AS REAL)), 0) AS amount
            FROM withdrawals
            GROUP BY status
            """
        ).fetchall()
        plans = connection.execute(
            "SELECT plan_id, name, amount_rupees, duration_days, active FROM subscription_plans ORDER BY name"
        ).fetchall()
        liability = connection.execute(
            """
            SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS credits
            FROM ledger_entries
            WHERE entry_type IN ('credit', 'demo_credit')
            """
        ).fetchone()
        settled = connection.execute(
            """
            SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS debits
            FROM ledger_entries
            WHERE entry_type = 'debit'
            """
        ).fetchone()
    finally:
        connection.close()
    return {
        "payments": [dict(row) for row in payments],
        "withdrawals": [dict(row) for row in withdrawals],
        "plans": [dict(row) for row in plans],
        "demo_credits": float(liability["credits"] or 0),
        "settled_debits": float(settled["debits"] or 0),
        "payout_mode": payouts.payout_mode_label(),
        "live_payouts_enabled": payouts.live_payouts_enabled(),
        "note": (
            "This build does not send real payouts. If live flags are switched on, "
            "completion is refused until a separate authorized provider is implemented."
        ),
    }
