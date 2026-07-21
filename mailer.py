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
