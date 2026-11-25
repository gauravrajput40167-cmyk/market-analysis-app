import os
import time
import sqlite3
from pathlib import Path
from datetime import datetime
# =========================================================
# DATABASE CONFIGURATION & PATH SETUP
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "memiixyz.db"
BACKUP_DIR = BASE_DIR / "backups"
_DB_INITIALIZED = False
# =========================================================
# THREAD-SAFE CONCURRENT DATABASE CONNECTION
# =========================================================
def get_connection():
    """Return an optimized SQLite connection with WAL mode and row factory."""
    connection = sqlite3.connect(
        str(DB_PATH),
        timeout=30.0,
        check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    # Performance & Concurrency Optimizations
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        pass
    return connection
# =========================================================
# SCHEMA VALIDATION & COLUMN MIGRATION HELPERS
# =========================================================
ALLOWED_TABLES = {
    "users",
    "payments",
    "payment_attempts",
    "withdrawals",
    "activity_logs",
    "security_logs",
    "app_configurations",
    "configuration_history",
    "api_monitoring_logs",
    "database_monitoring_logs",
}
def _get_table_columns(connection, table_name):
    """Retrieve existing column names for an allow-listed table."""
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Unauthorized table name requested: {table_name}")
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}
def _ensure_columns(connection, table_name, columns):
    """Ensure required columns exist without deleting existing table data."""
    existing_columns = _get_table_columns(connection, table_name)
    for column_name, column_type in columns.items():
        if column_name not in existing_columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
# =========================================================
# CENTRAL DATABASE INITIALIZATION
# =========================================================
def initialize_database(force=False):
    """Initialize all platform tables, columns, indexes and default configurations."""
    global _DB_INITIALIZED
    if _DB_INITIALIZED and not force:
        return True
    connection = get_connection()
    try:
        # 1. Users Table (with Subscription Lifecycle Columns)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                mobile TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                subscription INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                subscription_plan TEXT DEFAULT 'Free',
                subscription_start TEXT,
                subscription_expires_at TEXT
            )
        """)
        _ensure_columns(connection, "users", {
            "email": "TEXT",
            "mobile": "TEXT",
            "password": "TEXT",
            "role": "TEXT NOT NULL DEFAULT 'user'",
            "subscription": "INTEGER NOT NULL DEFAULT 0",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "subscription_plan": "TEXT DEFAULT 'Free'",
            "subscription_start": "TEXT",
            "subscription_expires_at": "TEXT"
        })
        # 2. Payments Table (with Plan, Currency & Live Mode Audit Columns)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                payment_status TEXT NOT NULL DEFAULT 'pending',
                amount TEXT NOT NULL DEFAULT '0',
                transaction_id TEXT,
                razorpay_order_id TEXT,
                razorpay_payment_id TEXT,
                plan_name TEXT DEFAULT 'Monthly',
                currency TEXT DEFAULT 'INR',
                live_mode INTEGER DEFAULT 0,
                verified_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (username) REFERENCES users(username)
            )
        """)
        _ensure_columns(connection, "payments", {
            "username": "TEXT",
            "payment_status": "TEXT NOT NULL DEFAULT 'pending'",
            "amount": "TEXT NOT NULL DEFAULT '0'",
            "transaction_id": "TEXT",
            "razorpay_order_id": "TEXT",
            "razorpay_payment_id": "TEXT",
            "plan_name": "TEXT DEFAULT 'Monthly'",
            "currency": "TEXT DEFAULT 'INR'",
            "live_mode": "INTEGER DEFAULT 0",
            "verified_at": "TEXT",
            "created_at": "TEXT"
        })
        # 3. Payment Attempts Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS payment_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id INTEGER NOT NULL,
                razorpay_payment_id TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'failed', 'captured')),
                failure_reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE
            )
        """)
        _ensure_columns(connection, "payment_attempts", {
            "payment_id": "INTEGER",
            "razorpay_payment_id": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "failure_reason": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT"
        })
        # 4. Withdrawals Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                amount TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (username) REFERENCES users(username)
            )
        """)
        _ensure_columns(connection, "withdrawals", {
            "username": "TEXT",
            "amount": "TEXT NOT NULL DEFAULT '0'",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "created_at": "TEXT"
        })
        # 5. Activity Logs Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _ensure_columns(connection, "activity_logs", {
            "username": "TEXT",
            "action": "TEXT",
            "details": "TEXT",
            "created_at": "TEXT"
        })
        # 6. Security Logs Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS security_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'INFO',
                details TEXT,
                ip_address TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _ensure_columns(connection, "security_logs", {
            "username": "TEXT",
            "event_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "severity": "TEXT NOT NULL DEFAULT 'INFO'",
            "details": "TEXT",
            "ip_address": "TEXT",
            "created_at": "TEXT"
        })
        # 7. App Configurations Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS app_configurations (
                config_key TEXT PRIMARY KEY,
                config_value TEXT NOT NULL,
                updated_by TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 8. Configuration History Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS configuration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                changed_by TEXT,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 9. API Monitoring Logs Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS api_monitoring_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                status TEXT NOT NULL,
                http_status INTEGER,
                response_ms REAL,
                message TEXT,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 10. Database Monitoring Logs Table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS database_monitoring_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                integrity_status TEXT NOT NULL,
                response_ms REAL,
                database_size_bytes INTEGER,
                table_count INTEGER,
                total_rows INTEGER,
                journal_mode TEXT,
                message TEXT,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Default platform settings initialization
        defaults = {
            "app_name": "Market Prediction App",
            "app_description": "Live read-only market analysis dashboard",
            "support_email": "support@example.com",
            "maintenance_mode": "false",
            "registration_enabled": "true",
            "login_enabled": "true",
            "subscription_enabled": "true",
            "refresh_interval": "5",
            "currency": "INR",
            "timezone": "Asia/Kolkata",
            "notification_login": "true",
            "notification_registration": "true",
            "notification_payment": "true",
            "notification_withdrawal": "true",
        }
        for k, v in defaults.items():
            connection.execute(
                "INSERT OR IGNORE INTO app_configurations(config_key, config_value) VALUES (?, ?)",
                (k, v)
            )
        # High-performance indexes
        connection.execute("CREATE INDEX IF NOT EXISTS idx_payments_username ON payments(username)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(razorpay_order_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_payments_live_mode ON payments(live_mode)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_payment_attempts_payment_id ON payment_attempts(payment_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_username ON withdrawals(username)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_username ON activity_logs(username)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_activity_logs_action ON activity_logs(action)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_security_logs_created_at ON security_logs(created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_security_logs_username ON security_logs(username)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_security_logs_event_type ON security_logs(event_type)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_users_subscription_expires ON users(subscription_expires_at)")
        connection.commit()
        _DB_INITIALIZED = True
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
# Safe startup execution
try:
    initialize_database()
