#!/usr/bin/env python3
"""Standalone VM resource monitor — deliberately independent of the Flask
app (reads only .env for Gmail creds) so it keeps working even if the app
itself is down or crashing due to resource exhaustion."""

import os
import smtplib
import time
from email.mime.text import MIMEText

ENV_FILE = "/opt/yt-downloader/.env"
STATE_FILE = "/opt/vm-monitor/last_alert.txt"
COOLDOWN_SECONDS = 3600

CPU_LOAD_THRESHOLD = 3.0
MEM_AVAILABLE_MB_THRESHOLD = 500
DISK_PERCENT_THRESHOLD = 90


def load_env():
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def get_load_avg():
    return os.getloadavg()[0]


def get_mem_available_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    return None


def get_disk_percent(path="/"):
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return (1 - free / total) * 100


def send_alert(env, subject, body):
    gmail_address = env.get("GMAIL_ADDRESS")
    gmail_password = env.get("GMAIL_APP_PASSWORD")
    if not gmail_address or not gmail_password:
        print("Gmail credentials not configured, cannot send alert")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = gmail_address

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, [gmail_address], msg.as_string())


def already_alerted_recently():
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with open(STATE_FILE) as f:
            last = float(f.read().strip())
        return (time.time() - last) < COOLDOWN_SECONDS
    except (ValueError, OSError):
        return False


def mark_alerted():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(str(time.time()))


def main():
    load = get_load_avg()
    mem_available = get_mem_available_mb()
    disk_percent = get_disk_percent()

    problems = []
    if load > CPU_LOAD_THRESHOLD:
        problems.append(f"CPU load average is {load:.2f} (threshold {CPU_LOAD_THRESHOLD})")
    if mem_available is not None and mem_available < MEM_AVAILABLE_MB_THRESHOLD:
        problems.append(f"Available memory is {mem_available:.0f}MB (threshold {MEM_AVAILABLE_MB_THRESHOLD}MB)")
    if disk_percent > DISK_PERCENT_THRESHOLD:
        problems.append(f"Disk usage is {disk_percent:.1f}% (threshold {DISK_PERCENT_THRESHOLD}%)")

    if not problems:
        return

    if already_alerted_recently():
        print("Already alerted recently, skipping (cooldown active):", problems)
        return

    env = load_env()
    body = (
        "The YouTube Downloader VM is under strain:\n\n"
        + "\n".join(f"- {p}" for p in problems)
        + f"\n\nCurrent load avg: {load:.2f}\n"
        + f"Available memory: {mem_available:.0f}MB\n"
        + f"Disk usage: {disk_percent:.1f}%\n"
    )
    send_alert(env, "⚠ VM resource alert - youtube-downloader", body)
    mark_alerted()
    print("Alert sent:", problems)


if __name__ == "__main__":
    main()
