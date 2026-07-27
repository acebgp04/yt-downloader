import os
from datetime import timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request, jsonify, send_file, render_template, redirect, url_for
from flask_login import LoginManager, current_user, login_required

from models import db, User, DownloadLog
import auth
from auth import auth_bp
from billing import billing_bp
from reports import reports_bp
from admin import admin_bp
from donations import donations_bp
from ads import ads_bp, get_active_ad
import jobs

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "app.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


app.register_blueprint(auth_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(donations_bp)
app.register_blueprint(ads_bp)
auth.init_app(app)
jobs.init_app(app)

with app.app_context():
    db.create_all()

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5001")

_style_css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "style.css")
try:
    ASSET_VERSION = str(int(os.path.getmtime(_style_css_path)))
except OSError:
    ASSET_VERSION = "1"


@app.context_processor
def inject_base_url():
    return {"base_url": BASE_URL, "asset_version": ASSET_VERSION}


EASTERN = ZoneInfo("America/New_York")


@app.template_filter("eastern")
def format_eastern(dt):
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN).strftime("%Y-%m-%d %I:%M %p ET")


@app.route("/google338e2ecb0ee9220b.html")
def google_site_verification():
    return "google-site-verification: google338e2ecb0ee9220b.html", 200, {"Content-Type": "text/html"}


@app.route("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.ico"))


@app.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /$",
        "Disallow: /account",
        "Disallow: /history",
        "Disallow: /report-issue",
        "Disallow: /login",
        "Disallow: /signup",
        "Disallow: /forgot-password",
        "Disallow: /reset-password",
        "Disallow: /api/",
        f"Sitemap: {BASE_URL}/sitemap.xml",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap_xml():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{BASE_URL}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return xml, 200, {"Content-Type": "application/xml"}


def user_is_unlimited():
    return current_user.is_authenticated and (current_user.is_subscribed or current_user.is_admin)


def get_client_ip():
    # The app is only reachable through the Cloudflare Tunnel (Gunicorn binds to
    # 127.0.0.1 only), so CF-Connecting-IP is always set by Cloudflare's edge and
    # can't be spoofed by a client bypassing the tunnel. Fall back to X-Forwarded-For
    # / remote_addr for local/dev use where that header isn't present.
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr


@app.route("/")
def index():
    unlimited_user = user_is_unlimited()
    daily_cap_enabled = jobs.FREE_DOWNLOADS_PER_DAY is not None
    remaining = None
    if not unlimited_user and daily_cap_enabled:
        used = jobs.downloads_used_today(get_client_ip())
        remaining = max(0, jobs.FREE_DOWNLOADS_PER_DAY - used)
    return render_template(
        "index.html",
        remaining=remaining,
        limit=jobs.FREE_DOWNLOADS_PER_DAY,
        max_minutes=jobs.FREE_MAX_DURATION_SECONDS // 60,
        unlimited_user=unlimited_user,
        daily_cap_enabled=daily_cap_enabled,
        homepage_ad=get_active_ad("homepage_banner"),
    )


@app.route("/history")
@login_required
def history():
    logs = (
        DownloadLog.query.filter_by(user_id=current_user.id)
        .order_by(DownloadLog.created_at.desc())
        .limit(200)
        .all()
    )
    return render_template("history.html", logs=logs)


@app.route("/api/info", methods=["POST"])
def info():
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        data = jobs.probe(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    is_playlist = jobs.is_playlist_info(data)
    unlimited = user_is_unlimited()
    duration = data.get("duration") or 0

    result = {
        "title": data.get("title"),
        "thumbnail": data.get("thumbnail"),
        "duration": duration,
        "uploader": data.get("uploader"),
        "is_playlist": is_playlist,
        "unlimited": unlimited,
        "over_duration_limit": (
            not unlimited and not is_playlist and duration > jobs.FREE_MAX_DURATION_SECONDS
        ),
    }

    if is_playlist:
        result["total_items"] = data.get("playlist_count") or len(data.get("entries") or [])
        result["qualities"] = [
            {"label": "Best", "size": None},
            {"label": "720p", "size": None},
            {"label": "480p", "size": None},
            {"label": "Audio only (mp3)", "size": None},
        ]
    else:
        result["qualities"] = jobs.quality_options_for(data)

    return jsonify(result)


@app.route("/api/jobs", methods=["POST"])
def api_create_job():
    url = request.json.get("url", "").strip()
    quality = request.json.get("quality", "Best")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    unlimited = user_is_unlimited()
    user_id = current_user.id if current_user.is_authenticated else None

    try:
        job_id = jobs.create_job(url, quality, user_id, get_client_ip(), unlimited)
    except jobs.JobError as e:
        return jsonify({"error": e.message, "limit": e.limit}), e.status

    return jsonify({"job_id": job_id}), 202


@app.route("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    return jsonify({
        "status": job["status"],
        "kind": job["kind"],
        "progress": job["progress"],
        "current_item": job["current_item"],
        "total_items": job["total_items"],
        "current_item_title": job["current_item_title"],
        "error": job["error"],
        "files": [{"index": i, "title": f["title"]} for i, f in enumerate(job["files"])],
    })


@app.route("/api/jobs/<job_id>/download/<int:file_index>")
def job_download(job_id, file_index):
    job = jobs.get_job(job_id)
    if not job or job["status"] != "finished":
        return jsonify({"error": "Job not ready"}), 404

    if file_index < 0 or file_index >= len(job["files"]):
        return jsonify({"error": "File not found"}), 404

    file_info = job["files"][file_index]
    if not os.path.exists(file_info["path"]):
        return jsonify({"error": "File no longer available"}), 404

    title = file_info["title"] or "download"
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "download"
    ext = os.path.splitext(file_info["path"])[1]

    return send_file(file_info["path"], as_attachment=True, download_name=f"{safe_title}{ext}")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