except Exception as init_err:
    print(f"[Database] Startup schema warning: {init_err}")
# =========================================================
# CENTRAL AUDIT & ACTIVITY LOGGING
# =========================================================
def log_activity(username, action, details=None):
    """Save an activity log entry without performance overhead."""
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO activity_logs (username, action, details)
            VALUES (?, ?, ?)
        """, (
            str(username).strip()[:100] if username else "System",
            str(action).strip()[:100],
            str(details)[:1000] if details is not None else None
        ))
        connection.commit()
        return True
    except Exception as err:
        connection.rollback()
        print(f"[Database] log_activity error: {err}")
        return False
    finally:
        connection.close()
def get_activity_logs(limit=100, username=None, action=None, start_date=None, end_date=None):
    """Retrieve filtered activity records."""
    limit = max(1, min(int(limit), 10000))
    if not _DB_INITIALIZED:
        initialize_database()
    conditions, values = [], []
    if username and str(username).strip():
        conditions.append("LOWER(COALESCE(username, '')) LIKE ?")
        values.append(f"%{str(username).strip().lower()}%")
    if action and str(action).strip() and str(action).strip() != "All":
        conditions.append("action = ?")
        values.append(str(action).strip())
    if start_date:
        conditions.append("date(created_at) >= date(?)")
        values.append(str(start_date))
    if end_date:
        conditions.append("date(created_at) <= date(?)")
        values.append(str(end_date))
    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    connection = get_connection()
    try:
        rows = connection.execute(f"""
            SELECT id, username, action, details, created_at
            FROM activity_logs
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
        """, (*values, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
def get_activity_log_count(username=None, action=None, start_date=None, end_date=None):
    """Return total count of matching activity logs."""
    if not _DB_INITIALIZED:
        initialize_database()
    conditions, values = [], []
    if username and str(username).strip():
        conditions.append("LOWER(COALESCE(username, '')) LIKE ?")
        values.append(f"%{str(username).strip().lower()}%")
    if action and str(action).strip() and str(action).strip() != "All":
        conditions.append("action = ?")
        values.append(str(action).strip())
    if start_date:
        conditions.append("date(created_at) >= date(?)")
        values.append(str(start_date))
    if end_date:
        conditions.append("date(created_at) <= date(?)")
        values.append(str(end_date))
    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    connection = get_connection()
    try:
        row = connection.execute(f"SELECT COUNT(*) AS total FROM activity_logs {where_sql}", values).fetchone()
        return int(row["total"] or 0)
    finally:
        connection.close()
def get_activity_log_actions():
    """Retrieve distinct activity actions."""
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT DISTINCT action
            FROM activity_logs
            WHERE action IS NOT NULL AND TRIM(action) != ''
            ORDER BY action COLLATE NOCASE
        """).fetchall()
        return [row["action"] for row in rows]
    finally:
        connection.close()
