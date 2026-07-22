import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

import yt_dlp

from models import db, DownloadLog

FREE_DOWNLOADS_PER_DAY = 5
FREE_MAX_DURATION_SECONDS = 30 * 60
JOB_TTL_SECONDS = 60 * 60

DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "yt-downloader")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PLAYER_CLIENT_ARGS = {"youtube": {"player_client": ["android", "web"]}}

QUALITY_HEIGHT_THRESHOLDS = [1080, 720, 480, 360]

QUALITY_SELECTORS = {
    "Best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]",
    "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
    "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]",
    "360p": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]",
    "Audio only (mp3)": "bestaudio/best",
}

# NOTE: jobs live in this process's memory only. This is fine for a single
# `flask run` / `python app.py` process, but will NOT work correctly behind a
# multi-worker WSGI server (e.g. gunicorn -w 4) since each worker would have
# its own separate _jobs dict. Would need Redis (or similar) to fix that.
_app = None
_jobs = {}
_jobs_lock = threading.Lock()


class JobError(Exception):
    def __init__(self, message, status=400, limit=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.limit = limit


def init_app(app):
    global _app
    _app = app


def today_midnight_utc():
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def downloads_used_today(ip_address):
    return DownloadLog.query.filter(
        DownloadLog.ip_address == ip_address,
        DownloadLog.created_at >= today_midnight_utc(),
    ).count()


def probe(url):
    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "extractor_args": PLAYER_CLIENT_ARGS,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def is_playlist_info(info):
    return info.get("_type") == "playlist"


def quality_options_for(info):
    formats = info.get("formats") or []
    heights = {f.get("height") for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")}
    labels = ["Best"]
    for threshold in QUALITY_HEIGHT_THRESHOLDS:
        if threshold in heights:
            labels.append(f"{threshold}p")
    labels.append("Audio only (mp3)")
    return labels


def get_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _update_job(job_id, **fields):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def _prune_old_jobs():
    cutoff = datetime.now(timezone.utc).timestamp() - JOB_TTL_SECONDS
    with _jobs_lock:
        expired = [jid for jid, j in _jobs.items() if j["created_at"].timestamp() < cutoff]
    for jid in expired:
        job = get_job(jid)
        if job:
            for f in job.get("files", []):
                try:
                    os.remove(f["path"])
                except OSError:
                    pass
            try:
                os.rmdir(os.path.join(DOWNLOAD_DIR, jid))
            except OSError:
                pass
        with _jobs_lock:
            _jobs.pop(jid, None)


def create_job(url, quality_label, user_id, ip_address, unlimited):
    _prune_old_jobs()

    try:
        info = probe(url)
    except Exception as e:
        raise JobError(str(e), 400)

    playlist = is_playlist_info(info)

    if playlist and not unlimited:
        raise JobError(
            "Playlists are available to subscribers only. Subscribe for unlimited downloads.",
            403, "playlist",
        )

    if not playlist and not unlimited:
        duration = info.get("duration") or 0
        if duration > FREE_MAX_DURATION_SECONDS:
            raise JobError(
                f"This video is longer than the {FREE_MAX_DURATION_SECONDS // 60}-minute "
                f"free tier limit. Subscribe for unlimited length.",
                403, "duration",
            )
        used = downloads_used_today(ip_address)
        if used >= FREE_DOWNLOADS_PER_DAY:
            raise JobError(
                f"You've used your {FREE_DOWNLOADS_PER_DAY} free downloads for today. "
                f"Subscribe for unlimited downloads.",
                403, "count",
            )

    selector = QUALITY_SELECTORS.get(quality_label, QUALITY_SELECTORS["Best"])
    is_audio = quality_label == "Audio only (mp3)"
    total_items = (info.get("playlist_count") or len(info.get("entries") or []) or 1) if playlist else 1
    kind = "playlist" if playlist else "single"

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "user_id": user_id,
            "status": "queued",
            "kind": kind,
            "progress": 0,
            "current_item": 1,
            "total_items": total_items,
            "current_item_title": None,
            "error": None,
            "files": [],
            "created_at": datetime.now(timezone.utc),
        }

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, url, selector, is_audio, quality_label, playlist, ip_address, user_id),
        daemon=True,
    )
    thread.start()
    return job_id


def _resolve_filename(ydl, entry, is_audio):
    filename = ydl.prepare_filename(entry)
    if is_audio:
        base, _ = os.path.splitext(filename)
        filename = base + ".mp3"
    return filename


def _run_job(job_id, url, selector, is_audio, quality_label, playlist, ip_address, user_id):
    kind = "playlist" if playlist else "single"
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    if playlist:
        outtmpl = os.path.join(job_dir, "%(playlist_index)s - %(title)s.%(ext)s")
    else:
        outtmpl = os.path.join(job_dir, "%(title)s.%(ext)s")

    def progress_hook(d):
        info_dict = d.get("info_dict") or {}
        idx = info_dict.get("playlist_index") or 1
        current_job = get_job(job_id) or {}
        n_entries = info_dict.get("n_entries") or current_job.get("total_items") or 1
        status = d.get("status")

        if status == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            item_pct = min((downloaded / total * 100) if total else 50, 99)
        elif status == "finished":
            item_pct = 100
        else:
            item_pct = 0

        overall = ((idx - 1) + item_pct / 100) / max(n_entries, 1) * 100
        _update_job(
            job_id,
            status="downloading",
            progress=int(min(overall, 99)),
            current_item=idx,
            total_items=n_entries,
            current_item_title=info_dict.get("title"),
        )

    def pp_hook(d):
        if d.get("status") == "started":
            _update_job(job_id, status="postprocessing")

    ydl_opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "noplaylist": not playlist,
        "extractor_args": PLAYER_CLIENT_ARGS,
        "format": selector,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
    }

    if is_audio:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        ydl_opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(url, download=True)
    except Exception as e:
        _update_job(job_id, status="error", error=str(e))
        return

    entries = result.get("entries") if playlist else [result]
    files = []

    with _app.app_context():
        for entry in entries:
            if not entry:
                continue
            filename = _resolve_filename(ydl, entry, is_audio)
            if not filename or not os.path.exists(filename):
                continue

            title = entry.get("title", "download")
            files.append({"path": filename, "filename": os.path.basename(filename), "title": title})

            db.session.add(DownloadLog(
                ip_address=ip_address,
                user_id=user_id,
                title=title,
                source_url=entry.get("webpage_url") or url,
                format="audio" if is_audio else "video",
                quality=quality_label,
                job_kind=kind,
            ))
        db.session.commit()

    if not files:
        _update_job(job_id, status="error", error="Download finished but no files were produced.")
        return

    _update_job(job_id, status="finished", progress=100, files=files)
