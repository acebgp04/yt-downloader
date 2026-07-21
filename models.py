from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)

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

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