# =========================================================
# SECURITY LOGGING
# =========================================================
def log_security_event(username, event_type, details=None, severity="INFO", ip_address=None):
    """Persist a security event with sanitization. Never stores passwords or secrets."""
    allowed_severities = {"INFO", "WARNING", "ERROR", "CRITICAL"}
    severity = str(severity).upper()
    if severity not in allowed_severities:
        severity = "INFO"
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO security_logs (username, event_type, severity, details, ip_address)
            VALUES (?, ?, ?, ?, ?)
        """, (
            str(username).strip()[:100] if username else "Unknown",
            str(event_type).strip()[:100],
            severity,
            str(details)[:1000] if details is not None else None,
            str(ip_address)[:64] if ip_address else None,
        ))
        connection.commit()
        return True
    except Exception as err:
        connection.rollback()
        print(f"[Database] log_security_event error: {err}")
        return False
    finally:
        connection.close()
def get_security_logs(limit=100, username=None, event_type=None, severity=None, start_date=None, end_date=None):
    """Retrieve security event records."""
    limit = max(1, min(int(limit), 10000))
    if not _DB_INITIALIZED:
        initialize_database()
    conditions, values = [], []
    if username and str(username).strip():
        conditions.append("LOWER(COALESCE(username, '')) LIKE ?")
        values.append(f"%{str(username).strip().lower()}%")
    if event_type and event_type != "All":
        conditions.append("event_type = ?")
        values.append(str(event_type))
    if severity and severity != "All":
        conditions.append("severity = ?")
        values.append(str(severity).upper())
    if start_date:
        conditions.append("date(created_at) >= date(?)")
        values.append(str(start_date))
    if end_date:
        conditions.append("date(created_at) <= date(?)")
        values.append(str(end_date))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    connection = get_connection()
    try:
        rows = connection.execute(
            f"SELECT id, username, event_type, severity, details, ip_address, created_at FROM security_logs{where} ORDER BY id DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
def get_security_log_count(username=None, event_type=None, severity=None, start_date=None, end_date=None):
    if not _DB_INITIALIZED:
        initialize_database()
    conditions, values = [], []
    if username and str(username).strip():
        conditions.append("LOWER(COALESCE(username, '')) LIKE ?")
        values.append(f"%{str(username).strip().lower()}%")
    if event_type and event_type != "All":
        conditions.append("event_type = ?")
        values.append(str(event_type))
    if severity and severity != "All":
        conditions.append("severity = ?")
        values.append(str(severity).upper())
    if start_date:
        conditions.append("date(created_at) >= date(?)")
        values.append(str(start_date))
    if end_date:
        conditions.append("date(created_at) <= date(?)")
        values.append(str(end_date))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    connection = get_connection()
    try:
        row = connection.execute(f"SELECT COUNT(*) AS total FROM security_logs{where}", values).fetchone()
        return int(row["total"] or 0)
    finally:
        connection.close()
def get_security_log_event_types():
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute("SELECT DISTINCT event_type FROM security_logs ORDER BY event_type").fetchall()
        return [row["event_type"] for row in rows if row["event_type"]]
    finally:
        connection.close()
# =========================================================
# APPLICATION CONFIGURATION & AUDIT HISTORY
# =========================================================
def get_app_configurations():
    """Retrieve all current configuration keys and values."""
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT config_key, config_value, updated_by, updated_at FROM app_configurations ORDER BY config_key"
        ).fetchall()
        return {row["config_key"]: dict(row) for row in rows}
    finally:
        connection.close()
def save_app_configurations(settings, changed_by):
    """Save updated application configuration and append to history log."""
    if not isinstance(settings, dict):
        raise ValueError("Settings must be provided as a dictionary.")
    allowed = {
        "app_name", "app_description", "support_email", "maintenance_mode",
        "registration_enabled", "login_enabled", "subscription_enabled",
        "refresh_interval", "currency", "timezone", "notification_login",
        "notification_registration", "notification_payment", "notification_withdrawal",
    }
    if set(settings) - allowed:
        raise ValueError("Unsupported configuration parameter supplied.")
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        for key, value in settings.items():
            value = str(value)
            old = connection.execute(
                "SELECT config_value FROM app_configurations WHERE config_key = ?", (key,)
            ).fetchone()
            old_value = old["config_value"] if old else None
            if old_value != value:
                connection.execute("""
                    INSERT INTO app_configurations(config_key, config_value, updated_by, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(config_key) DO UPDATE SET
                        config_value=excluded.config_value,
                        updated_by=excluded.updated_by,
                        updated_at=CURRENT_TIMESTAMP
                """, (key, value, str(changed_by)))
                connection.execute("""
                    INSERT INTO configuration_history(config_key, old_value, new_value, changed_by)
                    VALUES (?, ?, ?, ?)
                """, (key, old_value, value, str(changed_by)))
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
def get_configuration_history(limit=100):
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute("""
            SELECT config_key, old_value, new_value, changed_by, changed_at
            FROM configuration_history ORDER BY id DESC LIMIT ?
        """, (max(1, min(int(limit), 1000)),)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
# =========================================================
# ADMIN DATA ACCESS (READ-ONLY EXPLORER)
# =========================================================
DATA_ACCESS_TABLES = {
    "Users": {
        "table": "users",
        "columns": [
            "username", "email", "mobile", "role", "subscription",
            "subscription_plan", "subscription_start", "subscription_expires_at", "active"
        ],
        "search_columns": ["username", "email", "mobile", "subscription_plan"],
        "status_column": "active",
        "date_column": None
    },
    "Payments": {
        "table": "payments",
        "columns": [
            "id", "username", "payment_status", "amount", "plan_name",
            "currency", "live_mode", "transaction_id", "verified_at", "created_at"
        ],
        "search_columns": ["username", "payment_status", "plan_name", "transaction_id"],
        "status_column": "payment_status",
        "date_column": "created_at"
    },
    "Withdrawals": {
        "table": "withdrawals",
        "columns": ["id", "username", "amount", "status", "created_at"],
        "search_columns": ["username", "status"],
        "status_column": "status",
        "date_column": "created_at"
    },
    "Payment Attempts": {
        "table": "payment_attempts",
        "columns": ["id", "payment_id", "status", "failure_reason", "created_at", "updated_at"],
        "search_columns": ["status", "failure_reason"],
        "status_column": "status",
        "date_column": "created_at"
    },
    "Activity Logs": {
        "table": "activity_logs",
        "columns": ["id", "username", "action", "details", "created_at"],
        "search_columns": ["username", "action", "details"],
        "status_column": None,
        "date_column": "created_at"
    },
    "Security Logs": {
        "table": "security_logs",
        "columns": ["id", "username", "event_type", "severity", "details", "created_at"],
        "search_columns": ["username", "event_type", "severity", "details"],
        "status_column": "severity",
        "date_column": "created_at"
    },
    "App Configurations": {
        "table": "app_configurations",
        "columns": ["config_key", "config_value", "updated_by", "updated_at"],
        "search_columns": ["config_key", "config_value", "updated_by"],
        "status_column": None,
        "date_column": "updated_at"
    },
}
def get_data_access_summary():
    """Return record counts for all registered data collections."""
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        return {
            label: int(connection.execute(f"SELECT COUNT(*) FROM {spec['table']}").fetchone()[0])
            for label, spec in DATA_ACCESS_TABLES.items()
        }
    finally:
        connection.close()
def get_data_access_records(dataset, search_text=None, status="All", start_date=None, end_date=None, limit=500):
    """Safe read-only explorer; sensitive credentials and secret keys are excluded."""
    if dataset not in DATA_ACCESS_TABLES:
        raise ValueError(f"Unsupported dataset requested: {dataset}")
    if not _DB_INITIALIZED:
        initialize_database()
    spec = DATA_ACCESS_TABLES[dataset]
    conditions, params = [], []
    if search_text and str(search_text).strip() and spec["search_columns"]:
        conditions.append("(" + " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in spec["search_columns"]) + ")")
        params.extend([f"%{str(search_text).strip()}%"] * len(spec["search_columns"]))
    if spec["status_column"] and status and status != "All":
        conditions.append(f"CAST({spec['status_column']} AS TEXT) = ?")
        params.append(str(status))
    if spec["date_column"] and start_date:
        conditions.append(f"date({spec['date_column']}) >= date(?)")
        params.append(str(start_date))
    if spec["date_column"] and end_date:
        conditions.append(f"date({spec['date_column']}) <= date(?)")
        params.append(str(end_date))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    limit = max(1, min(int(limit), 1000))
    sql = "SELECT " + ", ".join(spec["columns"]) + f" FROM {spec['table']}{where} ORDER BY rowid DESC LIMIT ?"
    params.append(limit)
    connection = get_connection()
    try:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()
def get_data_access_status_values(dataset):
    if dataset not in DATA_ACCESS_TABLES:
        raise ValueError(f"Unsupported dataset: {dataset}")
    spec = DATA_ACCESS_TABLES[dataset]
    col = spec["status_column"]
    if not col:
        return []
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        rows = connection.execute(
            f"SELECT DISTINCT {col} FROM {spec['table']} WHERE {col} IS NOT NULL AND CAST({col} AS TEXT) != '' ORDER BY {col}"
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        connection.close()
# =========================================================
# API MONITORING TELEMETRY
# =========================================================
def save_api_monitoring_result(service_name, endpoint, status, http_status=None, response_ms=None, message=None):
    """Persist an external API health check record."""
    allowed = {"UP", "DOWN", "ERROR"}
    if status not in allowed:
        raise ValueError("Invalid API monitoring status.")
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO api_monitoring_logs
            (service_name, endpoint, status, http_status, response_ms, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(service_name)[:100],
            str(endpoint)[:300],
            status,
            http_status,
            response_ms,
            str(message or "")[:500]
        ))
        connection.commit()
    finally:
        connection.close()
def get_api_monitoring_logs(limit=100, service_name="All", status="All"):
    limit = max(1, min(int(limit), 500))
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        conditions, params = [], []
        if service_name and service_name != "All":
            conditions.append("service_name = ?")
            params.append(str(service_name))
        if status and status != "All":
            conditions.append("status = ?")
            params.append(str(status))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        rows = connection.execute(
            f"SELECT id, service_name, endpoint, status, http_status, response_ms, message, checked_at FROM api_monitoring_logs{where} ORDER BY id DESC LIMIT ?",
            params
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
def get_api_monitoring_summary():
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        row = connection.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'UP' THEN 1 ELSE 0 END) AS up_count,
                   SUM(CASE WHEN status IN ('DOWN', 'ERROR') THEN 1 ELSE 0 END) AS issue_count,
                   AVG(CASE WHEN status = 'UP' THEN response_ms END) AS avg_response_ms
            FROM api_monitoring_logs
        """).fetchone()
        return {
            "total": int(row["total"] or 0),
            "up": int(row["up_count"] or 0),
            "issues": int(row["issue_count"] or 0),
            "avg_response_ms": round(float(row["avg_response_ms"]), 2) if row["avg_response_ms"] is not None else None,
        }
    finally:
        connection.close()
