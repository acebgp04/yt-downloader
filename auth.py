from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from models import db, User
from mailer import send_verification_email, send_password_reset_email

auth_bp = Blueprint("auth", __name__)

EMAIL_VERIFY_MAX_AGE = 24 * 60 * 60
PASSWORD_RESET_MAX_AGE = 60 * 60


def _serializer(salt):
    return URLSafeTimedSerializer(current_app.secret_key, salt=salt)


def generate_token(email, salt):
    return _serializer(salt).dumps(email)


def verify_token(token, salt, max_age):
    try:
        return _serializer(salt).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.")
            return redirect(url_for("auth.signup"))

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.")
            return redirect(url_for("auth.signup"))

        user = User(email=email, password_hash=generate_password_hash(password, method="pbkdf2:sha256"))
        db.session.add(user)
        db.session.commit()

        token = generate_token(user.email, "email-verify")
        send_verification_email(user, token)

        login_user(user)
        flash("We've sent a verification link to your email.")
        return redirect(url_for("billing.account"))

    return render_template("signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.")
            return redirect(url_for("auth.login"))

        login_user(user)
        return redirect(url_for("billing.account"))

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    email = verify_token(token, "email-verify", EMAIL_VERIFY_MAX_AGE)
    if not email:
        flash("That verification link is invalid or has expired.")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=email).first()
    if user:
        user.email_verified = True
        db.session.commit()
        flash("Your email is verified.")

    return redirect(url_for("billing.account"))


@auth_bp.route("/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    if not current_user.email_verified:
        token = generate_token(current_user.email, "email-verify")
        send_verification_email(current_user, token)
        flash("Verification email sent.")
    return redirect(url_for("billing.account"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_token(user.email, "password-reset")
            send_password_reset_email(user, token)
        flash("If that email exists, we've sent a password reset link.")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    email = verify_token(token, "password-reset", PASSWORD_RESET_MAX_AGE)
    if not email:
        flash("That password reset link is invalid or has expired.")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if not password or len(password) < 6:
            flash("Password must be at least 6 characters.")
            return render_template("reset_password.html", token=token)

        user = User.query.filter_by(email=email).first()
        if user:
            user.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
            db.session.commit()

        flash("Password updated. You can now log in.")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)
