import os
import time

import stripe
from flask import Blueprint, render_template, redirect, url_for, request, jsonify, flash
from flask_login import login_required, current_user

from models import db, User, Donation
from mailer import send_new_subscriber_notification, send_donation_notification
import jobs

billing_bp = Blueprint("billing", __name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")

PRICE_CACHE_TTL = 3600
_price_cache = {"value": None, "fetched_at": 0}


def get_price_display():
    now = time.time()
    if _price_cache["value"] and (now - _price_cache["fetched_at"]) < PRICE_CACHE_TTL:
        return _price_cache["value"]

    try:
        price = stripe.Price.retrieve(STRIPE_PRICE_ID)
        amount = price["unit_amount"] / 100
        currency = price["currency"].upper()
        recurring = price["recurring"]
        interval = recurring["interval"] if recurring else "week"
        interval_count = recurring["interval_count"] if recurring else 1

        amount_str = f"${amount:.2f}" + ("" if currency == "USD" else f" {currency}")
        period_str = f"/ {interval}" if interval_count == 1 else f"/ {interval_count} {interval}s"

        result = {"amount": amount_str, "period": period_str}
        _price_cache["value"] = result
        _price_cache["fetched_at"] = now
        return result
    except Exception:
        return _price_cache["value"] or {"amount": "", "period": ""}


@billing_bp.route("/account")
@login_required
def account():
    return render_template(
        "account.html",
        user=current_user,
        limit=jobs.FREE_DOWNLOADS_PER_DAY,
        max_minutes=jobs.FREE_MAX_DURATION_SECONDS // 60,
        price=get_price_display(),
    )


@billing_bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    if not current_user.email_verified:
        flash("Please verify your email before subscribing.")
        return redirect(url_for("billing.account"))

    session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        customer_email=current_user.email,
        client_reference_id=str(current_user.id),
        success_url=f"{BASE_URL}/account?success=1",
        cancel_url=f"{BASE_URL}/account?canceled=1",
    )
    return redirect(session.url, code=303)


@billing_bp.route("/billing/portal", methods=["POST"])
@login_required
def billing_portal():
    if not current_user.stripe_customer_id:
        return redirect(url_for("billing.account"))

    session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=f"{BASE_URL}/account",
    )
    return redirect(session.url, code=303)


@billing_bp.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed" and getattr(obj, "mode", None) == "payment":
        session_id = getattr(obj, "id", None)
        if session_id and not Donation.query.filter_by(stripe_checkout_session_id=session_id).first():
            user_id = getattr(obj, "client_reference_id", None)
            amount_total = getattr(obj, "amount_total", 0) or 0
            currency = (getattr(obj, "currency", None) or "cad").upper()
            customer_details = getattr(obj, "customer_details", None)
            donor_email = getattr(customer_details, "email", None) if customer_details else None

            donation = Donation(
                user_id=int(user_id) if user_id else None,
                amount_cents=amount_total,
                currency=currency,
                donor_email=donor_email,
                stripe_checkout_session_id=session_id,
            )
            db.session.add(donation)
            db.session.commit()

            amount_display = f"${amount_total / 100:.2f} {currency}"
            send_donation_notification(amount_display, donor_email)

    elif event_type == "checkout.session.completed" and getattr(obj, "mode", None) == "subscription":
        user_id = getattr(obj, "client_reference_id", None)
        user = User.query.get(int(user_id)) if user_id else None
        if user:
            user.stripe_customer_id = getattr(obj, "customer", None)
            user.stripe_subscription_id = getattr(obj, "subscription", None)
            user.subscription_status = "active"
            db.session.commit()
            send_new_subscriber_notification(user)

    elif event_type == "customer.subscription.updated":
        user = User.query.filter_by(stripe_subscription_id=getattr(obj, "id", None)).first()
        if user:
            user.subscription_status = getattr(obj, "status", user.subscription_status)
            db.session.commit()

    elif event_type == "customer.subscription.deleted":
        user = User.query.filter_by(stripe_subscription_id=getattr(obj, "id", None)).first()
        if user:
            user.subscription_status = "canceled"
            db.session.commit()

    return jsonify({"received": True})