# =========================================================
# DATABASE DIAGNOSTICS & MONITORING
# =========================================================
DATABASE_MONITOR_TABLES = (
    "users", "payments", "payment_attempts", "withdrawals",
    "activity_logs", "security_logs", "app_configurations",
    "configuration_history", "api_monitoring_logs", "database_monitoring_logs"
)
def get_database_monitor_snapshot(run_integrity_check=True):
    """Retrieve real-time database file, table count, and integrity telemetry."""
    started = time.perf_counter()
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        table_names = [row[0] for row in table_rows]
        counts = {}
        total_rows = 0
        for table_name in DATABASE_MONITOR_TABLES:
            if table_name in table_names:
                count = int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
                counts[table_name] = count
                total_rows += count
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
        integrity_status = "NOT RUN"
        if run_integrity_check:
            integrity_status = str(connection.execute("PRAGMA quick_check").fetchone()[0]).upper()
        response_ms = round((time.perf_counter() - started) * 1000, 2)
        size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        status = "HEALTHY" if integrity_status in {"OK", "NOT RUN"} else "ISSUE"
        return {
            "status": status,
            "integrity_status": integrity_status,
            "response_ms": response_ms,
            "database_size_bytes": size_bytes,
            "database_size_mb": round(size_bytes / (1024 * 1024), 3),
            "table_count": len(table_names),
            "total_rows": total_rows,
            "journal_mode": journal_mode,
            "table_counts": counts,
            "message": "Database health check completed." if status == "HEALTHY" else "SQLite integrity check reported an issue.",
        }
    finally:
        connection.close()
