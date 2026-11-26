import os
import sys
import json
import time
import threading
from datetime import datetime, date, timezone
from decimal import Decimal
import streamlit as st
import pandas as pd
import requests
import psutil
import plotly.express as px
import plotly.graph_objects as go
import streamlit.components.v1 as components
# Database & Authentication Imports
import auth
import database
from database import (
    get_activity_logs,
    get_activity_log_count,
    get_activity_log_actions,
    log_activity,
    get_security_logs,
    get_security_log_count,
    get_security_log_event_types,
    log_security_event,
    get_app_configurations,
    save_app_configurations,
    get_configuration_history,
    get_data_access_summary,
    get_data_access_records,
    get_data_access_status_values,
    save_api_monitoring_result,
    get_api_monitoring_logs,
    get_api_monitoring_summary,
    get_database_monitor_snapshot,
    save_database_monitor_result,
    get_database_monitor_logs,
    get_database_monitor_summary,
    get_database_backup_overview,
    create_database_backup,
    get_database_backup_history,
    get_database_backup_file,
)
from auth import (
    authenticate_user,
    register_user,
    check_subscription,
    get_user_financial_summary,
    get_user_subscription_info,
    activate_subscription,
    create_payment_record,
    get_payment_record,
    verify_admin_access,
    get_admin_dashboard_summary,
    get_all_users,
    set_payment_amount,
)
from data import get_market_data, get_24h_stats
from indicators import calculate_average, calculate_indicators
from prediction import get_signal, get_demo_prediction
import account_center
import finance
from payment_gateway import (
    verify_checkout_payment,
    mark_checkout_payment_failed,
    mark_checkout_payment_cancelled,
    mark_checkout_payment_pending,
)
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")
PAGE_ICON = LOGO_PATH if os.path.exists(LOGO_PATH) else "📈"
# =========================================================
# CENTRAL ACTIVITY TRACKING WRAPPER
# =========================================================
def _install_activity_tracking():
    def wrap(function_name, event_builder):
        original = getattr(auth, function_name, None)
        if not callable(original) or getattr(original, "_activity_wrapped", False):
            return
        def tracked(*args, **kwargs):
            result = original(*args, **kwargs)
            try:
                event = event_builder(args, kwargs, result)
                if event:
                    username, action, details = event
                    log_activity(username, action, details)
            except Exception as log_error:
                print(f"[ActivityTracking] Error in {function_name}: {log_error}")
            return result
        tracked._activity_wrapped = True
        setattr(auth, function_name, tracked)
    wrap("authenticate_user", lambda a, k, r: (
        (r.get("username") or (str(a[0]).strip() if a else "Unknown")),
        ("ADMIN_LOGIN_SUCCESS" if r.get("logged_in") and r.get("role") == "admin"
         else "USER_LOGIN_SUCCESS" if r.get("logged_in")
         else "LOGIN_FAILED"),
        "Authentication successful" if r.get("logged_in") else "Invalid credentials or inactive account"
    ))
    wrap("register_user", lambda a, k, r: (
        str(a[0]).strip() if a else "Unknown",
        "USER_REGISTERED",
        "Account registration completed" if r else "Registration was not completed"
    ) if r else None)
    wrap("activate_user", lambda a, k, r: (str(a[0]), "ACCOUNT_ACTIVATED", "Account activated") if r else None)
    wrap("deactivate_user", lambda a, k, r: (str(a[0]), "ACCOUNT_DEACTIVATED", "Account deactivated") if r else None)
    wrap("activate_subscription", lambda a, k, r: (str(a[0]), "SUBSCRIPTION_ACTIVATED", "Subscription activated") if r else None)
    wrap("deactivate_subscription", lambda a, k, r: (str(a[0]), "SUBSCRIPTION_DEACTIVATED", "Subscription deactivated") if r else None)
    wrap("create_payment_record", lambda a, k, r: (str(a[0]), "PAYMENT_RECORD_CREATED", "Payment order record created") if r else None)
    wrap("update_payment_status", lambda a, k, r: (str(a[0]), "PAYMENT_STATUS_UPDATED", f"Payment status set to {a[1] if len(a) > 1 else k.get('status', 'unknown')}") if r else None)
    wrap("create_withdrawal_request", lambda a, k, r: (str(a[0]), "WITHDRAWAL_REQUEST_CREATED", f"Withdrawal request amount: {a[1] if len(a) > 1 else k.get('amount', 'unknown')}") if r else None)
    wrap("update_withdrawal_status", lambda a, k, r: (str(a[0]), "WITHDRAWAL_STATUS_UPDATED", f"Withdrawal status set to {a[1] if len(a) > 1 else k.get('status', 'unknown')}") if r else None)
_install_activity_tracking()

def _bootstrap_admin_from_secrets():
    """Provision or sync the admin account from Streamlit Secrets or environment variables."""
    def _read_setting(key, default=""):
        try:
            if key in st.secrets:
                return str(st.secrets[key]).strip()
        except Exception:
            pass
        return str(os.getenv(key, default)).strip()

    admin_user = _read_setting("ADMIN_USERNAME")
    admin_pass = _read_setting("ADMIN_PASSWORD")
    if not admin_user or not admin_pass:
        return

    admin_email = _read_setting("ADMIN_EMAIL", f"{admin_user}@admin.local").lower()
    admin_mobile = _read_setting("ADMIN_MOBILE", "9999999999")
    password_hash = auth.hash_password(admin_pass)

    try:
        database.initialize_database()
        with database.get_connection() as connection:
            existing = connection.execute(
                "SELECT username FROM users WHERE username = ?",
                (admin_user,),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE users
                    SET password = ?, role = 'admin', active = 1, subscription = 1,
                        subscription_plan = 'Admin Unlimited'
                    WHERE username = ?
                    """,
                    (password_hash, admin_user),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO users (
                        username, email, mobile, password, role,
                        subscription, active, subscription_plan
                    )
                    VALUES (?, ?, ?, ?, 'admin', 1, 1, 'Admin Unlimited')
                    """,
                    (admin_user, admin_email, admin_mobile, password_hash),
                )
            connection.commit()
        auth.load_users_from_database()
    except Exception as bootstrap_err:
        print(f"[AdminBootstrap] Failed to provision admin: {bootstrap_err}")

_bootstrap_admin_from_secrets()
# =========================================================
# STREAMLIT PAGE CONFIGURATION & FINTECH DESIGN SYSTEM
# =========================================================
st.set_page_config(
    page_title="Market Analysis App | Quantitative Crypto Intelligence",
    page_icon=PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
html, body, [class*="css"] {
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}

#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"], [class*="viewerBadge"], .stDeployButton {
    display: none !important;
    visibility: hidden !important;
}
.stApp {
    background-color: #080C14 !important;
    color: #E2E8F0 !important;
}
.block-container {
    max-width: 1440px !important;
    padding-top: 1.5rem !important;
    padding-bottom: 3.5rem !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0D1322 0%, #080C14 100%) !important;
    border-right: 1px solid rgba(255, 255, 255, 0.08) !important;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255, 255, 255, 0.08) !important;
    margin: 1rem 0 !important;
}
h1, h2, h3, h4, h5, h6 {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
    color: #F8FAFC !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em !important;
}
h1 {
    font-size: 2.1rem !important;
    margin-bottom: 0.25rem !important;
}
.fintech-card {
    background: #0F172A;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.45);
    margin-bottom: 1rem;
    transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}
