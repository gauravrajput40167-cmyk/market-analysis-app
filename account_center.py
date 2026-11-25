"""Account, checkout, and admin finance screens for the Streamlit app."""

import json
import os

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import auth
import finance
import payouts
from payment_gateway import (
    create_checkout_order,
    get_gateway_config,
    get_single_plan,
)


def _notice(result):
    if not isinstance(result, dict):
        st.error("The request could not be completed.")
        return
    message = result.get("message") or ("Saved." if result.get("ok") or result.get("success") else "Request failed.")
    if result.get("ok") or result.get("success"):
        st.success(message)
    else:
        st.error(message)


def render_checkout(username, plan_id="vip_pass"):
    """Show the fixed plan and open Razorpay Checkout in the configured mode."""
    config = get_gateway_config()
    try:
        plan = get_single_plan(plan_id)
    except Exception as error:
        st.error(str(error) if isinstance(error, ValueError) else "The VIP plan is unavailable.")
        return

    mode = config["mode"]
    mode_label = "LIVE MODE" if mode == "live" else "TEST MODE"
    mode_color = "#F59E0B" if mode == "live" else "#38BDF8"
    st.markdown(
        f"""
        <div class="kukutv-vip-card">
            <div class="kukutv-badge">{mode_label}</div>
            <h2 style="margin: 0.8rem 0 0.2rem 0;">{plan['name']}</h2>
            <div class="kukutv-price">₹{plan['amount']}</div>
            <p style="color:#94A3B8;margin-top:0.6rem;">
                {int(plan['duration_days'])} days of VIP market terminal access.
                The price is set on the server. VIP starts only after Razorpay signature verification.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if config.get("live_blocked"):
        st.warning(
            "Live mode was requested but is not armed. Checkout is staying in test mode. "
            "Set RAZORPAY_MODE=live, RAZORPAY_LIVE_ENABLED=true, and rzp_live keys only when you are ready to accept real payments."
        )
    if mode == "test":
        st.caption("Test mode uses Razorpay test keys and test cards. No live charge is created.")
    else:
        st.warning("Live mode is on. A captured payment will charge the customer's real payment method.")

    if not config["configured"]:
        st.info(
            "Add RAZORPAY_TEST_KEY_ID and RAZORPAY_TEST_KEY_SECRET to the environment or Streamlit secrets "
            "before opening checkout."
        )
        return

    if st.button("Continue to secure checkout", type="primary", use_container_width=True):
        with st.spinner("Creating a signed Razorpay order..."):
            order = create_checkout_order(username, plan["id"])
        if not order.get("success"):
            st.error(order.get("message") or "Checkout could not be started.")
            return
        st.session_state["razorpay_checkout"] = order

    order = st.session_state.get("razorpay_checkout")
    if not order or not order.get("success"):
        return
    options = {
        "key": order["key_id"],
        "amount": int(order["amount"]),
        "currency": order["currency"],
        "name": "Market Analysis App",
        "description": order["plan_name"],
        "order_id": order["order_id"],
        "theme": {"color": "#F59E0B"},
    }
    options_json = json.dumps(options)
    order_id_json = json.dumps(order["order_id"])
    components.html(
        f"""
        <html><body style="margin:0;background:#0F172A;color:#E2E8F0;font-family:Arial,sans-serif;">
        <button id="pay" style="width:100%;padding:12px 16px;border:0;border-radius:12px;
          background:#F59E0B;color:#111;font-weight:800;cursor:pointer;">
          Pay {order['currency']} {order['amount_rupees']} with Razorpay
        </button>
        <p style="font-size:12px;color:#94A3B8;">Mode: {mode}. Order {order['order_id']}</p>
        <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
        <script>
          const options = {options_json};
          options.handler = function (response) {{
            const url = new URL(window.top.location.href);
            url.search = "";
            url.searchParams.set("razorpay_payment_id", response.razorpay_payment_id);
            url.searchParams.set("razorpay_order_id", response.razorpay_order_id);
            url.searchParams.set("razorpay_signature", response.razorpay_signature);
            window.top.location.href = url.toString();
          }};
          options.modal = {{
            ondismiss: function () {{
              const url = new URL(window.top.location.href);
              url.search = "";
              url.searchParams.set("payment_result", "cancelled");
              url.searchParams.set("razorpay_order_id", {order_id_json});
              window.top.location.href = url.toString();
            }}
          }};
          function openCheckout() {{
            if (!window.Razorpay) {{
              document.body.insertAdjacentHTML("beforeend",
                "<p style='color:#FCA5A5'>Razorpay Checkout could not be loaded.</p>");
              return;
            }}
            const checkout = new Razorpay(options);
            checkout.on("payment.failed", function (resp) {{
              const url = new URL(window.top.location.href);
              url.search = "";
              url.searchParams.set("payment_result", "failed");
              url.searchParams.set("razorpay_order_id", {order_id_json});
              const paymentId = resp && resp.error && resp.error.metadata
                ? resp.error.metadata.payment_id || "" : "";
              if (paymentId) url.searchParams.set("razorpay_payment_id", paymentId);
              window.top.location.href = url.toString();
            }});
            checkout.open();
          }}
          document.getElementById("pay").onclick = openCheckout;
        </script>
        </body></html>
        """,
        height=120,
    )


def render_user_center(username):
    profile = auth.get_user_profile(username) or {}
    subscription = profile.get("subscription_info") or auth.get_user_subscription_info(username)
    balance = finance.get_balance(username)
    st.markdown(
        f"""
        <div class="fintech-hero-card">
            <h1 style="margin:0;font-size:1.8rem;">Account Center</h1>
            <p style="color:#94A3B8;margin:0.35rem 0 0 0;">
                {profile.get('display_name') or username} · {profile.get('email') or ''}
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("VIP status", subscription.get("status", "Inactive"))
    c2.metric("Days remaining", subscription.get("days_remaining", 0))
    c3.metric("Available balance", f"₹{balance['available']}")
    c4.metric("Payout mode", "DEMO" if balance["mode"] == "demo" else balance["mode"].upper())
    st.caption("Withdrawal balances are demo practice funds until a live payout provider is authorized. No real money is sent.")

    profile_tab, vip_tab, wallet_tab, notes_tab, settings_tab = st.tabs(
        ["Profile", "VIP & Payments", "Withdrawals", "Notifications", "Settings"]
    )
    with profile_tab:
        st.markdown('<div class="fintech-card">', unsafe_allow_html=True)
        st.write(f"**Username:** {profile.get('username', username)}")
        st.write(f"**Email:** {profile.get('email', '—')}")
        st.write(f"**Mobile:** {profile.get('mobile', '—')}")
        st.write(f"**Role:** {profile.get('role', 'user')}")
        st.write(f"**Account:** {'Active' if profile.get('active') else 'Disabled'}")
        st.write(f"**Plan:** {subscription.get('plan', 'Free')}")
        st.write(f"**Started:** {subscription.get('start_date', 'N/A')}")
        st.write(f"**Expires:** {subscription.get('expires_at', 'N/A')}")
        st.markdown("</div>", unsafe_allow_html=True)

    with vip_tab:
        if subscription.get("is_active"):
            st.success(f"{subscription.get('plan')} is active. {subscription.get('days_remaining')} day(s) remaining.")
        else:
            st.info("VIP access is inactive. It turns on only after a verified payment or an administrator update.")
        render_checkout(username)
        st.subheader("Payment history")
        payments = finance.list_payments(username)
        if payments:
            frame = pd.DataFrame(payments)
            st.dataframe(frame, use_container_width=True, hide_index=True)
        else:
            st.caption("No payments yet.")

    with wallet_tab:
        st.subheader("Demo withdrawal request")
        st.warning("DEMO WORKFLOW. Approving or completing a request does not transfer real money.")
        st.write(f"Available: **₹{balance['available']}** · Reserved: ₹{balance['reserved']}")
        if st.button("Add ₹100 demo practice balance"):
            _notice(finance.grant_demo_practice_balance(username))
            st.rerun()
        with st.form("withdrawal_form"):
            amount = st.text_input("Amount (INR)", placeholder="10.00")
            upi = st.text_input("UPI id", placeholder="name@okbank")
            submitted = st.form_submit_button("Submit demo withdrawal", type="primary")
        if submitted:
            key = st.session_state.get("withdrawal_idempotency")
            if not key:
                import uuid
                key = uuid.uuid4().hex
                st.session_state["withdrawal_idempotency"] = key
            result = finance.request_withdrawal(username, amount, upi, key)
            if result.get("ok"):
                st.session_state.pop("withdrawal_idempotency", None)
            _notice(result)
        st.subheader("Withdrawal history")
        rows = finance.list_withdrawals(username)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No withdrawal requests yet.")
        st.subheader("Balance ledger")
        ledger = finance.list_ledger(username)
        if ledger:
            st.dataframe(pd.DataFrame(ledger), use_container_width=True, hide_index=True)
        else:
            st.caption("No balance entries yet.")

    with notes_tab:
        notes = finance.list_notifications(username)
        if st.button("Mark all as read"):
            finance.mark_notifications_read(username)
            st.rerun()
        if notes:
            for note in notes:
                flag = "Unread" if not note["is_read"] else "Read"
                st.markdown(
                    f"**{note['title']}** · {flag}  \n{note['body']}  \n_{note['created_at']}_"
                )
        else:
            st.caption("No notifications yet.")

    with settings_tab:
        st.subheader("Change password")
        with st.form("password_form"):
            current = st.text_input("Current password", type="password")
            new_password = st.text_input("New password", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            save = st.form_submit_button("Update password", type="primary")
        if save:
            if new_password != confirm:
                st.error("New passwords do not match.")
            else:
                ok, message = auth.change_password(username, current, new_password)
                if ok:
                    st.success(message)
                else:
                    st.error(message)


def render_locked_market(username):
    st.markdown(
        """
        <div class="fintech-hero-card">
            <h1 style="margin:0;font-size:1.8rem;">VIP market terminal</h1>
            <p style="color:#94A3B8;margin:0.4rem 0 0 0;">
                Live charts unlock after a verified VIP payment. You can still manage your account from the sidebar.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_checkout(username)


def render_admin_user_controls(admin_username):
    st.subheader("Account and VIP actions")
    users = auth.get_all_users(admin_username)
    if not users:
        st.info("No users to manage.")
        return
    labels = [f"{row['username']} ({row['role']})" for row in users]
    choice = st.selectbox("Select user", labels)
    selected = users[labels.index(choice)]["username"]
    plans = finance.list_plans() or [{"plan_id": "vip_pass", "name": "VIP 5-Day Pass", "duration_days": 5}]
    plan_labels = [f"{plan['name']} ({plan['duration_days']} days)" for plan in plans]
    plan_choice = st.selectbox("VIP plan", plan_labels, key="admin_plan_choice")
    chosen_plan = plans[plan_labels.index(plan_choice)]
    days = st.number_input("Duration days", min_value=1, max_value=365, value=int(chosen_plan["duration_days"]))
    a1, a2, a3, a4 = st.columns(4)
    if a1.button("Activate account", use_container_width=True):
        if auth.activate_user(selected):
            st.success(f"{selected} activated.")
        else:
            st.error("Account could not be activated.")
    if a2.button("Deactivate account", use_container_width=True):
        if selected == admin_username:
            st.error("You cannot deactivate the account you are using.")
        elif auth.deactivate_user(selected):
            st.success(f"{selected} deactivated.")
        else:
            st.error("Account could not be deactivated.")
    if a3.button("Activate VIP", use_container_width=True):
        if auth.activate_subscription(selected, chosen_plan["name"], int(days)):
            st.success(f"VIP activated for {selected}.")
        else:
            st.error("VIP could not be activated.")
    if a4.button("Deactivate VIP", use_container_width=True):
        if auth.deactivate_subscription(selected):
            st.success(f"VIP removed for {selected}.")
        else:
            st.error("VIP could not be removed.")


def render_admin_payments(admin_username):
    st.markdown(
        """
        <div class="fintech-hero-card">
            <h1 style="margin:0;font-size:1.8rem;">Payment history</h1>
            <p style="color:#94A3B8;margin:0.35rem 0 0 0;">Successful, failed, pending, and cancelled checkouts.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    rows = auth.get_all_payments(admin_username)
    if not rows:
        st.info("No payments recorded.")
        return
    frame = pd.DataFrame(rows)
    status = st.selectbox("Status", ["All"] + sorted(frame["payment_status"].dropna().unique().tolist()))
    if status != "All":
        frame = frame[frame["payment_status"] == status]
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.download_button(
        "Download payment report",
        frame.to_csv(index=False).encode("utf-8-sig"),
        file_name="payment_history.csv",
        mime="text/csv",
    )


def render_admin_withdrawals(admin_username):
    st.markdown(
        """
        <div class="fintech-hero-card">
            <h1 style="margin:0;font-size:1.8rem;">Withdrawal requests</h1>
            <p style="color:#94A3B8;margin:0.35rem 0 0 0;">DEMO payouts only. Completing a request does not send real money.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.warning(payouts.live_payout_block_reason() if not payouts.live_payouts_enabled() else payouts.live_payout_block_reason())
    rows = auth.get_all_withdrawals_detailed(admin_username)
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No withdrawal requests.")
        return
    open_rows = [row for row in rows if row["status"] in {"pending", "approved"}]
    if not open_rows:
        st.caption("No requests are waiting for a decision.")
        return
    options = [f"#{row['id']} · {row['username']} · ₹{row['amount']} · {row['status']}" for row in open_rows]
    selected = st.selectbox("Open request", options)
    target = open_rows[options.index(selected)]
    note = st.text_input("Decision note", placeholder="Required context for a rejection")
    b1, b2, b3 = st.columns(3)
    if b1.button("Approve", type="primary", use_container_width=True):
        _notice(finance.review_withdrawal(admin_username, target["id"], "approved", note))
        st.rerun()
    if b2.button("Reject", use_container_width=True):
        _notice(finance.review_withdrawal(admin_username, target["id"], "rejected", note or "Rejected by administrator."))
        st.rerun()
    if b3.button("Complete demo payout", use_container_width=True):
        _notice(finance.complete_demo_payout(admin_username, target["id"]))
        st.rerun()


def render_admin_plans(admin_username):
    st.markdown(
        """
        <div class="fintech-hero-card">
            <h1 style="margin:0;font-size:1.8rem;">Subscription plans</h1>
            <p style="color:#94A3B8;margin:0.35rem 0 0 0;">Checkout always uses the price stored here, not a price sent by the browser.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    plans = finance.list_plans()
    if plans:
        st.dataframe(pd.DataFrame(plans), use_container_width=True, hide_index=True)
    with st.form("plan_form"):
        plan_id = st.text_input("Plan id", value="vip_pass")
        name = st.text_input("Name", value="VIP 5-Day Pass")
        amount = st.text_input("Price in INR", value="2")
        duration = st.number_input("Duration days", min_value=1, max_value=365, value=5)
        active = st.checkbox("Active", value=True)
        description = st.text_area("Description", value="VIP market terminal access")
        saved = st.form_submit_button("Save plan", type="primary")
    if saved:
        _notice(finance.save_plan(admin_username, plan_id, name, amount, duration, active, description))
        st.rerun()


def render_admin_reports(admin_username):
    st.markdown(
        """
        <div class="fintech-hero-card">
            <h1 style="margin:0;font-size:1.8rem;">Financial report</h1>
            <p style="color:#94A3B8;margin:0.35rem 0 0 0;">Payment totals and demo withdrawal totals from the local ledger.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    report = finance.financial_report(admin_username)
    if not report:
        st.error("Report is available to active administrators only.")
        return
    st.info(report["note"])
    left, right = st.columns(2)
    with left:
        st.subheader("Payments")
        pay_frame = pd.DataFrame(report["payments"])
        if pay_frame.empty:
            st.caption("No payments.")
        else:
            st.dataframe(pay_frame, use_container_width=True, hide_index=True)
    with right:
        st.subheader("Withdrawals")
        draw_frame = pd.DataFrame(report["withdrawals"])
        if draw_frame.empty:
            st.caption("No withdrawals.")
        else:
            st.dataframe(draw_frame, use_container_width=True, hide_index=True)
    st.metric("Demo credits recorded", f"₹{report['demo_credits']:.2f}")
    st.metric("Demo debits settled", f"₹{report['settled_debits']:.2f}")
    export = {
        "payments": report["payments"],
        "withdrawals": report["withdrawals"],
        "demo_credits": report["demo_credits"],
        "settled_debits": report["settled_debits"],
        "payout_mode": report["payout_mode"],
    }
    st.download_button(
        "Download financial report (JSON)",
        json.dumps(export, indent=2, default=str).encode("utf-8"),
        file_name="financial_report.json",
        mime="application/json",
    )


def logo_exists():
    return os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png"))