def save_database_monitor_result(snapshot):
    """Persist database diagnostic check results."""
    required = {"status", "integrity_status", "response_ms", "database_size_bytes", "table_count", "total_rows", "journal_mode", "message"}
    if not isinstance(snapshot, dict) or not required.issubset(snapshot):
        raise ValueError("Invalid database monitoring snapshot payload.")
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        connection.execute("""
            INSERT INTO database_monitoring_logs
            (status, integrity_status, response_ms, database_size_bytes, table_count, total_rows, journal_mode, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(snapshot["status"]),
            str(snapshot["integrity_status"]),
            float(snapshot["response_ms"]),
            int(snapshot["database_size_bytes"]),
            int(snapshot["table_count"]),
            int(snapshot["total_rows"]),
            str(snapshot["journal_mode"])[:30],
            str(snapshot["message"])[:300],
        ))
        connection.commit()
    finally:
        connection.close()
def get_database_monitor_logs(limit=100, status="All"):
    limit = max(1, min(int(limit), 500))
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        if status and status != "All":
            rows = connection.execute("""
                SELECT id, status, integrity_status, response_ms, database_size_bytes,
                       table_count, total_rows, journal_mode, message, checked_at
                FROM database_monitoring_logs WHERE status = ? ORDER BY id DESC LIMIT ?
            """, (str(status), limit)).fetchall()
        else:
            rows = connection.execute("""
                SELECT id, status, integrity_status, response_ms, database_size_bytes,
                       table_count, total_rows, journal_mode, message, checked_at
                FROM database_monitoring_logs ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
def get_database_monitor_summary():
    if not _DB_INITIALIZED:
        initialize_database()
    connection = get_connection()
    try:
        row = connection.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'HEALTHY' THEN 1 ELSE 0 END) AS healthy,
                   SUM(CASE WHEN status IN ('ISSUE', 'ERROR') THEN 1 ELSE 0 END) AS issues,
                   AVG(response_ms) AS avg_response_ms
            FROM database_monitoring_logs
        """).fetchone()
        return {
            "total": int(row["total"] or 0),
            "healthy": int(row["healthy"] or 0),
            "issues": int(row["issues"] or 0),
            "avg_response_ms": round(float(row["avg_response_ms"]), 2) if row["avg_response_ms"] is not None else None,
        }
    finally:
        connection.close()
# =========================================================
# ATOMIC BACKUP & DISASTER RECOVERY
# =========================================================
def get_database_backup_overview():
    """Return database file size and backup metadata."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    db_exists = DB_PATH.exists()
    db_size = DB_PATH.stat().st_size if db_exists else 0
    history = get_database_backup_history()
    return {
        "database_exists": db_exists,
        "database_size_bytes": db_size,
        "database_size_mb": db_size / (1024 * 1024),
        "total_backups": len(history),
        "backup_directory": "backups/",
    }
