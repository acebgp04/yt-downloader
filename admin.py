from functools import wraps

from flask import Blueprint, render_template, abort, request
from flask_login import login_required, current_user
from sqlalchemy import func

from models import db, User, DownloadLog, IssueReport, Donation
import jobs

admin_bp = Blueprint("admin", __name__)

DOWNLOADS_PER_PAGE = 25


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(404)
        return f(*args, **kwargs)
    return wrapper


@admin_bp.route("/admin")
@admin_required
def dashboard():
    total_users = User.query.count()
    verified_users = User.query.filter_by(email_verified=True).count()
    active_subscribers = User.query.filter_by(subscription_status="active").count()
    downloads_today = DownloadLog.query.filter(
        DownloadLog.created_at >= jobs.today_midnight_utc()
    ).count()
    downloads_total = DownloadLog.query.count()

    downloads_page = max(1, request.args.get("downloads_page", 1, type=int))
    downloads_total_pages = max(1, -(-downloads_total // DOWNLOADS_PER_PAGE))
    downloads_page = min(downloads_page, downloads_total_pages)
    recent_users = User.query.order_by(User.created_at.desc()).limit(20).all()
    recent_downloads = (
        db.session.query(DownloadLog, User.email)
        .outerjoin(User, DownloadLog.user_id == User.id)
        .order_by(DownloadLog.created_at.desc())
        .offset((downloads_page - 1) * DOWNLOADS_PER_PAGE)
        .limit(DOWNLOADS_PER_PAGE)
        .all()
    )
    recent_reports = (
        db.session.query(IssueReport, User.email)
        .join(User, IssueReport.user_id == User.id)
        .order_by(IssueReport.created_at.desc())
        .limit(20)
        .all()
    )

    donation_totals = (
        db.session.query(Donation.currency, func.sum(Donation.amount_cents))
        .group_by(Donation.currency)
        .all()
    )
    donation_totals_display = [f"${cents / 100:.2f} {currency}" for currency, cents in donation_totals]
    recent_donations = Donation.query.order_by(Donation.created_at.desc()).limit(20).all()

    return render_template(
        "admin.html",
        total_users=total_users,
        verified_users=verified_users,
        active_subscribers=active_subscribers,
        downloads_today=downloads_today,
        downloads_total=downloads_total,
        recent_users=recent_users,
        recent_downloads=recent_downloads,
        downloads_page=downloads_page,
        downloads_total_pages=downloads_total_pages,
        recent_reports=recent_reports,
        donation_totals_display=donation_totals_display,
        recent_donations=recent_donations,
    )
