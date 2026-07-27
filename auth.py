import os
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from authlib.integrations.flask_client import OAuth

from models import db, User
from mailer import send_verification_email, send_password_reset_email, send_new_user_notification

auth_bp = Blueprint("auth", __name__)

EMAIL_VERIFY_MAX_AGE = 24 * 60 * 60
PASSWORD_RESET_MAX_AGE = 60 * 60

oauth = OAuth()


def init_app(app):
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _is_safe_next_url(next_url):
    if not next_url:
        return False
    parsed = urlparse(next_url)
    if parsed.netloc and parsed.netloc != request.host:
        return False
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return False
    return True


def _log_in(user):
    login_user(user, remember=True)
    session.permanent = True


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
        send_new_user_notification(user)

        _log_in(user)
        flash("We've sent a verification link to your email.")
        return redirect(url_for("billing.account"))

    return render_template("signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.args.get("next") or request.form.get("next")
    if not _is_safe_next_url(next_url):
        next_url = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if not user or not user.password_hash:
            flash("Invalid email or password.")
            return redirect(url_for("auth.login", next=next_url))
        if not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.")
            return redirect(url_for("auth.login", next=next_url))

        _log_in(user)
        return redirect(next_url or url_for("billing.account"))

    return render_template("login.html", next=next_url)


@auth_bp.route("/auth/google/login")
def google_login():
    next_url = request.args.get("next")
    if _is_safe_next_url(next_url):
        session["post_login_next"] = next_url
    else:
        session.pop("post_login_next", None)
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
def google_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or {}
    google_id = userinfo.get("sub")
    email = (userinfo.get("email") or "").strip().lower()

    if not google_id or not email:
        flash("Google sign-in failed. Please try again.")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()

    if user:
        if not user.google_id:
            user.google_id = google_id
        if userinfo.get("email_verified"):
            user.email_verified = True
        db.session.commit()
    else:
        user = User(
            email=email,
            google_id=google_id,
            email_verified=bool(userinfo.get("email_verified")),
        )
        db.session.add(user)
        db.session.commit()
        send_new_user_notification(user)

    _log_in(user)
    next_url = session.pop("post_login_next", None)
    return redirect(next_url or url_for("billing.account"))


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
