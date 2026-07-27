from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    subscription_status = db.Column(db.String(50), default="none", nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def is_subscribed(self):
        return self.subscription_status == "active"


class DownloadLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    title = db.Column(db.String(500))
    source_url = db.Column(db.String(1000))
    format = db.Column(db.String(20))
    quality = db.Column(db.String(50))
    job_kind = db.Column(db.String(20))
    is_long_video = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Donation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    amount_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    donor_email = db.Column(db.String(255))
    stripe_checkout_session_id = db.Column(db.String(255), unique=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Ad(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    ad_type = db.Column(db.String(20), default="image", nullable=False)  # "image" | "script" | "smartlink"
    placement = db.Column(db.String(50), nullable=False)

    # Used when ad_type == "image"; link_url is also used for "smartlink"
    image_url = db.Column(db.String(1000), nullable=False, default="")
    link_url = db.Column(db.String(1000), nullable=False, default="")
    alt_text = db.Column(db.String(255))

    # Used when ad_type == "smartlink" -- the visible text of the link (no image)
    link_text = db.Column(db.String(255))

    # Used when ad_type == "script" -- a raw embed snippet from an ad network (e.g. Adsterra)
    script_content = db.Column(db.Text)

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    impression_count = db.Column(db.Integer, default=0, nullable=False)
    click_count = db.Column(db.Integer, default=0, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class IssueReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    issue_type = db.Column(db.String(50))
    description = db.Column(db.Text, nullable=False)
    context_url = db.Column(db.String(1000))
    context_error = db.Column(db.Text)
    context_quality = db.Column(db.String(50))

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
