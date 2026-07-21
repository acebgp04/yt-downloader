import os

import stripe
from flask import Blueprint, render_template, redirect, url_for, request, jsonify, flash
from flask_login import login_required, current_user

from models import db, User

billing_bp = Blueprint("billing", __name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")


@billing_bp.route("/account")
@login_required
def account():
    return render_template("account.html", user=current_user)


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

    if event_type == "checkout.session.completed":
        user_id = getattr(obj, "client_reference_id", None)
        user = User.query.get(int(user_id)) if user_id else None
        if user:
            user.stripe_customer_id = getattr(obj, "customer", None)
            user.stripe_subscription_id = getattr(obj, "subscription", None)
            user.subscription_status = "active"
            db.session.commit()

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