.fintech-card:hover {
    border-color: rgba(59, 130, 246, 0.35);
    box-shadow: 0 8px 30px -4px rgba(37, 99, 235, 0.15);
}
.fintech-hero-card {
    background: radial-gradient(circle at top right, rgba(37, 99, 246, 0.15), transparent 40%),
                linear-gradient(135deg, #0F172A 0%, #0A101D 100%);
    border: 1px solid rgba(59, 130, 246, 0.25);
    border-radius: 16px;
    padding: 1.5rem 1.75rem;
    margin-bottom: 1.5rem;
    box-shadow: 0 10px 30px -5px rgba(0, 0, 0, 0.5);
}
/* Kuku TV / Story TV Style Ultra-Sleek VIP Card */
.kukutv-vip-card {
    background: radial-gradient(circle at top right, rgba(245, 158, 11, 0.22), transparent 50%),
                linear-gradient(135deg, #151C2C 0%, #0C101A 100%);
    border: 2px solid #F59E0B;
    border-radius: 20px;
    padding: 2.25rem 2.5rem;
    box-shadow: 0 12px 40px -5px rgba(245, 158, 11, 0.25);
    margin: 0 auto;
    max-width: 640px;
    position: relative;
    overflow: hidden;
}
.kukutv-badge {
    background: linear-gradient(135deg, #F59E0B 0%, #D97706 100%);
    color: #000000;
    font-weight: 800;
    font-size: 0.8rem;
    padding: 5px 16px;
    border-radius: 9999px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    display: inline-block;
}
.kukutv-price {
    font-size: 3.8rem;
    font-weight: 800;
    color: #F8FAFC;
    font-family: 'JetBrains Mono', monospace;
    line-height: 1;
}
.kukutv-strike {
    color: #64748B;
    text-decoration: line-through;
    font-size: 1.6rem;
    font-weight: 500;
    margin-right: 12px;
}
[data-testid="stMetric"] {
    background: #0F172A !important;
    border: 1px solid rgba(255, 255, 255, 0.07) !important;
    padding: 1rem 1.25rem !important;
    border-radius: 14px !important;
    box-shadow: 0 4px 15px -3px rgba(0, 0, 0, 0.35) !important;
}
[data-testid="stMetricLabel"] {
    color: #94A3B8 !important;
    font-size: 0.85rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
}
[data-testid="stMetricValue"] {
    color: #F8FAFC !important;
    font-size: 1.75rem !important;
    font-weight: 700 !important;
    font-family: 'JetBrains Mono', monospace !important;
}
.badge-bullish {
    display: inline-flex;
    align-items: center;
    background: rgba(16, 185, 129, 0.15);
    color: #10B981;
    border: 1px solid rgba(16, 185, 129, 0.35);
    padding: 0.25rem 0.75rem;
    border-radius: 9999px;
    font-weight: 700;
    font-size: 0.82rem;
}
.badge-bearish {
    display: inline-flex;
    align-items: center;
    background: rgba(239, 68, 68, 0.15);
    color: #EF4444;
    border: 1px solid rgba(239, 68, 68, 0.35);
    padding: 0.25rem 0.75rem;
    border-radius: 9999px;
    font-weight: 700;
    font-size: 0.82rem;
}
.badge-neutral {
    display: inline-flex;
    align-items: center;
    background: rgba(245, 158, 11, 0.15);
    color: #F59E0B;
    border: 1px solid rgba(245, 158, 11, 0.35);
    padding: 0.25rem 0.75rem;
    border-radius: 9999px;
    font-weight: 700;
    font-size: 0.82rem;
}
.live-dot {
    width: 8px;
    height: 8px;
    background-color: #10B981;
    border-radius: 50%;
    display: inline-block;
    margin-right: 6px;
    box-shadow: 0 0 10px #10B981;
    animation: pulse 1.8s infinite;
}
@keyframes pulse {
    0% { transform: scale(0.95); opacity: 0.8; }
    50% { transform: scale(1.2); opacity: 1; }
    100% { transform: scale(0.95); opacity: 0.8; }
}
.stButton > button {
    border-radius: 12px !important;
    font-weight: 700 !important;
    padding: 0.7rem 1.5rem !important;
    transition: all 0.2s ease !important;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #F59E0B 0%, #D97706 100%) !important;
    border: 1px solid #F59E0B !important;
    color: #000000 !important;
    font-weight: 800 !important;
    box-shadow: 0 4px 15px rgba(245, 158, 11, 0.4) !important;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #FBBF24 0%, #F59E0B 100%) !important;
    box-shadow: 0 6px 25px rgba(245, 158, 11, 0.6) !important;
    transform: translateY(-2px);
}
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stSelectbox > div > div {
    background-color: #0F172A !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    color: #F8FAFC !important;
    border-radius: 10px !important;
}
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background-color: #0F172A;
    padding: 6px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.06);
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px;
    color: #94A3B8;
    font-weight: 600;
    padding: 8px 18px;
}
.stTabs [aria-selected="true"] {
    background-color: #1E293B !important;
    color: #FFFFFF !important;
}
[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 12px !important;
    background-color: #0F172A !important;
}
hr {
    border: none !important;
    height: 1px !important;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent) !important;
    margin: 1.5rem 0 !important;
}
</style>
""", unsafe_allow_html=True)
# =========================================================
# BINANCE PUBLIC MARKET DATA STREAM & WORKER
# =========================================================
BINANCE_DATA_URL = "https://data-api.binance.vision"

@st.cache_data(ttl=15, show_spinner=False)
def get_binance_klines(symbol, interval="1m", limit=1000):
    """Fetch public Binance Spot OHLCV candles for the chart (read-only)."""
    allowed_intervals = {"1m", "5m", "15m", "1h", "4h", "1d", "1w", "1M"}
    if interval not in allowed_intervals:
        raise ValueError("Unsupported chart timeframe.")
    response = requests.get(
        f"{BINANCE_DATA_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": min(int(limit), 1000)},
        timeout=12,
    )
    response.raise_for_status()
    raw_rows = response.json()
    return [{
        "time": int(row[0] // 1000),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
    } for row in raw_rows]

API_HEALTH_TARGETS = [
    {"service": "Binance Market Data - Ping", "url": f"{BINANCE_DATA_URL}/api/v3/ping"},
    {"service": "Binance Market Data - Server Time", "url": f"{BINANCE_DATA_URL}/api/v3/time"},
]
@st.cache_data(ttl=600)
def get_all_usdt_symbols():
    try:
        response = requests.get(f"{BINANCE_DATA_URL}/api/v3/exchangeInfo", timeout=10)
        response.raise_for_status()
        data = response.json()
        symbols = []
        for item in data.get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("quoteAsset") == "USDT"
                and item.get("isSpotTradingAllowed", True)
            ):
                symbols.append(item["symbol"])
        return sorted(symbols)
    except Exception:
        return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"]
class LiveMarketStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.market_data = {}
        self.stats = {}
        self.all_tickers = []
        self.selected_symbol = "BTCUSDT"
        self.running = True
        self.last_ticker_update = 0.0
        self.last_symbol_update = 0.0
        self.thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="MarketDataWorker",
        )
        self.thread.start()
    def set_symbol(self, symbol):
        with self.lock:
            changed = self.selected_symbol != symbol
            self.selected_symbol = symbol
        if changed:
            self.last_symbol_update = 0.0
    def _fetch_symbol(self, symbol):
        try:
            m_data = get_market_data(symbol)
            m_stats = get_24h_stats(symbol)
            if m_data and m_stats:
                with self.lock:
                    self.market_data[symbol] = m_data
                    self.stats[symbol] = m_stats
        except Exception:
            pass
    def _fetch_all_tickers(self):
        try:
            response = requests.get(f"{BINANCE_DATA_URL}/api/v3/ticker/24hr", timeout=15)
            response.raise_for_status()
            data = response.json()
            rows = []
            for item in data:
                symbol_name = item.get("symbol", "")
                if symbol_name.endswith("USDT"):
                    rows.append({
                        "Symbol": symbol_name,
                        "Price": float(item.get("lastPrice", 0)),
                        "24h Change": float(item.get("priceChangePercent", 0)),
                        "24h High": float(item.get("highPrice", 0)),
                        "24h Low": float(item.get("lowPrice", 0)),
                        "Volume": float(item.get("volume", 0)),
                        "Quote Volume": float(item.get("quoteVolume", 0)),
                    })
            if rows:
                with self.lock:
                    self.all_tickers = rows
                    self.last_ticker_update = time.time()
        except Exception:
            pass
    def _worker(self):
        while self.running:
            try:
                with self.lock:
                    selected = self.selected_symbol
                symbols_to_update = {selected, "BTCUSDT", "ETHUSDT", "BNBUSDT"}
                for sym in symbols_to_update:
                    self._fetch_symbol(sym)
                if time.time() - self.last_ticker_update >= 30:
                    self._fetch_all_tickers()
                with self.lock:
                    self.last_symbol_update = time.time()
            except Exception:
                pass
            time.sleep(5)
    def seed_symbol(self, symbol, m_data, m_stats):
        with self.lock:
            if m_data:
                self.market_data[symbol] = m_data
            if m_stats:
                self.stats[symbol] = m_stats
    def get_market_data(self, symbol):
        with self.lock:
            return self.market_data.get(symbol, [])
    def get_stats(self, symbol):
        with self.lock:
            return self.stats.get(symbol, {})
    def get_all_tickers(self):
        with self.lock:
            return list(self.all_tickers)
@st.cache_resource(show_spinner=False)
def get_live_market_store():
    return LiveMarketStore()
live_store = get_live_market_store()
def get_live_market_data(symbol):
    return live_store.get_market_data(symbol)
def get_live_24h_stats(symbol):
    return live_store.get_stats(symbol)
def get_all_usdt_market_tickers():
    return live_store.get_all_tickers()
if hasattr(st, "fragment"):
    fragment_decorator = st.fragment(run_every="5s")
elif hasattr(st, "experimental_fragment"):
    fragment_decorator = st.experimental_fragment(run_every="5s")
else:
    def fragment_decorator(func):
        return func
# =========================================================
# SAFE URL QUERY PARAMETER HELPER
# =========================================================
def get_query_param(key):
    try:
        if hasattr(st, "query_params"):
            val = st.query_params.get(key)
            if isinstance(val, list):
                return val[0] if val else None
            return val
        elif hasattr(st, "experimental_get_query_params"):
            params = st.experimental_get_query_params()
            val = params.get(key)
            return val[0] if val else None
    except Exception:
        return None
    return None
def clear_query_params():
    try:
        if hasattr(st, "query_params"):
            st.query_params.clear()
        elif hasattr(st, "experimental_set_query_params"):
            st.experimental_set_query_params()
    except Exception:
        pass
# =========================================================
# SESSION STATE INITIALIZATION
# =========================================================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""
if "role" not in st.session_state:
    st.session_state.role = ""
if "selected_market" not in st.session_state:
    st.session_state.selected_market = "BTCUSDT"
# =========================================================
# AUTHENTICATION PORTAL (LOGIN & REGISTRATION)
# =========================================================
if not st.session_state.logged_in:
    col_logo, col_stat = st.columns([2.5, 1])
    with col_logo:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=92)
        st.markdown("""
        <div style="margin-bottom: 0.5rem;">
            <h1 style="margin: 0; font-size: 2rem;">Market Analysis App</h1>
            <p style="color: #94A3B8; font-size: 0.95rem;">Educational Technical Charting &amp; Market Data Analytics Software</p>
        </div>
        """, unsafe_allow_html=True)
    with col_stat:
        st.markdown("""
        <div style="text-align: right; padding-top: 10px;">
            <span class="badge-bullish">● BINANCE PUBLIC DATA</span>
            <div style="color: #64748B; font-size: 0.8rem; margin-top: 4px;">PBKDF2-SHA256 Encrypted</div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown("<hr style='margin: 1.25rem 0;'>", unsafe_allow_html=True)
    auth_container_col1, auth_container_col2, auth_container_col3 = st.columns([1, 1.8, 1])
    with auth_container_col2:
        st.markdown("""
        <div class="fintech-card" style="padding: 1.75rem 2rem;">
            <h3 style="margin-top: 0; margin-bottom: 0.5rem; text-align: center;">Sign in or create an account</h3>
            <p style="color: #94A3B8; text-align: center; font-size: 0.88rem; margin-bottom: 1.5rem;">
                Use your username and password to open the market terminal, VIP status, payments, and account settings.
            </p>
        </div>
        """, unsafe_allow_html=True)
        login_tab, signup_tab = st.tabs(["🔐 Sign In", "✨ Create New Account"])
        with login_tab:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.form("login_form", clear_on_submit=False):
                login_user = st.text_input("Username", placeholder="Enter your username", key="login_username")
                login_pass = st.text_input("Password", type="password", placeholder="••••••••", key="login_password")
                submit_login = st.form_submit_button("Sign In to Terminal", type="primary", use_container_width=True)
            if submit_login:
                user_clean = login_user.strip()
                auth.load_users_from_database()
                auth_res = authenticate_user(user_clean, login_pass)
                if auth_res.get("logged_in"):
                    st.session_state.logged_in = True
                    st.session_state.username = auth_res["username"]
                    st.session_state.role = auth_res["role"]
                    st.success("Authentication successful! Loading terminal...")
                    st.rerun()
                else:
                    st.error("Invalid username or password. Please verify your credentials or check if your account is active.")
        with signup_tab:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.form("signup_form", clear_on_submit=False):
                new_username = st.text_input("Choose Username", placeholder="e.g. trader_pro", key="signup_username")
                new_email = st.text_input("Email Address", placeholder="name@example.com", key="signup_email")
                new_mobile = st.text_input("Mobile Number", placeholder="+919876543210 (10-15 digits)", key="signup_mobile")
                c_p1, c_p2 = st.columns(2)
                with c_p1:
                    new_password = st.text_input("Create Password", type="password", placeholder="Min 8 characters", key="signup_password")
                with c_p2:
                    confirm_password = st.text_input("Confirm Password", type="password", placeholder="Repeat password", key="signup_confirm_password")
                submit_signup = st.form_submit_button("Create Free Account", type="primary", use_container_width=True)
            if submit_signup:
                if not all([new_username.strip(), new_email.strip(), new_mobile.strip(), new_password, confirm_password]):
                    st.error("Please fill in all required fields.")
                elif len(new_password) < 8:
                    st.error("Password must be at least 8 characters long.")
                elif new_password != confirm_password:
                    st.error("Passwords do not match. Please re-enter carefully.")
                else:
                    registered = register_user(
                        new_username.strip(),
                        new_email.strip(),
                        new_mobile.strip(),
                        new_password,
                    )
                    if registered:
                        st.success("Account created successfully! Please click on 'Sign In' tab to log into your account.")
                    else:
                        st.error("Registration failed. Username, email, or mobile might already be registered, or format is invalid.")
    st.markdown("<br><hr>", unsafe_allow_html=True)
    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        st.markdown("<div style='font-size: 0.85rem; color: #94A3B8;'><b style='color: #F8FAFC;'>⚡ Real-time Data:</b> Binance Spot API integration.</div>", unsafe_allow_html=True)
    with f_col2:
        st.markdown("<div style='font-size: 0.85rem; color: #94A3B8;'><b style='color: #F8FAFC;'>🔒 Payments:</b> Razorpay checkout. Live payouts stay off until authorized.</div>", unsafe_allow_html=True)
    with f_col3:
        st.markdown("<div style='font-size: 0.85rem; color: #94A3B8;'><b style='color: #F8FAFC;'>📊 Technical Indicators:</b> SMA, EMA, RSI &amp; MACD learning tools.</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    p_tab1, p_tab2, p_tab3, p_tab4 = st.tabs(["🔒 Privacy Policy", "📜 Terms & Conditions", "💸 Refund Policy", "📞 Contact Us"])
    with p_tab1:
        st.caption("**Privacy Policy:** Market Analysis App collects only your username, email, and mobile number for account authentication and subscription management. We do not sell or share personal data with third parties. All passwords are encrypted using PBKDF2-HMAC-SHA256.")
    with p_tab2:
        st.caption("**Terms & Conditions:** Market Analysis App is an educational data visualization and technical charting software. It does not offer financial advice, brokerage, or real-money trading services. By using this platform, you agree to use the analytics strictly for educational purposes.")
    with p_tab3:
        st.caption("**Refund & Cancellation Policy:** Subscription passes (e.g., 5-Day VIP Educational Pass) grant immediate digital access to charting tools. Cancellations stop future renewals. For duplicate charges or technical billing issues, contact support within 7 days for a full refund.")
    with p_tab4:
        st.caption("**Contact Us:** Developer / Support: Gaurav Rajput | Email: gauravrajput40167@gmail.com | Response time: 24–48 business hours.")
    st.stop()
# =========================================================
# SIDEBAR - ACCOUNT, TELEMETRY & SUBSCRIPTION MONITOR
# =========================================================
current_user = st.session_state.username
current_role = st.session_state.role
if os.path.exists(LOGO_PATH):
    st.sidebar.image(LOGO_PATH, width=48)
st.sidebar.markdown("""
<div style="padding: 0.25rem 0 0.75rem 0;">
    <div style="font-weight: 700; color: #F8FAFC; font-size: 1.1rem; line-height: 1.2;">Market Analysis App</div>
    <div style="color: #64748B; font-size: 0.75rem;">SaaS Analytics Pro</div>
</div>
""", unsafe_allow_html=True)
# Fetch Subscription Telemetry
sub_info = get_user_subscription_info(current_user)
is_sub_active = sub_info["is_active"]
role_badge = f"<span class='badge-bullish' style='font-size: 0.72rem;'>{current_role.upper()}</span>" if current_role == "admin" else "<span class='badge-neutral' style='font-size: 0.72rem;'>USER</span>"
if is_sub_active:
    sub_badge = f"<span style='color: #10B981; font-weight: 600;'>● Active ({sub_info['days_remaining']}d remaining)</span>"
else:
    sub_badge = "<span style='color: #EF4444; font-weight: 600;'>✕ Expired</span>" if sub_info["status"] == "Expired" else "<span style='color: #F59E0B; font-weight: 600;'>○ Inactive (No Pass)</span>"
st.sidebar.markdown(f"""
<div class="fintech-card" style="padding: 0.85rem 1rem; margin-bottom: 1rem;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <span style="font-weight: 700; color: #F8FAFC; font-size: 0.95rem;">{current_user}</span>
        {role_badge}
    </div>
    <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: #94A3B8; margin-bottom: 4px;">
        <span>Plan:</span>
        <span style="color: #F8FAFC; font-weight: 600;">{sub_info.get('plan', 'VIP 5-Day Pass')}</span>
    </div>
    <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: #94A3B8;">
        <span>Status:</span>
        <span>{sub_badge}</span>
    </div>
</div>
""", unsafe_allow_html=True)
if st.sidebar.button("🚪 Sign Out", use_container_width=True):
    log_activity(
        st.session_state.username or None,
        "USER_LOGOUT",
        "User signed out" if current_role != "admin" else "Admin signed out",
    )
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.rerun()
# =========================================================
# PAYMENT CALLBACKS DISABLED
# This build does not create or verify real-money payment transactions.
# =========================================================
# =========================================================
# ADMIN CONTROL PANEL ROUTER (ALL 10 COMPLETE MODULES)
# =========================================================
if current_role == "admin":
    if not verify_admin_access(current_user):
        st.error("Admin access denied or account disabled.")
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""
        st.rerun()
    st.sidebar.markdown("<hr>", unsafe_allow_html=True)
    st.sidebar.markdown("<div style='font-size: 0.8rem; font-weight: 700; color: #94A3B8; margin-bottom: 0.5rem; text-transform: uppercase;'>Admin Operations</div>", unsafe_allow_html=True)
    admin_menu = st.sidebar.radio(
        "Navigation",
        [
            "📊 Admin Dashboard",
            "👥 User Management",
            "🖥️ System Monitoring",
            "📋 Activity Logs",
            "🔐 Security Logs",
            "⚙️ App Configuration",
            "🗂️ Data Access",
            "🌐 API Monitoring",
            "🗄️ Database Monitoring",
            "💾 Backup Status",
            "📈 Live Market Analysis App",
        ],
        key="admin_navigation_radio",
        label_visibility="collapsed",
    )
    st.sidebar.markdown("<hr>", unsafe_allow_html=True)
    st.sidebar.caption("System Mode: Admin Security Guard Active")
    # -----------------------------------------------------
    # 1. ADMIN DASHBOARD
    # -----------------------------------------------------
    if admin_menu == "📊 Admin Dashboard":
        st.markdown("""
        <div class="fintech-hero-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h1 style="margin: 0; font-size: 1.85rem;">📊 Executive Admin Dashboard</h1>
                <span class='badge-bullish'>● PAYMENT RECORDS OVERVIEW</span>
            </div>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">
                Real-time database telemetry and 5-Day VIP subscription payment oversight
            </p>
        </div>
        """, unsafe_allow_html=True)
        summary = get_admin_dashboard_summary(current_user)
        if summary:
            total_users = int(summary["total_users"] or 0)
            active_users = int(summary["active_users"] or 0)
            inactive_users = int(summary["inactive_users"] or 0)
            active_subs = int(summary.get("active_subscriptions", 0) or 0)
            expired_subs = int(summary.get("expired_subscriptions", 0) or 0)
            total_payments = int(summary["total_payments"] or 0)
            successful_payments = int(summary["successful_payments"] or 0)
            pending_payments = int(summary["pending_payments"] or 0)
            live_payments = int(summary.get("live_payments", 0) or 0)
            total_withdrawals = int(summary["total_withdrawals"] or 0)
            pending_withdrawals = int(summary["pending_withdrawals"] or 0)
            completed_withdrawals = int(summary["completed_withdrawals"] or 0)
            st.subheader("👥 User Account & VIP Subscription Overview")
            u_c1, u_c2, u_c3, u_c4 = st.columns(4)
            u_c1.metric("Total Users", f"{total_users:,}")
            u_c2.metric("Active Accounts", f"{active_users:,}")
            u_c3.metric("Active 5-Day Passes", f"{active_subs:,}")
            u_c4.metric("Expired Passes", f"{expired_subs:,}")
            st.markdown("<hr>", unsafe_allow_html=True)
            st.subheader("💳 ₹2 Payment Operations & Gateway Telemetry")
            p_c1, p_c2, p_c3, p_c4 = st.columns(4)
            p_c1.metric("Total Transactions", f"{total_payments:,}")
            p_c2.metric("Verified Successful", f"{successful_payments:,}")
            p_c3.metric("Live Real Payments", f"{live_payments:,}")
            p_c4.metric("Pending Verifications", f"{pending_payments:,}")
            # Plotly Charts: Users, Payments, Withdrawals
            st.markdown("<br>", unsafe_allow_html=True)
            chart_c1, chart_c2, chart_c3 = st.columns(3)
            with chart_c1:
                st.markdown("<div class='fintech-card'>", unsafe_allow_html=True)
                st.caption("User Distribution")
                fig_users = px.pie(
                    names=["Active Users", "Inactive Users"],
                    values=[active_users, max(0, total_users - active_users)],
                    hole=0.5,
                    color_discrete_sequence=["#10B981", "#64748B"]
                )
                fig_users.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#94A3B8"))
                st.plotly_chart(fig_users, use_container_width=True)
                st.markdown("</div>", unsafe_allow_html=True)
            with chart_c2:
                st.markdown("<div class='fintech-card'>", unsafe_allow_html=True)
                st.caption("Transactions Breakdown")
                fig_pay = px.bar(
                    x=["Successful", "Pending"],
                    y=[successful_payments, pending_payments],
                    color=["Successful", "Pending"],
                    color_discrete_map={"Successful": "#10B981", "Pending": "#F59E0B"}
                )
                fig_pay.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94A3B8"), showlegend=False)
                st.plotly_chart(fig_pay, use_container_width=True)
                st.markdown("</div>", unsafe_allow_html=True)
            with chart_c3:
                st.markdown("<div class='fintech-card'>", unsafe_allow_html=True)
                st.caption("Withdrawal Overview")
                fig_with = px.bar(
                    x=["Pending", "Completed"],
                    y=[pending_withdrawals, completed_withdrawals],
                    color=["Pending", "Completed"],
                    color_discrete_map={"Pending": "#F59E0B", "Completed": "#10B981"}
                )
                fig_with.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94A3B8"), showlegend=False)
                st.plotly_chart(fig_with, use_container_width=True)
                st.markdown("</div>", unsafe_allow_html=True)
            st.markdown("<hr>", unsafe_allow_html=True)
            st.subheader("💰 User Withdrawal Requests")
            w_c1, w_c2, w_c3 = st.columns(3)
            w_c1.metric("Total Requests", f"{total_withdrawals:,}")
            w_c2.metric("Pending Approvals", f"{pending_withdrawals:,}")
            w_c3.metric("Completed Disbursals", f"{completed_withdrawals:,}")
        else:
            st.warning("Admin statistics are currently unavailable from the database.")
        st.stop()
    # -----------------------------------------------------
    # 2. USER MANAGEMENT
    # -----------------------------------------------------
    elif admin_menu == "👥 User Management":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">👥 User Directory & 5-Day VIP Lifecycle</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Inspect registered user accounts, active VIP pass status, and expiry dates.</p>
        </div>
        """, unsafe_allow_html=True)
        users = get_all_users(current_user)
        if users:
            df = pd.DataFrame(users)
            search_username = st.text_input("🔍 Search User by Username or Email", placeholder="Type username or email...")
            if search_username:
                df = df[
                    df["username"].str.contains(search_username, case=False, na=False) |
                    df["email"].str.contains(search_username, case=False, na=False)
                ]
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "username": st.column_config.TextColumn("Username"),
                    "email": st.column_config.TextColumn("Email Address"),
                    "mobile": st.column_config.TextColumn("Mobile Number"),
                    "role": st.column_config.TextColumn("Role"),
                    "subscription": st.column_config.CheckboxColumn("VIP Active"),
                    "subscription_plan": st.column_config.TextColumn("Plan"),
                    "subscription_expires_at": st.column_config.TextColumn("Expires At (UTC)"),
                    "active": st.column_config.CheckboxColumn("Account Active"),
                }
            )
            c1, c2 = st.columns([3, 1])
            with c1:
                st.caption(f"Total matching users: {len(df):,}")
            with c2:
                st.download_button(
                    "⬇️ Export Users CSV",
                    df.to_csv(index=False).encode("utf-8-sig"),
                    file_name="users_export.csv",
                    mime="text/csv",
                    use_container_width=True
                )
        else:
            st.info("No registered users found in SQLite database.")
        st.stop()
    # -----------------------------------------------------
    # 3. SYSTEM MONITORING
    # -----------------------------------------------------
    elif admin_menu == "🖥️ System Monitoring":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">🖥️ System Telemetry & Performance</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Real-time resource utilization, CPU, RAM and hardware runtime statistics.</p>
        </div>
        """, unsafe_allow_html=True)
        cpu_usage = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        col1, col2, col3 = st.columns(3)
        col1.metric("CPU Utilization", f"{cpu_usage:.1f}%")
        col2.metric("RAM In-Use", f"{memory.percent:.1f}%")
        col3.metric("Available Physical RAM", f"{memory.available / (1024 ** 3):.2f} GB")
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("Platform System Specifications")
        sys_info = pd.DataFrame([
            {"Parameter": "Application Name", "Specification": "Market Analysis App (Fintech SaaS)"},
            {"Parameter": "Framework Engine", "Specification": f"Streamlit v{st.__version__}"},
            {"Parameter": "Database Engine", "Specification": "SQLite 3 (WAL Non-Blocking Mode)"},
            {"Parameter": "Telemetry Service", "Specification": "psutil Host Diagnostics"},
            {"Parameter": "Active PID", "Specification": str(os.getpid())},
            {"Parameter": "Python Runtime", "Specification": sys.version.split()[0]},
        ])
        st.table(sys_info)
        st.stop()
    # -----------------------------------------------------
    # 4. ACTIVITY LOGS
    # -----------------------------------------------------
    elif admin_menu == "📋 Activity Logs":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">📋 Audit & Activity Logs</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Comprehensive audit trail of user actions, payments, and administrative events.</p>
        </div>
        """, unsafe_allow_html=True)
        filter_col1, filter_col2, filter_col3 = st.columns([1.5, 1, 1])
        with filter_col1:
            username_filter = st.text_input("Search Username", placeholder="Filter by username...", key="act_user_filter")
        with filter_col2:
            action_options = ["All"] + get_activity_log_actions()
            action_filter = st.selectbox("Action Type", action_options, key="act_action_filter")
        with filter_col3:
            limit_filter = st.selectbox("Record Limit", [100, 250, 500, 1000, 5000], index=0, key="act_limit_filter")
        date_col1, date_col2, date_col3 = st.columns([1, 1, 1])
        with date_col1:
            start_filter = st.date_input("From Date", value=None, key="act_start_date")
        with date_col2:
            end_filter = st.date_input("To Date", value=None, key="act_end_date")
        with date_col3:
            st.write("")
            st.write("")
            if st.button("🔄 Refresh Activity Logs", use_container_width=True):
                st.rerun()
        start_val = start_filter.isoformat() if start_filter else None
        end_val = end_filter.isoformat() if end_filter else None
        total_matching = get_activity_log_count(username=username_filter, action=action_filter, start_date=start_val, end_date=end_val)
        logs = get_activity_logs(limit=limit_filter, username=username_filter, action=action_filter, start_date=start_val, end_date=end_val)
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Activities", f"{total_matching:,}")
        m2.metric("Displayed Records", f"{len(logs):,}")
        m3.metric("Limit", f"{limit_filter:,}")
        st.markdown("<hr>", unsafe_allow_html=True)
        if logs:
            activity_df = pd.DataFrame(logs)
            st.dataframe(activity_df, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Export Activity Logs as CSV",
                activity_df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"activity_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("No activity records match the selected filters.")
        st.stop()
    # -----------------------------------------------------
    # 5. SECURITY LOGS
    # -----------------------------------------------------
    elif admin_menu == "🔐 Security Logs":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">🔐 Security Event Logs</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Authentication attempts, signature verifications, and cryptographic security events.</p>
        </div>
        """, unsafe_allow_html=True)
        f1, f2 = st.columns(2)
        with f1:
            security_user = st.text_input("Filter by Username", key="sec_user_filter")
        with f2:
            event_types = ["All"] + get_security_log_event_types()
            security_event = st.selectbox("Security Event Type", event_types, key="sec_event_filter")
        total = get_security_log_count(username=security_user, event_type=security_event)
        security_rows = get_security_logs(limit=500, username=security_user, event_type=security_event)
        m1, m2 = st.columns(2)
        m1.metric("Matching Security Events", f"{total:,}")
        m2.metric("Displayed Events", f"{len(security_rows):,}")
        st.markdown("<hr>", unsafe_allow_html=True)
        if security_rows:
            security_df = pd.DataFrame(security_rows)
            st.dataframe(security_df, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Export Security Logs (CSV)",
                security_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="security_logs.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("No security events recorded matching criteria.")
        st.stop()
    # -----------------------------------------------------
    # 6. APP CONFIGURATION (FULL INTERACTIVE FORM)
    # -----------------------------------------------------
    elif admin_menu == "⚙️ App Configuration":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">⚙️ Application Global Configuration</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Manage application preferences. Changes are stored in SQLite with full audit history.</p>
        </div>
        """, unsafe_allow_html=True)
        try:
            config_rows = get_app_configurations()
            cfg = {key: row["config_value"] for key, row in config_rows.items()}
            with st.form("app_configuration_form"):
                st.subheader("General Settings")
                c1, c2 = st.columns(2)
                with c1:
                    app_name = st.text_input("Application Name", cfg.get("app_name", "Market Analysis App"), max_chars=100)
                    support_email = st.text_input("Support Email", cfg.get("support_email", "support@market.terminal"))
                    currency_list = ["INR", "USD", "EUR", "GBP"]
                    currency_idx = currency_list.index(cfg.get("currency", "INR")) if cfg.get("currency", "INR") in currency_list else 0
                    currency = st.selectbox("Display Currency", currency_list, index=currency_idx)
                with c2:
                    app_description = st.text_area("Application Description", cfg.get("app_description", "Quantitative Market Analytics Terminal"), max_chars=300)
                    tz_list = ["Asia/Kolkata", "UTC", "Asia/Dubai", "Europe/London", "America/New_York"]
                    tz_idx = tz_list.index(cfg.get("timezone", "Asia/Kolkata")) if cfg.get("timezone", "Asia/Kolkata") in tz_list else 0
                    timezone_val = st.selectbox("Timezone", tz_list, index=tz_idx)
                st.markdown("<hr>", unsafe_allow_html=True)
                st.subheader("Application Controls")
                c1, c2 = st.columns(2)
                with c1:
                    maintenance = st.checkbox("Maintenance Mode", cfg.get("maintenance_mode", "false") == "true")
                    registration = st.checkbox("Allow New Registrations", cfg.get("registration_enabled", "true") == "true")
                    login_enabled = st.checkbox("Allow User Login", cfg.get("login_enabled", "true") == "true")
                with c2:
                    subscription = st.checkbox("Enable 5-Day VIP Subscription Feature", cfg.get("subscription_enabled", "true") == "true")
                    ref_list = [5, 10, 15, 30, 60]
                    cur_ref = int(cfg.get("refresh_interval", "5")) if str(cfg.get("refresh_interval", "5")).isdigit() else 5
                    refresh_idx = ref_list.index(cur_ref) if cur_ref in ref_list else 0
                    refresh = st.selectbox("Dashboard Refresh Interval (seconds)", ref_list, index=refresh_idx)
                st.markdown("<hr>", unsafe_allow_html=True)
                st.subheader("Notification Preferences")
                n1, n2 = st.columns(2)
                with n1:
                    notify_login = st.checkbox("Log-in Notifications", cfg.get("notification_login", "true") == "true")
                    notify_registration = st.checkbox("Registration Notifications", cfg.get("notification_registration", "true") == "true")
                with n2:
                    notify_payment = st.checkbox("Payment Notifications", cfg.get("notification_payment", "true") == "true")
                    notify_withdrawal = st.checkbox("Withdrawal Notifications", cfg.get("notification_withdrawal", "true") == "true")
                submitted = st.form_submit_button("💾 Save Configuration Changes", type="primary", use_container_width=True)
                if submitted:
                    if not app_name.strip():
                        st.error("Application Name cannot be empty.")
                    else:
                        values = {
                            "app_name": app_name.strip(),
                            "app_description": app_description.strip(),
                            "support_email": support_email.strip(),
                            "currency": currency,
                            "timezone": timezone_val,
                            "maintenance_mode": str(maintenance).lower(),
                            "registration_enabled": str(registration).lower(),
                            "login_enabled": str(login_enabled).lower(),
                            "subscription_enabled": str(subscription).lower(),
                            "refresh_interval": str(refresh),
                            "notification_login": str(notify_login).lower(),
                            "notification_registration": str(notify_registration).lower(),
                            "notification_payment": str(notify_payment).lower(),
                            "notification_withdrawal": str(notify_withdrawal).lower(),
                        }
                        save_app_configurations(values, current_user)
                        log_activity(current_user, "APP_CONFIGURATION_UPDATED", "Updated system configurations.")
                        st.success("Application configuration saved successfully!")
                        time.sleep(1)
                        st.rerun()
            st.markdown("<hr>", unsafe_allow_html=True)
            st.subheader("Configuration Audit Trail")
            history = get_configuration_history(100)
            if history:
                st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
            else:
                st.info("No configuration changes recorded yet.")
        except Exception as error:
            st.error(f"Unable to load App Configuration: {error}")
        st.stop()
    # -----------------------------------------------------
    # 7. DATA ACCESS (TABLE EXPLORER & CSV EXPORTER)
    # -----------------------------------------------------
    elif admin_menu == "🗂️ Data Access":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">🗂️ Read-Only Data Explorer</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Direct read-only inspection of SQLite system collections. Sensitive credentials are hidden.</p>
        </div>
        """, unsafe_allow_html=True)
        try:
            summary = get_data_access_summary()
            metric_cols = st.columns(4)
            for index, (label, count) in enumerate(summary.items()):
                metric_cols[index % 4].metric(label, f"{count:,}")
            st.markdown("<hr>", unsafe_allow_html=True)
            dataset = st.selectbox("Select Data Collection", list(summary.keys()), key="data_access_dataset")
            f1, f2, f3 = st.columns([2, 1, 1])
            with f1:
                search_text = st.text_input("Search records", placeholder="Filter username, email, ID...", key="data_access_search")
            with f2:
                status_values = ["All"] + get_data_access_status_values(dataset)
                status_filter = st.selectbox("Status Filter", status_values, key="data_access_status")
            with f3:
                row_limit = st.selectbox("Rows to display", [100, 250, 500, 1000], index=1, key="data_access_limit")
            date_start = date_end = None
            if dataset in ["Payments", "Withdrawals", "Payment Attempts", "Activity Logs", "Security Logs"]:
                d1, d2 = st.columns(2)
                with d1:
                    date_start = st.date_input("From date", value=None, key="da_start_date")
                with d2:
                    date_end = st.date_input("To date", value=None, key="da_end_date")
            records = get_data_access_records(
                dataset=dataset,
                search_text=search_text,
                status=status_filter,
                start_date=date_start.isoformat() if date_start else None,
                end_date=date_end.isoformat() if date_end else None,
                limit=row_limit,
            )
            st.subheader(f"{dataset} Records")
            if records:
                data_frame = pd.DataFrame(records)
                st.dataframe(data_frame, use_container_width=True, hide_index=True)
                st.download_button(
                    f"⬇️ Export {dataset} (CSV)",
                    data_frame.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"{dataset.lower().replace(' ', '_')}_export.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            else:
                st.info("No records found for the selected criteria.")
        except Exception as error:
            st.error(f"Unable to load Data Access: {error}")
        st.stop()
    # -----------------------------------------------------
    # 8. API MONITORING
    # -----------------------------------------------------
    elif admin_menu == "🌐 API Monitoring":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">🌐 External API Availability Monitor</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Verify Binance Spot Public API reachability, HTTP latency, and status codes.</p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("▶ Run Full API Health Checks", type="primary"):
            progress = st.progress(0)
            for check_index, target in enumerate(API_HEALTH_TARGETS):
                started = time.perf_counter()
                http_status = None
                try:
                    res = requests.get(target["url"], timeout=6)
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    http_status = int(res.status_code)
                    result_status = "UP" if res.ok else "DOWN"
                    msg = "Endpoint responded successfully." if res.ok else f"HTTP {http_status}"
                except Exception as err:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    result_status = "DOWN"
                    msg = str(err)
                save_api_monitoring_result(
                    service_name=target["service"],
                    endpoint=target["url"],
                    status=result_status,
                    http_status=http_status,
                    response_ms=elapsed_ms,
                    message=msg
                )
                progress.progress((check_index + 1) / len(API_HEALTH_TARGETS))
            st.success("API health checks completed successfully!")
        summary = get_api_monitoring_summary()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Checks", f"{summary['total']:,}")
        m2.metric("Successful Checks", f"{summary['up']:,}")
        m3.metric("Issues", f"{summary['issues']:,}")
        avg_resp = f"{summary['avg_response_ms']:.2f} ms" if summary.get("avg_response_ms") is not None else "N/A"
        m4.metric("Avg Response Time", avg_resp)
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("API Monitoring History")
        f1, f2, f3 = st.columns([2, 1, 1])
        with f1:
            service_filter = st.selectbox("Service", ["All"] + [item["service"] for item in API_HEALTH_TARGETS], key="api_srv_filter")
        with f2:
            status_filter = st.selectbox("Result Status", ["All", "UP", "DOWN", "ERROR"], key="api_res_filter")
        with f3:
            hist_limit = st.selectbox("Row Limit", [25, 50, 100, 250, 500], index=2, key="api_lim_filter")
        api_logs = get_api_monitoring_logs(limit=hist_limit, service_name=service_filter, status=status_filter)
        if api_logs:
            api_frame = pd.DataFrame(api_logs)
            st.dataframe(api_frame, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Export API Monitoring Logs",
                api_frame.to_csv(index=False).encode("utf-8-sig"),
                file_name="api_monitoring_history.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("No API checks recorded yet. Press the button above to run checks.")
        st.stop()
    # -----------------------------------------------------
    # 9. DATABASE MONITORING
    # -----------------------------------------------------
    elif admin_menu == "🗄️ Database Monitoring":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">🗄️ SQLite Database Health & Diagnostics</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Integrity diagnostics, table row counts, file size metrics, and connection latency.</p>
        </div>
        """, unsafe_allow_html=True)
        if st.button("▶ Run Full Database Integrity Check", type="primary"):
            snap = get_database_monitor_snapshot(run_integrity_check=True)
            save_database_monitor_result(snap)
            if snap["status"] == "HEALTHY":
                st.success("Database check passed! Integrity verified.")
            else:
                st.error(f"Database alert: {snap.get('message')}")
        current_snap = get_database_monitor_snapshot(run_integrity_check=False)
        db_sum = get_database_monitor_summary()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Database Health", current_snap["status"])
        c2.metric("Database File Size", f"{current_snap['database_size_mb']:.3f} MB")
        c3.metric("Tracked Tables", f"{current_snap['table_count']:,}")
        c4.metric("Total Records", f"{current_snap['total_rows']:,}")
        c5, c6, c7 = st.columns(3)
        c5.metric("Query Latency", f"{current_snap['response_ms']:.2f} ms")
        c6.metric("Journal Mode", current_snap["journal_mode"])
        c7.metric("Logged Audits", f"{db_sum['total']:,}")
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("Database Table Records Overview")
        if current_snap.get("table_counts"):
            tbl_df = pd.DataFrame([{"Table Name": k, "Row Count": v} for k, v in current_snap["table_counts"].items()])
            st.dataframe(tbl_df, use_container_width=True, hide_index=True)
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("Database Diagnostics History")
        db_logs = get_database_monitor_logs(limit=100)
        if db_logs:
            st.dataframe(pd.DataFrame(db_logs), use_container_width=True, hide_index=True)
        else:
            st.info("No database diagnostics history found.")
        st.stop()
    # -----------------------------------------------------
    # 10. BACKUP STATUS (BACKUP CREATION & DOWNLOADS)
    # -----------------------------------------------------
    elif admin_menu == "💾 Backup Status":
        st.markdown("""
        <div class="fintech-hero-card">
            <h1 style="margin: 0; font-size: 1.85rem;">💾 SQLite Backup & Disaster Recovery</h1>
            <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">Atomic online backups, disaster snapshots, and point-in-time restore downloads.</p>
        </div>
        """, unsafe_allow_html=True)
        overview = get_database_backup_overview()
        c1, c2, c3 = st.columns(3)
        c1.metric("Database Size", f"{overview['database_size_mb']:.3f} MB")
        c2.metric("Available Backups", f"{overview['total_backups']:,}")
        c3.metric("Database Integrity", "HEALTHY" if overview["database_exists"] else "MISSING")
        st.caption(f"Storage Directory: `{overview['backup_directory']}`")
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("Generate On-Demand Backup")
        if st.button("📦 Create Instant Database Backup", type="primary"):
            with st.spinner("Generating atomic SQLite backup..."):
                res = create_database_backup()
            st.success(f"Backup created: {res['file_name']} ({res['size_mb']:.3f} MB)")
            st.rerun()
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("Backup History & File Retrieval")
        history = get_database_backup_history()
        if history:
            b_df = pd.DataFrame(history)
            st.dataframe(b_df, use_container_width=True, hide_index=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.subheader("📥 Download Backup File (.db)")
            backup_names = [h["File Name"] for h in history]
            selected_b = st.selectbox("Select Backup to Download", backup_names)
            if selected_b:
                try:
                    b_path = get_database_backup_file(selected_b)
                    with open(b_path, "rb") as f:
                        f_bytes = f.read()
                    st.download_button(
                        f"⬇️ Download {selected_b}",
                        data=f_bytes,
                        file_name=selected_b,
                        mime="application/octet-stream",
                        use_container_width=True
                    )
                except Exception as err:
                    st.error(f"Unable to read backup file: {err}")
        else:
            st.info("No backups exist yet. Click the button above to create your first backup.")
        st.stop()
# =========================================================
# SUBSCRIPTION ACCESS GATE & RAZORPAY CHECKOUT
# =========================================================
_rzp_order_id = get_query_param("razorpay_order_id")
_rzp_payment_id = get_query_param("razorpay_payment_id")
_rzp_signature = get_query_param("razorpay_signature")
if _rzp_order_id and _rzp_payment_id and _rzp_signature:
    _verify_res = verify_checkout_payment(
        current_user,
        _rzp_order_id,
        _rzp_payment_id,
        _rzp_signature,
    )
    clear_query_params()
    if _verify_res.get("success"):
        st.success(_verify_res.get("message", "VIP Pass activated!"))
        time.sleep(1)
        st.rerun()
    else:
        st.error(_verify_res.get("message", "Payment verification failed."))

if current_role != "admin" and not is_sub_active:
    from payment_gateway import create_checkout_order, get_single_plan, get_gateway_config
    plan = get_single_plan()
    gw_cfg = get_gateway_config()
    mode_label = "LIVE PAYMENT" if gw_cfg.get("mode") == "live" else "TEST MODE"

    st.markdown(f"""
    <div class="kukutv-vip-card">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
            <span class="kukutv-badge">🔥 {plan.get('name', 'VIP 5-Day Pass')}</span>
            <span class="badge-bullish">{mode_label}</span>
        </div>
        <div style="margin: 1rem 0;">
            <span class="kukutv-strike">₹{plan.get('original_price', 49)}</span>
            <span class="kukutv-price">₹{plan.get('amount', 2)}</span>
            <span style="color: #94A3B8; font-size: 1rem;">/ {plan.get('duration_days', 5)} Days</span>
        </div>
        <p style="color: #CBD5E1; font-size: 0.95rem; margin-bottom: 1.25rem;">
            Unlock real-time Binance Spot signals, RSI/MACD/EMA indicators, TradingView-style charts, and full market screener.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        if st.button(f"💳 Pay ₹{plan['amount']} & Unlock VIP Pass", type="primary", use_container_width=True):
            order_res = create_checkout_order(current_user)
            if order_res.get("success"):
                st.session_state["pending_rzp_order"] = order_res
            else:
                st.error(order_res.get("message", "Unable to start Razorpay checkout."))

    pending_order = st.session_state.get("pending_rzp_order")
    if pending_order and pending_order.get("order_id"):
        components.html(f"""
        <div style="display:flex;justify-content:center;padding:8px 0;">
          <button id="rzp-pay-btn" style="background:linear-gradient(135deg,#10B981,#059669);color:#fff;border:none;border-radius:10px;padding:14px 28px;font-size:16px;font-weight:700;cursor:pointer;font-family:sans-serif;box-shadow:0 4px 15px rgba(16,185,129,0.4);">
            🔒 Click Here to Open Razorpay (₹{pending_order['amount_rupees']})
          </button>
        </div>
        <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
        <script>
        var options = {{
            "key": "{pending_order['key_id']}",
            "amount": "{pending_order['amount']}",
            "currency": "{pending_order['currency']}",
            "name": "Market Analysis App",
            "description": "{pending_order['plan_name']}",
            "order_id": "{pending_order['order_id']}",
            "handler": function (response) {{
                var url = new URL(window.parent.location.href);
                url.searchParams.set("razorpay_payment_id", response.razorpay_payment_id);
                url.searchParams.set("razorpay_order_id", response.razorpay_order_id);
                url.searchParams.set("razorpay_signature", response.razorpay_signature);
                window.parent.location.href = url.toString();
            }},
            "theme": {{ "color": "#F59E0B" }}
        }};
        var rzp1 = new Razorpay(options);
        document.getElementById("rzp-pay-btn").onclick = function(e) {{
            rzp1.open();
            e.preventDefault();
        }};
        </script>
        """, height=700, scrolling=True)
        _, ver_col, _ = st.columns([1, 2, 1])
        with ver_col:
            if st.button("✅ I Completed Payment — Verify & Unlock", use_container_width=True):
                try:
                    from payment_gateway import get_razorpay_client
                    client = get_razorpay_client()
                    order_payments = client.order.payments(pending_order["order_id"]).get("items", [])
                    captured = [p for p in order_payments if str(p.get("status", "")).lower() == "captured"]
                    if captured:
                        pay_id = captured[0]["id"]
                        import hmac, hashlib
                        from payment_gateway import get_gateway_config
                        sec = get_gateway_config()["key_secret"]
                        sig = hmac.new(sec.encode("utf-8"), f"{pending_order['order_id']}|{pay_id}".encode("utf-8"), hashlib.sha256).hexdigest()
                        res = verify_checkout_payment(current_user, pending_order["order_id"], pay_id, sig)
                        if res.get("success"):
                            st.session_state.pop("pending_rzp_order", None)
                            st.success(res.get("message", "VIP Pass activated!"))
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(res.get("message", "Verification failed."))
                    else:
                        st.warning("Payment not captured yet on Razorpay. Please complete the payment first.")
                except Exception as ver_err:
                    st.error(f"Verification error: {ver_err}")
    st.stop()

# =========================================================
# USER WORKSPACE NAVIGATION (TERMINAL vs ACCOUNT & SETTINGS)
# =========================================================
st.sidebar.markdown("<hr>", unsafe_allow_html=True)
user_nav = st.sidebar.radio(
    "Workspace",
    ["📈 Live Market Terminal", "⭐ Watchlist", "📅 Economic Calendar", "⚙️ Account & Settings"],
    key="user_workspace_nav",
    label_visibility="collapsed",
)

if user_nav == "⭐ Watchlist":
    st.markdown("""
    <div class="fintech-hero-card">
        <h1 style="margin: 0; font-size: 1.85rem;">⭐ My Crypto Watchlist</h1>
        <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">
            Track your favorite Binance Spot pairs with live 24h prices and quick chart switching.
        </p>
    </div>
    """, unsafe_allow_html=True)
    if "user_watchlist" not in st.session_state:
        st.session_state.user_watchlist = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
    all_syms = get_all_usdt_symbols() or ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
    w_c1, w_c2 = st.columns([3, 1])
    with w_c1:
        add_sym = st.selectbox("Add Pair to Watchlist", [s for s in all_syms if s not in st.session_state.user_watchlist] or all_syms)
    with w_c2:
        st.write("")
        st.write("")
        if st.button("➕ Add Pair", use_container_width=True, type="primary"):
            if add_sym not in st.session_state.user_watchlist:
                st.session_state.user_watchlist.append(add_sym)
                st.rerun()

    tickers = {t["Symbol"]: t for t in get_all_usdt_market_tickers()}
    for w_sym in list(st.session_state.user_watchlist):
        t = tickers.get(w_sym, {})
        price = t.get("Price", 0.0)
        chg = t.get("24h Change", 0.0)
        c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 1])
        c1.markdown(f"**{w_sym}**")
        c2.markdown(f"`${price:,.4f}`" if price else "Syncing...")
        chg_color = "#10B981" if chg >= 0 else "#EF4444"
        c3.markdown(f"<span style='color:{chg_color};font-weight:700;'>{chg:+.2f}%</span>", unsafe_allow_html=True)
        if c4.button("🗑️", key=f"rm_{w_sym}"):
            st.session_state.user_watchlist.remove(w_sym)
            st.rerun()
    st.stop()

