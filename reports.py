from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from models import db, IssueReport
from mailer import send_issue_report_email

reports_bp = Blueprint("reports", __name__)

ISSUE_TYPES = [
    {"value": "download-fails", "label": "Download fails"},
    {"value": "wrong-quality", "label": "Wrong quality/format"},
    {"value": "slow", "label": "Too slow"},
    {"value": "other", "label": "Something else"},
]
ISSUE_TYPE_VALUES = {t["value"] for t in ISSUE_TYPES}


@reports_bp.route("/report-issue", methods=["GET", "POST"])
@login_required
def report_issue():
    if request.method == "POST":
        description = request.form.get("description", "").strip()
        issue_type = request.form.get("issue_type", "")
        if issue_type not in ISSUE_TYPE_VALUES:
            issue_type = "other"

        if not description:
            flash("Please describe the issue.")
            return redirect(url_for(
                "reports.report_issue",
                url=request.form.get("context_url", ""),
                quality=request.form.get("context_quality", ""),
                error=request.form.get("context_error", ""),
            ))

        report = IssueReport(
            user_id=current_user.id,
            issue_type=issue_type,
            description=description,
            context_url=request.form.get("context_url") or None,
            context_quality=request.form.get("context_quality") or None,
            context_error=request.form.get("context_error") or None,
        )
        db.session.add(report)
        db.session.commit()

        send_issue_report_email(report, current_user.email)

        flash("Thanks — we've received your report.")
        return redirect(url_for("index"))

    return render_template(
        "report_issue.html",
        context_url=request.args.get("url", ""),
        context_quality=request.args.get("quality", ""),
        context_error=request.args.get("error", ""),
        issue_types=ISSUE_TYPES,
    )
