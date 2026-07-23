import os

import stripe
from flask import Blueprint, render_template, redirect, request, flash, url_for
from flask_login import current_user

donations_bp = Blueprint("donations", __name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")

MIN_DONATION_CENTS = 100
MAX_DONATION_CENTS = 50000


@donations_bp.route("/donate", methods=["GET"])
def donate():
    return render_template("donate.html")


@donations_bp.route("/donate", methods=["POST"])
def donate_checkout():
    amount_raw = request.form.get("amount", "").strip()
    try:
        amount_dollars = float(amount_raw)
    except ValueError:
        flash("Please enter a valid amount.")
        return redirect(url_for("donations.donate"))

    amount_cents = round(amount_dollars * 100)
    if amount_cents < MIN_DONATION_CENTS or amount_cents > MAX_DONATION_CENTS:
        flash(f"Please enter an amount between ${MIN_DONATION_CENTS / 100:.2f} and ${MAX_DONATION_CENTS / 100:.2f}.")
        return redirect(url_for("donations.donate"))

    session_kwargs = dict(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "cad",
                "product_data": {"name": "Donation to YouTube Downloader"},
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        success_url=f"{BASE_URL}/donate/thanks",
        cancel_url=f"{BASE_URL}/donate",
    )
    if current_user.is_authenticated:
        session_kwargs["client_reference_id"] = str(current_user.id)
        session_kwargs["customer_email"] = current_user.email

    session = stripe.checkout.Session.create(**session_kwargs)
    return redirect(session.url, code=303)


@donations_bp.route("/donate/thanks")
def donate_thanks():
    return render_template("donate_thanks.html")
