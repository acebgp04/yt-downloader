import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

import yt_dlp

from models import db, DownloadLog

# Temporarily uncapped (None) while gauging real traffic -- set back to a
# number (e.g. 3) to re-enable the daily download limit for free/anonymous
# users. The duration cap and playlist gating are unaffected by this.
FREE_DOWNLOADS_PER_DAY = None
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


def chapters_for(info):
    chapters = info.get("chapters") or []
    return [
        {
            "index": i + 1,
            "title": ch.get("title") or f"Chapter {i + 1}",
            "start_time": ch.get("start_time") or 0,
            "end_time": ch.get("end_time"),
        }
        for i, ch in enumerate(chapters)
    ]


def _format_size(num_bytes):
    if not num_bytes:
        return None
    mb = num_bytes / (1024 * 1024)
    return f"~{mb:.0f} MB"


def _filesize(fmt):
    return fmt.get("filesize") or fmt.get("filesize_approx")


def quality_options_for(info):
    formats = info.get("formats") or []
    video_formats = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]
    audio_formats = [f for f in formats if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]

    heights = {f["height"] for f in video_formats}

    best_audio_size = None
    for f in audio_formats:
        sz = _filesize(f)
        if sz and (best_audio_size is None or sz > best_audio_size):
            best_audio_size = sz

    def video_plus_audio_size(vf):
        sz = _filesize(vf)
        if sz and best_audio_size:
            return sz + best_audio_size
        return None

    options = []

    best_video = max(video_formats, key=lambda f: f["height"], default=None)
    options.append({
        "label": "Best",
        "size": _format_size(video_plus_audio_size(best_video)) if best_video else None,
    })

    for threshold in QUALITY_HEIGHT_THRESHOLDS:
        if threshold in heights:
            match = next((f for f in video_formats if f["height"] == threshold and _filesize(f)), None)
            options.append({
                "label": f"{threshold}p",
                "size": _format_size(video_plus_audio_size(match)) if match else None,
            })

    options.append({"label": "Audio only (mp3)", "size": _format_size(best_audio_size)})
    return options


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


def create_job(url, quality_label, user_id, ip_address, unlimited, split_mode="whole", chapter_indices=None):
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

    selected_chapters = []
    if playlist:
        split_mode = "whole"
    elif split_mode == "chapters":
        wanted = set(chapter_indices or [])
        selected_chapters = [ch for ch in chapters_for(info) if ch["index"] in wanted]
        if not selected_chapters:
            raise JobError("No chapters selected.", 400)
    else:
        split_mode = "whole"

    if not playlist and not unlimited:
        if split_mode == "chapters":
            duration = sum((ch["end_time"] or 0) - (ch["start_time"] or 0) for ch in selected_chapters)
        else:
            duration = info.get("duration") or 0
        if duration > FREE_MAX_DURATION_SECONDS:
            raise JobError(
                f"This video is longer than the {FREE_MAX_DURATION_SECONDS // 60}-minute "
                f"free tier limit. Subscribe for unlimited length.",
                403, "duration",
            )
        if FREE_DOWNLOADS_PER_DAY is not None:
            used = downloads_used_today(ip_address)
            if used >= FREE_DOWNLOADS_PER_DAY:
                raise JobError(
                    f"You've used your {FREE_DOWNLOADS_PER_DAY} free downloads for today. "
                    f"Subscribe for unlimited downloads.",
                    403, "count",
                )

    selector = QUALITY_SELECTORS.get(quality_label, QUALITY_SELECTORS["Best"])
    is_audio = quality_label == "Audio only (mp3)"
    if split_mode == "chapters":
        total_items = len(selected_chapters)
        kind = "chapters"
    elif playlist:
        total_items = info.get("playlist_count") or len(info.get("entries") or []) or 1
        kind = "playlist"
    else:
        total_items = 1
        kind = "single"

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
        args=(job_id, url, selector, is_audio, quality_label, playlist, ip_address, user_id, selected_chapters),
        daemon=True,
    )
    thread.start()
    return job_id


def _resolve_filename(ydl, entry, is_audio):
    # Section/chapter downloads strip unchanged fields (e.g. "title") from the
    # entry dict, which breaks prepare_filename()'s recomputation -- but they
    # do set "filepath" to the final resolved (post-postprocessing) path, so
    # prefer that when present.
    filepath = entry.get("filepath")
    if filepath:
        return filepath

    filename = ydl.prepare_filename(entry)
    if is_audio:
        base, _ = os.path.splitext(filename)
        filename = base + ".mp3"
    return filename


def _run_job(job_id, url, selector, is_audio, quality_label, playlist, ip_address, user_id, selected_chapters=None):
    selected_chapters = selected_chapters or []
    split_chapters = bool(selected_chapters)
    kind = "chapters" if split_chapters else ("playlist" if playlist else "single")
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    if playlist:
        outtmpl = os.path.join(job_dir, "%(playlist_index)s - %(title)s.%(ext)s")
    elif split_chapters:
        outtmpl = os.path.join(job_dir, "%(title)s - %(section_number)03d - %(section_title)s.%(ext)s")
    else:
        outtmpl = os.path.join(job_dir, "%(title)s.%(ext)s")

    # download_ranges yields chapters with their original position in the full
    # video (needed for %(section_number)03d in filenames), but progress should
    # reflect position within the user's *selection* -- map one to the other.
    chapter_position = {ch["index"]: i + 1 for i, ch in enumerate(selected_chapters)}

    def progress_hook(d):
        info_dict = d.get("info_dict") or {}
        raw_idx = info_dict.get("playlist_index") or info_dict.get("section_number") or 1
        idx = chapter_position.get(raw_idx, raw_idx)
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
            current_item_title=info_dict.get("section_title") or info_dict.get("title"),
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

    if split_chapters:
        def ranges_callback(_info_dict, _ydl):
            for ch in selected_chapters:
                yield {
                    "start_time": ch["start_time"],
                    "end_time": ch["end_time"],
                    "title": ch["title"],
                    "index": ch["index"],
                }

        ydl_opts["download_ranges"] = ranges_callback
        ydl_opts["force_keyframes_at_cuts"] = True

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

    if playlist:
        entries = result.get("entries")
    elif split_chapters:
        entries = result.get("requested_downloads") or []
    else:
        entries = [result]
    files = []
    video_title = result.get("title") or "download"

    with _app.app_context():
        for entry in entries:
            if not entry:
                continue
            filename = _resolve_filename(ydl, entry, is_audio)
            if not filename or not os.path.exists(filename):
                continue

            # Section/chapter entries have unchanged fields like "title" stripped
            # (see _resolve_filename), so fall back to the parent video's title.
            base_title = entry.get("title") or video_title
            section_title = entry.get("section_title")
            title = f"{base_title} — {section_title}" if section_title else base_title
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
