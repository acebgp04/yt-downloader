import os
import smtplib
from email.mime.text import MIMEText

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")


def send_email(to, subject, body):
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print(f"[mailer] GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set, skipping email to {to}: {subject}")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [to], msg.as_string())


def send_verification_email(user, token):
    link = f"{BASE_URL}/verify-email/{token}"
    send_email(
        user.email,
        "Verify your email",
        f"Click to verify your email address:\n\n{link}\n\nThis link expires in 24 hours.",
    )


def send_password_reset_email(user, token):
    link = f"{BASE_URL}/reset-password/{token}"
    send_email(
        user.email,
        "Reset your password",
        f"Click to reset your password:\n\n{link}\n\nThis link expires in 1 hour. "
        f"If you didn't request this, you can ignore this email.",
    )


def send_issue_report_email(report, reporter_email):
    lines = [
        f"From: {reporter_email}",
        f"Type: {report.issue_type or 'other'}",
        "",
        report.description,
    ]
    if report.context_url or report.context_error or report.context_quality:
        lines += ["", "--- Context ---"]
        if report.context_url:
            lines.append(f"URL: {report.context_url}")
        if report.context_quality:
            lines.append(f"Quality: {report.context_quality}")
        if report.context_error:
            lines.append(f"Error: {report.context_error}")

    send_email(GMAIL_ADDRESS, "New issue report", "\n".join(lines))


def send_new_user_notification(user):
    method = "Google" if user.google_id else "email/password"
    send_email(
        GMAIL_ADDRESS,
        "New user signup",
        f"New account created: {user.email}\nMethod: {method}",
    )


def send_new_subscriber_notification(user):
    send_email(
        GMAIL_ADDRESS,
        "New subscriber",
        f"{user.email} just subscribed.\nStripe customer: {user.stripe_customer_id}",
    )