if user_nav == "📅 Economic Calendar":
    st.markdown("""
    <div class="fintech-hero-card">
        <h1 style="margin: 0; font-size: 1.85rem;">📅 Economic Calendar</h1>
        <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">
            Live global macroeconomic events that impact crypto and financial markets.
        </p>
    </div>
    """, unsafe_allow_html=True)
    components.html("""
    <div class="tradingview-widget-container">
      <div class="tradingview-widget-container__widget"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-events.js" async>
      {
        "colorTheme": "dark",
        "isTransparent": true,
        "width": "100%",
        "height": "650",
        "locale": "en",
        "importanceFilter": "-1,0,1"
      }
      </script>
    </div>
    """, height=670)
    st.stop()

if user_nav == "⚙️ Account & Settings":
    st.markdown("""
    <div class="fintech-hero-card">
        <h1 style="margin: 0; font-size: 1.85rem;">⚙️ Account & Settings</h1>
        <p style="color: #94A3B8; margin: 4px 0 0 0; font-size: 0.9rem;">
            Manage your profile, VIP subscription status, and security preferences.
        </p>
    </div>
    """, unsafe_allow_html=True)

    user_rec = auth.USER_DATA.get(current_user, {})
    p_col1, p_col2 = st.columns(2)
    with p_col1:
        st.markdown(f"""
        <div class="fintech-card">
            <h3 style="margin-top:0;">👤 Profile Details</h3>
            <p><b>Username:</b> {current_user}</p>
            <p><b>Email:</b> {user_rec.get('email', 'N/A')}</p>
            <p><b>Mobile:</b> {user_rec.get('mobile', 'N/A')}</p>
            <p><b>Role:</b> {current_role.upper()}</p>
        </div>
        """, unsafe_allow_html=True)
    with p_col2:
        st.markdown(f"""
        <div class="fintech-card">
            <h3 style="margin-top:0;">💎 VIP Subscription</h3>
            <p><b>Plan:</b> {sub_info.get('plan', 'Free')}</p>
            <p><b>Status:</b> {sub_info.get('status', 'Inactive')}</p>
            <p><b>Days Remaining:</b> {sub_info.get('days_remaining', 0)} days</p>
            <p><b>Expires At (UTC):</b> {sub_info.get('expires_at', 'N/A')}</p>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<hr>", unsafe_allow_html=True)
    st.subheader("🔐 Change Password")
    with st.form("change_password_form"):
        cur_pw = st.text_input("Current Password", type="password")
        new_pw = st.text_input("New Password (min 8 chars)", type="password")
        cnf_pw = st.text_input("Confirm New Password", type="password")
        if st.form_submit_button("Update Password", type="primary"):
            stored_pw = user_rec.get("password", "")
            if not auth.verify_password(cur_pw, stored_pw):
                st.error("Current password is incorrect.")
            elif len(new_pw) < 8:
                st.error("New password must be at least 8 characters.")
            elif new_pw != cnf_pw:
                st.error("New passwords do not match.")
            else:
                new_hash = auth.hash_password(new_pw)
                with database.get_connection() as conn:
                    conn.execute("UPDATE users SET password = ? WHERE username = ?", (new_hash, current_user))
                    conn.commit()
                if current_user in auth.USER_DATA:
                    auth.USER_DATA[current_user]["password"] = new_hash
                log_activity(current_user, "PASSWORD_CHANGED", "User updated password from Account & Settings.")
                st.success("Password updated successfully!")
    st.stop()

# =========================================================
# MARKET SETTINGS & TARGET CONTROLS (SIDEBAR)
# =========================================================
st.sidebar.markdown("<hr>", unsafe_allow_html=True)
st.sidebar.markdown("<div style='font-size: 0.8rem; font-weight: 700; color: #94A3B8; margin-bottom: 0.5rem; text-transform: uppercase;'>Market Controls</div>", unsafe_allow_html=True)
market_symbols = get_all_usdt_symbols()
if not market_symbols:
    market_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
if st.session_state.selected_market not in market_symbols:
    st.session_state.selected_market = market_symbols[0]
symbol = st.sidebar.selectbox(
    "Active Asset Pair",
    market_symbols,
    index=market_symbols.index(st.session_state.selected_market),
    key="active_symbol_selector",
)
st.session_state.selected_market = symbol
live_store.set_symbol(symbol)
coin_name = symbol.replace("USDT", "")
# Initial seed check
initial_market_data = get_live_market_data(symbol)
initial_stats = get_live_24h_stats(symbol)
if not initial_market_data or not initial_stats:
    try:
        if not initial_market_data:
            initial_market_data = get_market_data(symbol)
        if not initial_stats:
            initial_stats = get_24h_stats(symbol)
        live_store.seed_symbol(symbol, initial_market_data, initial_stats)
    except Exception:
        pass
initial_price = float(initial_market_data[-1]["price"]) if initial_market_data else 0.0
if st.session_state.get("target_market") != symbol or "target_price" not in st.session_state:
    st.session_state.target_market = symbol
    st.session_state.target_price = initial_price
st.sidebar.number_input(
    "Target Alert Level ($)",
    min_value=0.0,
    format="%.2f",
    key="target_price",
)
if st.sidebar.button("🔄 Instant Manual Refresh", use_container_width=True):
    st.rerun()
# =========================================================
# SIDEBAR LIVE MINI TICKER (STREAMLIT FRAGMENT)
# =========================================================
@fragment_decorator
def render_sidebar_metrics():
    market_data = get_live_market_data(symbol)
    stats = get_live_24h_stats(symbol)
    if not market_data or not stats:
        st.sidebar.caption("Connecting to Binance live feed...")
        return
    cur_p = float(market_data[-1]["price"])
    avg_p = float(calculate_average(market_data))
    sig = get_signal(cur_p, avg_p)
    chg = float(stats["price_change_percent"])
    tgt = float(st.session_state.get("target_price", cur_p))
    st.sidebar.markdown(f"""
    <div style="background: #0F172A; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 12px; margin-top: 10px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-weight: 700; color: #F8FAFC; font-size: 1.1rem;">{coin_name}</span>
            <span style="font-family: 'JetBrains Mono', monospace; font-size: 1.1rem; font-weight: 700; color: #F8FAFC;">${cur_p:,.2f}</span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.8rem; margin-top: 6px;">
            <span style="color: #94A3B8;">24h Delta:</span>
            <span style="color: {'#10B981' if chg >= 0 else '#EF4444'}; font-weight: 600;">{chg:+.2f}%</span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.8rem; margin-top: 4px;">
            <span style="color: #94A3B8;">Signal:</span>
            <span style="font-weight: 700; color: {'#10B981' if sig == 'BULLISH' else '#EF4444' if sig == 'BEARISH' else '#F59E0B'};">{sig}</span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.8rem; margin-top: 4px;">
            <span style="color: #94A3B8;">Target Status:</span>
            <span style="color: {'#10B981' if tgt > 0 and cur_p >= tgt else '#94A3B8'}; font-weight: 600;">
                {'Target Met' if tgt > 0 and cur_p >= tgt else 'Pending'}
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)
render_sidebar_metrics()
# =========================================================
# MAIN LIVE MARKET DASHBOARD (STREAMLIT FRAGMENT)
# =========================================================
@fragment_decorator
def render_main_market_dashboard():
    market_data = get_live_market_data(symbol)
    stats = get_live_24h_stats(symbol)
    if not market_data or not stats:
        st.info("⚡ Synchronizing real-time market data from Binance API. The dashboard will populate momentarily.")
        return
    current_price = float(market_data[-1]["price"])
    average = float(calculate_average(market_data))
    signal = get_signal(current_price, average)
    indicator_values = calculate_indicators(market_data)
    demo_prediction = get_demo_prediction(current_price, indicator_values)
    price_change_24h = float(stats["price_change_percent"])
    high_price = float(stats["high_price"])
    low_price = float(stats["low_price"])
    volume_24h = float(stats["volume"])
    target_price = float(st.session_state.get("target_price", current_price))
    price_difference = ((current_price - average) / average * 100) if average else 0.0
    # -----------------------------------------------------
    # TOP HEADER & HERO SUMMARY BAR
    # -----------------------------------------------------
    top_col1, top_col2 = st.columns([2.5, 1.2])
    with top_col1:
        st.markdown(f"""
        <div style="display: flex; align-items: baseline; gap: 14px;">
            <h1 style="margin: 0; font-size: 2.2rem;">{coin_name} <span style="font-size: 1.2rem; color: #64748B;">/ USDT</span></h1>
            <span class="badge-bullish" style="vertical-align: middle;">VIP 5-DAY ACTIVE</span>
            <span style="color: #64748B; font-size: 0.85rem;"><span class="live-dot"></span> LIVE AUTO-REFRESH (5s)</span>
        </div>
        """, unsafe_allow_html=True)
    with top_col2:
        st.markdown(f"""
        <div style="text-align: right; padding-top: 4px;">
            <div style="color: #94A3B8; font-size: 0.8rem;">Last Synchronized</div>
            <div style="font-family: 'JetBrains Mono', monospace; font-size: 1rem; color: #F8FAFC; font-weight: 600;">
                {datetime.now().strftime('%H:%M:%S')} UTC
            </div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    # -----------------------------------------------------
    # 4 CORE METRIC CARDS
    # -----------------------------------------------------
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric(
            label="Current Spot Price",
            value=f"${current_price:,.2f}",
            delta=f"{price_change_24h:+.2f}% (24h)",
            delta_color="normal",
        )
    with m_col2:
        st.metric(
            label="Recent Mean Average",
            value=f"${average:,.2f}",
            delta=f"{price_difference:+.2f}% vs Mean",
            delta_color="off",
        )
    with m_col3:
        st.metric(
            label="24h High Range",
            value=f"${high_price:,.2f}",
            delta=f"{(current_price - high_price)/high_price*100:+.2f}% from High",
            delta_color="inverse",
        )
    with m_col4:
        st.metric(
            label="24h Base Volume",
            value=f"{volume_24h:,.2f} {coin_name}",
            delta=f"Low: ${low_price:,.2f}",
            delta_color="off",
        )
    st.markdown("<br>", unsafe_allow_html=True)
    # -----------------------------------------------------
    # ALGORITHMIC MOMENTUM & TECHNICAL INDICATORS SECTION
    # -----------------------------------------------------
    sig_col, ind_col = st.columns([1.2, 2.2])
    with sig_col:
        if signal == "BULLISH":
            badge_html = "<span class='badge-bullish' style='font-size: 1rem;'>▲ BULLISH MOMENTUM</span>"
            border_glow = "border-color: rgba(16, 185, 129, 0.4);"
        elif signal == "BEARISH":
            badge_html = "<span class='badge-bearish' style='font-size: 1rem;'>▼ BEARISH PRESSURE</span>"
            border_glow = "border-color: rgba(239, 68, 68, 0.4);"
        else:
            badge_html = "<span class='badge-neutral' style='font-size: 1rem;'>◆ NEUTRAL CONSOLIDATION</span>"
            border_glow = "border-color: rgba(245, 158, 11, 0.4);"
        st.markdown(f"""
        <div class="fintech-card" style="height: 100%; {border_glow}">
            <div style="font-size: 0.8rem; color: #94A3B8; text-transform: uppercase; font-weight: 700; margin-bottom: 8px;">
                Quantitative Momentum Engine
            </div>
            <div style="margin-bottom: 12px;">{badge_html}</div>
            <div style="font-size: 0.95rem; color: #E2E8F0; margin-bottom: 10px;">
                <b>Algorithmic Model:</b> {demo_prediction['signal']}
            </div>
            <div style="font-size: 0.85rem; color: #94A3B8; line-height: 1.5;">
                {demo_prediction['reason']}
            </div>
            <div style="margin-top: 14px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.06); font-size: 0.78rem; color: #64748B;">
                *Institutional quantitative scoring based on mathematical kline momentum.
            </div>
        </div>
        """, unsafe_allow_html=True)
    with ind_col:
        sma_val = indicator_values.get("sma_14")
        ema_val = indicator_values.get("ema_14")
        rsi_val = indicator_values.get("rsi_14")
        macd_val = indicator_values.get("macd")
        st.markdown("""
        <div class="fintech-card">
            <div style="font-size: 0.8rem; color: #94A3B8; text-transform: uppercase; font-weight: 700; margin-bottom: 12px;">
                Technical Indicator Suite (14-Period & Momentum)
            </div>
        """, unsafe_allow_html=True)
        i_c1, i_c2, i_c3 = st.columns(3)
        with i_c1:
            st.metric("SMA (14)", f"${sma_val:,.2f}" if sma_val else "N/A")
            st.metric("EMA (14)", f"${ema_val:,.2f}" if ema_val else "N/A")
        with i_c2:
            rsi_text = f"{rsi_val:.2f}" if rsi_val else "N/A"
            rsi_badge = "Neutral"
            if rsi_val and rsi_val > 70:
                rsi_badge = "Overbought (>70)"
            elif rsi_val and rsi_val < 30:
                rsi_badge = "Oversold (<30)"
            st.metric("RSI (14)", rsi_text, delta=rsi_badge, delta_color="off")
        with i_c3:
            if macd_val and isinstance(macd_val, dict):
                st.metric("MACD Line", f"{macd_val['macd']:.4f}")
                st.metric("Histogram", f"{macd_val['histogram']:+.4f}", delta=f"Signal: {macd_val['signal']:.4f}")
            else:
                st.metric("MACD", "Insufficient Data")
        st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("<hr>", unsafe_allow_html=True)
    # -----------------------------------------------------
    # BINANCE OHLCV + TRADINGVIEW-STYLE LIGHTWEIGHT CHART
    # -----------------------------------------------------
    st.subheader("📈 Advanced Candlestick Chart")
    tv_symbol = f"BINANCE:{symbol}"
    components.html(f"""
    <div id="tv-wrap" class="tradingview-widget-container" style="position:relative;height:100vh;min-height:620px;width:100%;background:#0F172A;">
      <button onclick="var el=document.getElementById('tv-wrap');if(!document.fullscreenElement){{el.requestFullscreen();}}else{{document.exitFullscreen();}}" style="position:absolute;top:8px;right:48px;z-index:99;background:#1E293B;color:#E2E8F0;border:1px solid rgba(255,255,255,0.2);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px;">⛶ Fullscreen</button>
      <div id="tradingview_pro_chart" style="height:100%;width:100%;"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({{
        "autosize": true,
        "symbol": "{tv_symbol}",
        "interval": "60",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "toolbar_bg": "#0F172A",
        "enable_publishing": false,
        "hide_side_toolbar": false,
        "allow_symbol_change": true,
        "withdateranges": true,
        "container_id": "tradingview_pro_chart"
      }});
      </script>
    </div>
    """, height=635)
    st.markdown("<hr>", unsafe_allow_html=True)
    st.subheader("🌐 Global Crypto Benchmark Overview")
    overview_coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    overview_cols = st.columns(3)
    for i, o_coin in enumerate(overview_coins):
        c_data = get_live_market_data(o_coin)
        c_stats = get_live_24h_stats(o_coin)
        c_display = o_coin.replace("USDT", "")
        with overview_cols[i]:
            if c_data and c_stats:
                c_price = float(c_data[-1]["price"])
                c_avg = float(calculate_average(c_data))
                c_chg = float(c_stats["price_change_percent"])
                c_sig = get_signal(c_price, c_avg)
                st.markdown(f"""
                <div class="fintech-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-weight: 700; color: #F8FAFC; font-size: 1.15rem;">{c_display} / USDT</span>
                        <span style="font-weight: 700; color: {'#10B981' if c_sig == 'BULLISH' else '#EF4444' if c_sig == 'BEARISH' else '#F59E0B'}; font-size: 0.8rem;">
                            {c_sig}
                        </span>
                    </div>
                    <div style="font-size: 1.5rem; font-weight: 700; color: #F8FAFC; font-family: 'JetBrains Mono', monospace;">
                        ${c_price:,.2f}
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.82rem; margin-top: 6px; color: #94A3B8;">
                        <span>24h Change: <b style="color: {'#10B981' if c_chg >= 0 else '#EF4444'};">{c_chg:+.2f}%</b></span>
                        <span>High: ${float(c_stats['high_price']):,.2f}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.caption(f"{c_display} loading...")
    st.markdown("<hr>", unsafe_allow_html=True)
    # -----------------------------------------------------
    # ALL USDT SPOT MARKETS WATCHLIST TABLE
    # -----------------------------------------------------
    st.subheader("📑 Binance Spot Market Screener")
    st.caption("Live streaming quote book for all USDT trading pairs.")
    all_market_rows = get_all_usdt_market_tickers()
    if all_market_rows:
        all_market_table = pd.DataFrame(all_market_rows)
        search_market = st.text_input("🔍 Filter Market Screener", placeholder="Search symbol (e.g. SOL, ADA, DOGE, XRP)...")
        if search_market:
            s_val = search_market.strip().upper()
            all_market_table = all_market_table[all_market_table["Symbol"].str.contains(s_val, na=False)]
        all_market_table = all_market_table.sort_values("Quote Volume", ascending=False)
        st.dataframe(
            all_market_table,
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={
                "Symbol": st.column_config.TextColumn("Pair"),
                "Price": st.column_config.NumberColumn("Last Price", format="$%.4f"),
                "24h Change": st.column_config.NumberColumn("24h Change %", format="%.2f%%"),
                "24h High": st.column_config.NumberColumn("24h High", format="$%.4f"),
                "24h Low": st.column_config.NumberColumn("24h Low", format="$%.4f"),
                "Volume": st.column_config.NumberColumn("Base Volume", format="%.2f"),
                "Quote Volume": st.column_config.NumberColumn("USDT Turnover", format="$%.2f"),
            },
        )
    else:
        st.info("Market screener is synchronizing quotes from Binance...")
    st.markdown("<hr>", unsafe_allow_html=True)
    # -----------------------------------------------------
    # RECENT TICK EXECUTION & SYSTEM DISCLOSURES
    # -----------------------------------------------------
    history_col, status_col = st.columns([1.5, 1])
    with history_col:
        st.subheader("⏱️ Recent Candle Records (Latest 10)")
        history_table = pd.DataFrame(market_data).iloc[::-1].head(10)
        history_table = history_table.rename(columns={"time": "Timestamp", "price": "Close Price", "volume": "Candle Volume"})
        st.dataframe(
            history_table,
            use_container_width=True,
            hide_index=True,
            height=280,
            column_config={
                "Close Price": st.column_config.NumberColumn("Price ($)", format="$%.2f"),
                "Candle Volume": st.column_config.NumberColumn("Volume", format="%.4f"),
                "Timestamp": st.column_config.TextColumn("UTC Time"),
            }
        )
    with status_col:
        st.subheader("🛡️ Terminal Integrity & Guard")
        st.markdown(f"""
        <div class="fintech-card" style="font-size: 0.82rem; color: #94A3B8; line-height: 1.6;">
            <div><b>Data Feed:</b> Binance Public Market API</div>
            <div><b>Selected Instrument:</b> {symbol} (Spot)</div>
            <div><b>Payment Gate:</b> Disabled (educational build)</div>
            <div><b>Account Access:</b> Full VIP Terminal Access</div>
            <div><b>Auto-Refresh:</b> Streamlit 5-Second Fragment</div>
            <div style="margin-top: 8px; color: #64748B;">
                Signals are mathematical calculations based on historical klines and do not constitute professional financial advice.
            </div>
        </div>
        """, unsafe_allow_html=True)
render_main_market_dashboard()