def create_database_backup():
    """Create an atomic, integrity-verified copy using SQLite Online Backup API."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        raise FileNotFoundError("Main database file was not found.")
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    backup_name = f"backup_{timestamp}.db"
    final_path = BACKUP_DIR / backup_name
    temporary_path = BACKUP_DIR / f"{backup_name}.tmp"
    source_conn = None
    dest_conn = None
    try:
        source_conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
        dest_conn = sqlite3.connect(str(temporary_path), timeout=30.0)
        # SQLite atomic online backup
        source_conn.backup(dest_conn)
        # Integrity verification
        chk = dest_conn.execute("PRAGMA quick_check").fetchone()
        if not chk or str(chk[0]).lower() != "ok":
            raise RuntimeError("Backup integrity check failed.")
        dest_conn.close()
        dest_conn = None
        source_conn.close()
        source_conn = None
        # Atomic replace
        os.replace(temporary_path, final_path)
        size_bytes = final_path.stat().st_size
        return {
            "status": "SUCCESS",
            "file_name": backup_name,
            "created_at": datetime.fromtimestamp(final_path.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "size_bytes": size_bytes,
            "size_mb": size_bytes / (1024 * 1024),
            "message": "Atomic backup created and verified successfully.",
        }
    except Exception:
        if dest_conn is not None:
            dest_conn.close()
        if source_conn is not None:
            source_conn.close()
        if temporary_path.exists():
            temporary_path.unlink()
        raise
def get_database_backup_history():
    """List available backup snapshots ordered from newest to oldest."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    history = []
    for backup_file in BACKUP_DIR.glob("backup_*.db"):
        filename_part = backup_file.stem.replace("backup_", "", 1)
        if not filename_part or not filename_part.replace("_", "").isdigit():
            continue
        if not backup_file.is_file():
            continue
        size_bytes = backup_file.stat().st_size
        history.append({
            "File Name": backup_file.name,
            "Created At": datetime.fromtimestamp(backup_file.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "Size (MB)": round(size_bytes / (1024 * 1024), 3),
            "Status": "AVAILABLE",
        })
    history.sort(key=lambda item: item["Created At"], reverse=True)
    return history
def get_database_backup_file(filename):
    """Safely validate and return path for a requested backup file."""
    if not isinstance(filename, str):
        raise ValueError("Invalid backup filename.")
    if not filename.startswith("backup_") or not filename.endswith(".db") or Path(filename).name != filename:
        raise ValueError("Invalid or unsafe backup filename.")
    filename_part = filename[len("backup_"):-len(".db")]
    if not filename_part or not filename_part.replace("_", "").isdigit():
        raise ValueError("Invalid backup timestamp format.")
    backup_path = BACKUP_DIR / filename
    if not backup_path.is_file():
        raise FileNotFoundError("Backup file does not exist.")
    return backup_path
# =========================================================
# STANDALONE DATABASE DIAGNOSTIC TEST
# =========================================================
def test_database():
    """Diagnostic self-test to verify all 10 tables and migrated columns."""
    initialize_database(force=True)
    connection = get_connection()
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        found = {row["name"] for row in rows}
        missing = ALLOWED_TABLES - found
        print("DB Path:", DB_PATH)
        print("Tables Found:", sorted(found & ALLOWED_TABLES))
        if missing:
            print("Missing Tables:", sorted(missing))
            return False
        # Verify new subscription lifecycle columns
        user_cols = _get_table_columns(connection, "users")
        assert "subscription_plan" in user_cols
        assert "subscription_expires_at" in user_cols
        pay_cols = _get_table_columns(connection, "payments")
        assert "plan_name" in pay_cols
        assert "live_mode" in pay_cols
        print("\nAll 10 tables and subscription lifecycle columns verified successfully!")
        return True
    finally:
        connection.close()
if __name__ == "__main__":
    print("Testing Database Module with Lifecycle Columns...")
    if test_database():
        print("Database self-test PASSED successfully!")
    else:
        print("Database self-test FAILED!")
