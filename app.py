from __future__ import annotations

import argparse
import csv
import io
import json
import os
import queue
import re
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

try:
    import yt_dlp
except Exception as exc:  # pragma: no cover
    yt_dlp = None
    YTDLP_IMPORT_ERROR = exc
else:
    YTDLP_IMPORT_ERROR = None

try:
    from fpdf import FPDF
    from fpdf.enums import WrapMode
    from fpdf.fonts import FontFace
except Exception as exc:  # pragma: no cover
    FPDF = None
    WrapMode = None
    FontFace = None
    FPDF_IMPORT_ERROR = exc
else:
    FPDF_IMPORT_ERROR = None

APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR / "frontend"
ASSET_DIR = APP_DIR / "assets"
FONT_DIR = ASSET_DIR / "fonts"
DOWNLOAD_DIR = Path(os.environ.get("YOUTUBESCRAPER_EXPORT_DIR", str(APP_DIR / ".exports")))
DOWNLOAD_DIR.mkdir(exist_ok=True)
HOST = "127.0.0.1"
PORT = int(os.environ.get("YOUTUBESCRAPER_PORT", os.environ.get("YOUTUBE_ARCHIVE_PORT", "8765")))
MAX_WORKERS = max(1, min(4, int(os.environ.get("YOUTUBESCRAPER_WORKERS", "4"))))
SOCKET_TIMEOUT = 30
RECORD_BATCH_SIZE = 32

FIELDS = {
    "published": {"label": "Published date & time", "short": "Published"},
    "title": {"label": "Title", "short": "Title"},
    "description": {"label": "Description", "short": "Description"},
    "url": {"label": "Video URL", "short": "URL"},
    "id": {"label": "Video ID", "short": "Video ID"},
    "channel": {"label": "Channel name", "short": "Channel"},
    "channel_url": {"label": "Channel URL", "short": "Channel URL"},
    "thumbnail": {"label": "Thumbnail URL", "short": "Thumbnail"},
    "duration": {"label": "Duration", "short": "Duration"},
    "views": {"label": "Views", "short": "Views"},
    "likes": {"label": "Likes", "short": "Likes"},
}
DEFAULT_FIELDS = list(FIELDS.keys())
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}

app = Flask(__name__, static_folder=None)
state_lock = threading.Lock()
state: dict[str, Any] = {
    "status": "idle",
    "message": "Ready",
    "mode": "channel",
    "source_url": "",
    "channel_url": "",
    "channel_name": "",
    "selected_fields": [],
    "records": [],
    "playlists": [],
    "sources": [],
    "selected_playlist_ids": [],
    "total": None,
    "processed": 0,
    "succeeded": 0,
    "failed": 0,
    "started_at": None,
    "finished_at": None,
    "current_title": "",
    "current_url": "",
    "current_playlist": "",
    "errors": [],
    "is_cancelled": False,
    "extract_id": 0,
}
subscribers: set[queue.Queue] = set()
cancel_event = threading.Event()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return unicodedata.normalize("NFC", value)


def normalize_description(text: Any) -> str:
    value = clean_text(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if out and not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def human_duration(seconds: Any) -> str:
    if seconds is None:
        return ""
    try:
        total = int(round(float(seconds)))
    except Exception:
        return ""
    if total < 0:
        return ""
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_timestamp(ts: Any) -> str:
    if ts in (None, ""):
        return ""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone()
        if os.name == "nt":
            return dt.strftime("%d %B %Y, %#I:%M:%S %p")
        return dt.strftime("%d %B %Y, %-I:%M:%S %p")
    except Exception:
        return ""


def normalize_youtube_url(raw: str) -> urllib.parse.ParseResult:
    value = clean_text(raw).strip()
    if not value:
        raise ValueError("Please enter a YouTube URL.")
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in YOUTUBE_HOSTS:
        raise ValueError("Please enter a valid youtube.com URL.")
    return parsed


def validate_source_url(raw: str) -> tuple[str, str]:
    parsed = normalize_youtube_url(raw)
    path = parsed.path.rstrip("/")
    query = urllib.parse.parse_qs(parsed.query)
    if path.startswith("/@") or path.startswith("/channel/") or path.startswith("/c/") or path.startswith("/user/"):
        clean = urllib.parse.urlunparse(("https", "www.youtube.com", path, "", "", ""))
        return "channel", clean
    if path == "/playlist" and query.get("list"):
        pid = query["list"][0]
        clean = urllib.parse.urlunparse(("https", "www.youtube.com", "/playlist", "", urllib.parse.urlencode({"list": pid}), ""))
        return "playlist", clean
    raise ValueError("That URL does not look like a supported YouTube channel or playlist URL.")


def publish(event: dict[str, Any]) -> None:
    payload = f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    with state_lock:
        current = list(subscribers)
    for q in current:
        try:
            q.put_nowait(payload)
        except queue.Full:
            with state_lock:
                subscribers.discard(q)


def set_state(**changes: Any) -> None:
    with state_lock:
        state.update(changes)
        snapshot = {
            "type": "status",
            "status": state["status"],
            "message": state["message"],
            "mode": state["mode"],
            "total": state["total"],
            "processed": state["processed"],
            "succeeded": state["succeeded"],
            "failed": state["failed"],
            "current_title": state["current_title"],
            "current_url": state["current_url"],
            "current_playlist": state["current_playlist"],
            "channel_name": state["channel_name"],
            "is_cancelled": state["is_cancelled"],
            "error_count": len(state["errors"]),
        }
    publish(snapshot)


def make_ydl_opts(*, flat: bool = False) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "retries": 3,
        "extractor_retries": 3,
        "fragment_retries": 2,
        "socket_timeout": SOCKET_TIMEOUT,
        "skip_download": True,
        "noplaylist": False,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
        "prefer_insecure": False,
        "consoletitle": False,
        "extract_flat": "in_playlist" if flat else False,
        "lazy_playlist": True,
    }
    deno = os.environ.get("DENO_EXE")
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    return opts


def make_video_ydl_opts() -> dict[str, Any]:
    opts = make_ydl_opts(flat=False)
    opts["noplaylist"] = True
    return opts


def flat_entry_url(entry: dict[str, Any]) -> str:
    """Return a canonical single-video URL for video playlist/channel entries."""
    video_id = clean_text(entry.get("id") or "")
    raw = str(entry.get("webpage_url") or entry.get("url") or "")
    entry_type = entry.get("_type")
    looks_like_video = (
        entry_type in {"url", "url_transparent"}
        and video_id
        and ("/watch" in raw or str(entry.get("ie_key") or "").lower() in {"youtube", "youtubewebpage"})
    )
    if looks_like_video:
        return f"https://www.youtube.com/watch?v={urllib.parse.quote(video_id, safe='')}"
    if raw.startswith("http"):
        return raw
    if video_id and entry_type in {"url", "url_transparent"}:
        return f"https://www.youtube.com/watch?v={urllib.parse.quote(video_id, safe='')}"
    return ""


def flatten_video_entries(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, dict):
            entries = node.get("entries")
            if entries is not None:
                try:
                    for child in entries:
                        walk(child)
                except Exception:
                    pass
                return
            node_type = node.get("_type")
            if node_type in {"url", "url_transparent", None} and node.get("id") and (node.get("webpage_url") or node.get("url")):
                url = flat_entry_url(node)
                vid = str(node.get("id") or url)
                if url and vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    found.append(node)
                return
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(obj)
    return found


def flatten_playlist_entries(obj: Any) -> list[dict[str, Any]]:
    """Preserve playlist entry order and allow duplicate video IDs across occurrences."""
    found: list[dict[str, Any]] = []
    occurrence = 0

    def walk(node: Any) -> None:
        nonlocal occurrence
        if node is None:
            return
        if isinstance(node, dict):
            entries = node.get("entries")
            if entries is not None:
                try:
                    for child in entries:
                        walk(child)
                except Exception:
                    pass
                return
            if node.get("id") and (node.get("webpage_url") or node.get("url")):
                url = flat_entry_url(node)
                if url:
                    item = dict(node)
                    item["_playlist_occurrence"] = occurrence
                    occurrence += 1
                    found.append(item)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(obj)
    return found


def discover_channel(channel_url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if yt_dlp is None:
        raise RuntimeError(f"yt-dlp is not installed: {YTDLP_IMPORT_ERROR}")
    with yt_dlp.YoutubeDL(make_ydl_opts(flat=True)) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    entries = flatten_video_entries(info)
    metadata = {
        "channel": clean_text(info.get("channel") or info.get("uploader") or info.get("title") or ""),
        "channel_url": clean_text(info.get("channel_url") or channel_url),
        "title": clean_text(info.get("title") or ""),
    }
    if not entries:
        raise RuntimeError("The channel was reached, but no publicly listed videos could be extracted.")
    return entries, metadata


def playlist_url_from_entry(entry: dict[str, Any]) -> str:
    """Build a canonical YouTube playlist URL whenever a playlist ID is known."""
    pid = clean_text(entry.get("id") or "")
    if pid and entry.get("_type") in {"url", "url_transparent"}:
        return f"https://www.youtube.com/playlist?list={urllib.parse.quote(pid, safe='')}"
    url = entry.get("webpage_url") or entry.get("url")
    if isinstance(url, str) and url.startswith("http"):
        parsed = urllib.parse.urlparse(url)
        list_id = urllib.parse.parse_qs(parsed.query).get("list", [""])[0]
        if list_id:
            return f"https://www.youtube.com/playlist?list={urllib.parse.quote(list_id, safe='')}"
        return url
    return ""


def best_thumbnail(info: dict[str, Any]) -> str:
    direct = clean_text(info.get("thumbnail") or "")
    if direct:
        return direct
    thumbs = info.get("thumbnails")
    if isinstance(thumbs, list):
        for item in reversed(thumbs):
            if isinstance(item, dict):
                url = clean_text(item.get("url") or "")
                if url:
                    return url
    vid = clean_text(info.get("id") or "")
    if vid and info.get("_type") in {None, "url", "url_transparent", "video"}:
        return f"https://i.ytimg.com/vi/{urllib.parse.quote(vid, safe='')}/hqdefault.jpg"
    return ""


def discover_playlists(source_url: str, mode: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if yt_dlp is None:
        raise RuntimeError(f"yt-dlp is not installed: {YTDLP_IMPORT_ERROR}")
    if mode == "playlist":
        targets = [source_url]
    else:
        targets = [source_url.rstrip("/") + "/playlists"]
    playlists: list[dict[str, Any]] = []
    seen: set[str] = set()
    channel_meta: dict[str, Any] = {}
    for target in targets:
        with yt_dlp.YoutubeDL(make_ydl_opts(flat=True)) as ydl:
            info = ydl.extract_info(target, download=False)
        channel_meta = {
            "channel": clean_text(info.get("channel") or info.get("uploader") or ""),
            "channel_url": clean_text(info.get("channel_url") or source_url),
        }
        if mode == "playlist":
            pid = clean_text(info.get("id") or "")
            url = playlist_url_from_entry(info)
            if not pid:
                parsed = urllib.parse.urlparse(source_url)
                pid = urllib.parse.parse_qs(parsed.query).get("list", [""])[0]
            playlists.append({
                "id": pid,
                "title": clean_text(info.get("title") or "Playlist"),
                "url": url or source_url,
                "thumbnail": best_thumbnail(info),
                "count": info.get("playlist_count") or info.get("n_entries"),
            })
            break
        raw_entries = info.get("entries") or []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            pid = clean_text(entry.get("id") or "")
            url = playlist_url_from_entry(entry)
            if not pid and "list=" in url:
                pid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("list", [""])[0]
            if not pid or pid in seen:
                continue
            seen.add(pid)
            playlists.append({
                "id": pid,
                "title": clean_text(entry.get("title") or "Untitled playlist"),
                "url": url,
                "thumbnail": best_thumbnail(entry),
                "count": entry.get("playlist_count") or entry.get("n_entries"),
            })
    if not playlists:
        raise RuntimeError("No publicly listed playlists were found for this channel.")
    return playlists, channel_meta


def discover_multiple_sources(sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover public playlists for multiple channel/playlist sources.

    Each returned playlist carries source identity so mixed channel + direct playlist
    inputs remain independently selectable and traceable during extraction/export.
    """
    all_playlists: list[dict[str, Any]] = []
    source_results: list[dict[str, Any]] = []
    seen_playlist_ids: set[str] = set()
    source_errors: list[str] = []
    for idx, source in enumerate(sources):
        raw_url = clean_text(source.get("url") or source.get("source_url") or "").strip()
        source_id = clean_text(source.get("source_id") or f"source-{idx + 1}")
        if not raw_url:
            source_errors.append(f"Source {idx + 1}: Please enter a YouTube channel or playlist URL.")
            continue
        try:
            kind, url = validate_source_url(raw_url)
            playlists, meta = discover_playlists(url, kind)
            source_title = clean_text(source.get("title") or meta.get("channel") or (playlists[0].get("title") if kind == "playlist" and playlists else "Source"))
            source_result = {
                "source_id": source_id,
                "url": url,
                "kind": kind,
                "title": source_title,
                "channel_name": clean_text(meta.get("channel") or (playlists[0].get("channel_name") if playlists else "")),
                "playlists": [],
            }
            for pitem in playlists:
                item = dict(pitem)
                item["source_id"] = source_id
                item["source_url"] = url
                item["source_title"] = source_title
                pid = clean_text(item.get("id") or "")
                # A playlist ID is globally unique on YouTube; keep the first occurrence.
                if pid and pid in seen_playlist_ids:
                    continue
                if pid:
                    seen_playlist_ids.add(pid)
                source_result["playlists"].append(item)
                all_playlists.append(item)
            source_results.append(source_result)
        except Exception as exc:
            source_errors.append(f"Source {idx + 1}: {humanize_error(str(exc))}")
    if not all_playlists:
        if source_errors:
            raise RuntimeError("No sources could be loaded. " + " | ".join(source_errors))
        raise RuntimeError("No publicly listed playlists were found for the supplied sources.")
    return source_results, all_playlists, source_errors


def extract_video(url: str, entry: dict[str, Any], selected: list[str]) -> dict[str, Any]:
    base = {
        "published": format_timestamp(entry.get("timestamp")),
        "published_ts": entry.get("timestamp"),
        "title": clean_text(entry.get("title")),
        "description": normalize_description(entry.get("description")),
        "url": flat_entry_url(entry),
        "id": clean_text(entry.get("id")),
        "channel": clean_text(entry.get("channel") or entry.get("uploader")),
        "channel_url": clean_text(entry.get("channel_url") or ""),
        "thumbnail": best_thumbnail(entry),
        "duration": human_duration(entry.get("duration")),
        "duration_seconds": entry.get("duration"),
        "views": entry.get("view_count"),
        "likes": entry.get("like_count"),
    }
    full_needed = any(k in selected for k in ("published", "description", "duration", "views", "likes"))
    missing_thumbnail = "thumbnail" in selected and not base["thumbnail"]
    missing_channel = "channel" in selected and not base["channel"]
    missing_channel_url = "channel_url" in selected and not base["channel_url"]
    if full_needed or missing_thumbnail or missing_channel or missing_channel_url:
        if yt_dlp is None:
            raise RuntimeError("yt-dlp is not available")
        with yt_dlp.YoutubeDL(make_video_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
        base.update({
            "published": format_timestamp(info.get("timestamp")),
            "published_ts": info.get("timestamp"),
            "title": clean_text(info.get("title") or base["title"]),
            "description": normalize_description(info.get("description")),
            "url": (
                f"https://www.youtube.com/watch?v={urllib.parse.quote(clean_text(info.get('id') or base['id']), safe='')}"
                if clean_text(info.get("id") or base["id"])
                else base["url"]
            ),
            "id": clean_text(info.get("id") or base["id"]),
            "channel": clean_text(info.get("channel") or info.get("uploader") or base["channel"]),
            "channel_url": clean_text(info.get("channel_url") or base["channel_url"]),
            "thumbnail": clean_text(info.get("thumbnail") or base["thumbnail"]),
            "duration": human_duration(info.get("duration")),
            "duration_seconds": info.get("duration"),
            "views": info.get("view_count"),
            "likes": info.get("like_count"),
        })
    return {k: base.get(k, "") for k in selected} | {
        "published_ts": base.get("published_ts"),
        "duration_seconds": base.get("duration_seconds"),
    }


def _record_for_playlist(record: dict[str, Any], playlist: dict[str, Any], occurrence: int, index: int) -> dict[str, Any]:
    out = dict(record)
    out["playlist_id"] = playlist.get("id", "")
    out["playlist_name"] = clean_text(playlist.get("title", ""))
    out["playlist_url"] = clean_text(playlist.get("url", ""))
    out["playlist_thumbnail"] = clean_text(playlist.get("thumbnail", ""))
    out["playlist_index"] = occurrence + 1
    out["source_id"] = clean_text(playlist.get("source_id") or "")
    out["source_title"] = clean_text(playlist.get("source_title") or "")
    out["source_url"] = clean_text(playlist.get("source_url") or "")
    out["_index"] = index
    return out


def extraction_worker(source_url: str, selected: list[str], mode: str, selected_playlists: list[dict[str, Any]], extract_id: int) -> None:
    try:
        with state_lock:
            state["mode"] = mode
        set_state(status="discovering", message="Discovering the source…", total=None, processed=0, succeeded=0, failed=0, current_title="", current_url="", current_playlist="", errors=[], is_cancelled=False, started_at=time.time(), finished_at=None)

        if mode == "channel":
            entries, metadata = discover_channel(source_url)
            jobs = [{"entry": entry, "playlist": None, "occurrence": i} for i, entry in enumerate(entries)]
        else:
            playlist_jobs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
            for playlist in selected_playlists:
                if cancel_event.is_set():
                    break
                set_state(status="discovering", message=f"Reading playlist: {playlist.get('title', '')}", current_playlist=playlist.get("title", ""))
                playlist_opts = make_ydl_opts(flat=True)
                playlist_opts["noplaylist"] = False
                playlist_opts["lazy_playlist"] = False
                with yt_dlp.YoutubeDL(playlist_opts) as ydl:
                    info = ydl.extract_info(playlist["url"], download=False)
                entries = flatten_playlist_entries(info)
                if not clean_text(playlist.get("thumbnail")) and entries:
                    playlist["thumbnail"] = best_thumbnail(entries[0])
                playlist_jobs.append((playlist, entries))
            jobs = []
            for playlist, entries in playlist_jobs:
                for pos, entry in enumerate(entries):
                    jobs.append({"entry": entry, "playlist": playlist, "occurrence": pos})
            metadata = {"channel": "", "channel_url": source_url}
            if playlist_jobs and playlist_jobs[0][0].get("id"):
                metadata["channel"] = clean_text(jobs[0]["entry"].get("channel") or jobs[0]["entry"].get("uploader") or "")

        with state_lock:
            if state["extract_id"] != extract_id:
                return
            state["channel_name"] = metadata.get("channel") or ""
            state["channel_url"] = metadata.get("channel_url") or source_url
            state["total"] = len(jobs)
            state["records"] = []
        publish({"type": "archive_discovered", "total": len(jobs), "channel_name": metadata.get("channel") or "", "mode": mode})
        set_state(status="extracting", message=f"Extracting {len(jobs):,} video entries…", total=len(jobs))

        # Shared cache lets the same video appearing in multiple playlists avoid repeated detailed requests.
        metadata_cache: dict[str, dict[str, Any]] = {}
        cache_lock = threading.Lock()

        def one(index: int, job: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str | None, dict[str, Any]]:
            if cancel_event.is_set():
                return index, None, "cancelled", job
            entry = job["entry"]
            url = flat_entry_url(entry)
            cache_key = clean_text(entry.get("id") or url)
            try:
                if mode == "playlist" and cache_key:
                    with cache_lock:
                        cached = metadata_cache.get(cache_key)
                    if cached is not None:
                        record = dict(cached)
                    else:
                        record = extract_video(url, entry, selected)
                        with cache_lock:
                            metadata_cache[cache_key] = dict(record)
                else:
                    record = extract_video(url, entry, selected)
                if mode == "playlist":
                    record = _record_for_playlist(record, job["playlist"], int(job["occurrence"]), index)
                else:
                    record["_index"] = index
                return index, record, None, job
            except Exception as exc:
                return index, None, str(exc), job

        futures: dict[Any, int] = {}
        next_index = 0
        batch: list[dict[str, Any]] = []
        pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ytmeta")
        try:
            while next_index < len(jobs) and len(futures) < MAX_WORKERS * 3 and not cancel_event.is_set():
                futures[pool.submit(one, next_index, jobs[next_index])] = next_index
                next_index += 1
            while futures:
                done, _ = wait(tuple(futures.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    idx = futures.pop(future)
                    _, record, error, job = future.result()
                    with state_lock:
                        state["processed"] += 1
                        if record is not None:
                            state["succeeded"] += 1
                            state["records"].append(record)
                            batch.append(record)
                        elif error and error != "cancelled":
                            state["failed"] += 1
                            state["errors"].append({"index": idx, "error": error})
                        processed = state["processed"]
                        total_now = state["total"]
                        state["current_title"] = clean_text(job["entry"].get("title"))
                        state["current_url"] = flat_entry_url(job["entry"])
                        state["current_playlist"] = clean_text(job["playlist"].get("title", "")) if job["playlist"] else ""
                    if len(batch) >= RECORD_BATCH_SIZE:
                        publish({"type": "records", "records": batch})
                        batch = []
                    if total_now:
                        percent = processed / total_now * 100
                        set_state(message=f"Processed {processed:,} of {total_now:,} videos · {percent:.2f}%")
                    while next_index < len(jobs) and len(futures) < MAX_WORKERS * 3 and not cancel_event.is_set():
                        futures[pool.submit(one, next_index, jobs[next_index])] = next_index
                        next_index += 1
                if cancel_event.is_set():
                    for future in list(futures):
                        future.cancel()
                    break
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if batch:
            publish({"type": "records", "records": batch})

        cancelled = cancel_event.is_set()
        with state_lock:
            # Completion order is concurrent; restore source/playlist order for the
            # canonical in-memory dataset before exports and subsequent views.
            state["records"].sort(key=lambda r: int(r.get("_index", 0)))
            is_partial = cancelled or state["failed"] > 0 or state["processed"] < state["total"]
            state["finished_at"] = time.time()
            state["is_cancelled"] = cancelled
        if cancelled:
            set_state(status="cancelled", message="Extraction stopped. Successfully extracted records were kept.")
        elif is_partial:
            set_state(status="partial", message="Extraction finished with some unavailable or failed items. Successful records were kept.")
        else:
            set_state(status="complete", message="Extraction complete.")
        publish({"type": "done"})
    except Exception as exc:
        text = str(exc).strip() or exc.__class__.__name__
        with state_lock:
            state["errors"].append({"error": text})
            state["finished_at"] = time.time()
        set_state(status="error", message=humanize_error(text))
        publish({"type": "done"})


def humanize_error(text: str) -> str:
    lower = text.lower()
    if "sign in" in lower or "private" in lower or "members-only" in lower:
        return "YouTube requires access that this public-only tool cannot provide. Public metadata extraction was not possible."
    if "no publicly listed playlists" in lower:
        return text
    if "video unavailable" in lower or "not available" in lower:
        return "YouTube reported that the requested channel, playlist, or video is unavailable."
    if "timed out" in lower or "timeout" in lower:
        return "The connection to YouTube timed out. Check your internet connection and try again."
    if "http error 429" in lower or "too many requests" in lower:
        return "YouTube is rate-limiting requests. Wait a little and retry with a smaller field selection."
    if "http error 403" in lower or "forbidden" in lower:
        return "YouTube refused the request. Updating yt-dlp or retrying later may resolve temporary restrictions."
    return text


def _display_columns(columns: list[str], playlist_mode: bool) -> list[str]:
    if playlist_mode:
        return ["source_title", "playlist_name", *columns]
    return columns


def _value(record: dict[str, Any], col: str) -> Any:
    return record.get(col, "") if record.get(col, "") is not None else ""


def export_records(records: list[dict[str, Any]], columns: list[str], kind: str, output_path: Path, playlist_mode: bool = False, playlist_order: list[str] | None = None) -> None:
    columns = [c for c in columns if c in FIELDS]
    if not columns:
        raise ValueError("Select at least one column to export.")
    export_columns = _display_columns(columns, playlist_mode)
    if kind == "csv":
        with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Source" if c == "source_title" else "Playlist" if c == "playlist_name" else FIELDS[c]["label"] for c in export_columns])
            for record in records:
                writer.writerow([_value(record, c) for c in export_columns])
        return
    if kind == "txt":
        with output_path.open("w", encoding="utf-8", newline="") as fh:
            if playlist_mode:
                grouped: dict[str, list[dict[str, Any]]] = {}
                display: dict[str, tuple[str, str, str]] = {}
                encounter_order: list[str] = []
                for r in records:
                    pid = clean_text(r.get("playlist_id") or "")
                    key = pid or f"name:{clean_text(r.get('playlist_name') or 'Untitled playlist')}|url:{clean_text(r.get('playlist_url') or '')}"
                    if key not in grouped:
                        grouped[key] = []
                        encounter_order.append(key)
                    grouped[key].append(r)
                    display[key] = (clean_text(r.get("source_title") or "Source"), clean_text(r.get("playlist_name") or "Untitled playlist"), clean_text(r.get("playlist_url") or ""))
                ordered_keys: list[str] = []
                if playlist_order:
                    wanted = {str(x): i for i, x in enumerate(playlist_order)}
                    ordered_keys.extend(sorted(grouped, key=lambda key: (wanted.get(key, len(wanted) + encounter_order.index(key) if key in encounter_order else 10**9))))
                else:
                    ordered_keys = encounter_order
                for playlist_no, key in enumerate(ordered_keys, start=1):
                    group = grouped[key]
                    source_title, playlist_name, playlist_url = display[key]
                    playlist_id = clean_text(group[0].get("playlist_id") or "")
                    fh.write(f"{'#' * 76}\n")
                    fh.write(f"SOURCE: {source_title}\n")
                    fh.write(f"PLAYLIST {playlist_no}: {playlist_name}\n")
                    if playlist_id:
                        fh.write(f"PLAYLIST ID: {playlist_id}\n")
                    fh.write(f"VIDEOS IN PLAYLIST: {len(group):,}\n")
                    if playlist_url:
                        fh.write(f"PLAYLIST URL: {playlist_url}\n")
                    fh.write(f"{'#' * 76}\n\n")
                    for item_no, record in enumerate(group, start=1):
                        fh.write(f"{'=' * 76}\nVIDEO {item_no} OF {len(group)}\n")
                        for c in columns:
                            fh.write(f"{FIELDS[c]['label']}:\n{_value(record, c)}\n\n")
                        fh.write(f"{'=' * 76}\n\n")
            else:
                for i, record in enumerate(records, start=1):
                    fh.write(f"{'=' * 76}\n{i}.\n")
                    for c in columns:
                        fh.write(f"{FIELDS[c]['label']}:\n{_value(record, c)}\n\n")
                    fh.write(f"{'=' * 76}\n\n")
        return
    if kind == "pdf":
        build_pdf(records, columns, output_path, playlist_mode=playlist_mode, playlist_order=playlist_order)
        return
    raise ValueError("Unsupported export format.")


def _font_paths() -> list[Path]:
    candidates = [
        FONT_DIR / "NotoSans-Regular.ttf",
        FONT_DIR / "NotoSansBengali-Regular.ttf",
        FONT_DIR / "NotoSansArabic-Regular.ttf",
        FONT_DIR / "NotoSansDevanagari-Regular.ttf",
        FONT_DIR / "NotoSansCJK-JP.ttf",
        FONT_DIR / "NotoSansCJK-KR.ttf",
        FONT_DIR / "NotoSansCJK-SC.ttf",
        FONT_DIR / "NotoSansCJK-TC.ttf",
        FONT_DIR / "NotoSansCJK-HK.ttf",
        FONT_DIR / "NotoSansSymbols2-Regular.ttf",
        FONT_DIR / "NotoColorEmoji.ttf",
    ]
    return [p for p in candidates if p.exists()]


def build_pdf(records: list[dict[str, Any]], columns: list[str], output_path: Path, playlist_mode: bool = False, playlist_order: list[str] | None = None) -> None:
    if FPDF is None:
        raise RuntimeError(f"fpdf2 is not installed: {FPDF_IMPORT_ERROR}")
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.set_title("YouScraper")
    font_files = _font_paths()
    if not font_files:
        raise RuntimeError("The bundled PDF Unicode fonts are missing.")
    registered: list[str] = []
    for font in font_files:
        family = font.stem.replace("-Regular", "")
        try:
            pdf.add_font(family=family, fname=str(font))
            registered.append(family)
        except Exception:
            continue
    main_font = "NotoSans"
    fallback = [family for family in registered if family != main_font]
    if hasattr(pdf, "set_fallback_fonts") and fallback:
        pdf.set_fallback_fonts(fallback, exact_match=False)
    pdf.set_font(main_font, size=8)

    weights = {
        "published": 1.45, "title": 2.2, "description": 5.2, "url": 2.7, "id": 1.5,
        "channel": 1.8, "channel_url": 2.2, "thumbnail": 2.4, "duration": 1.2,
        "views": 1.2, "likes": 1.2,
    }

    def write_table(group_records: list[dict[str, Any]]) -> None:
        page_width = 297 - 20
        total_w = sum(weights.get(c, 1.5) for c in columns)
        col_widths = [page_width * weights.get(c, 1.5) / total_w for c in columns]
        data = [[FIELDS[c]["label"] for c in columns]]
        for record in group_records:
            data.append([str(_value(record, c)) for c in columns])
        headings_style = FontFace(size_pt=8, fill_color=(40, 40, 44), color=(255, 255, 255))
        with pdf.table(
            col_widths=col_widths,
            width=page_width,
            headings_style=headings_style,
            repeat_headings=1,
            first_row_as_headings=True,
            text_align="LEFT",
            v_align="TOP",
            padding=1.4,
            wrapmode=WrapMode.CHAR,
            borders_layout="INTERNAL",
            min_row_height=5,
        ) as table:
            for rowdata in data:
                row = table.row()
                for value in rowdata:
                    row.cell(value)

    if playlist_mode:
        grouped: dict[str, list[dict[str, Any]]] = {}
        display: dict[str, tuple[str, str, str]] = {}
        order: list[str] = []
        for r in records:
            pid = clean_text(r.get("playlist_id") or "")
            key = pid or f"name:{clean_text(r.get('playlist_name') or 'Untitled playlist')}|url:{clean_text(r.get('playlist_url') or '')}"
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(r)
            display[key] = (clean_text(r.get("source_title") or "Source"), clean_text(r.get("playlist_name") or "Untitled playlist"), clean_text(r.get("playlist_url") or ""))
        if playlist_order:
            wanted = {str(x): i for i, x in enumerate(playlist_order)}
            encounter_rank = {key: i for i, key in enumerate(order)}
            order.sort(key=lambda key: (wanted.get(key, len(wanted) + encounter_rank.get(key, 10**9))))
        for playlist_no, key in enumerate(order, start=1):
            source_title, name, playlist_url = display[key]
            pdf.add_page()
            pdf.set_font(main_font, size=12)
            pdf.set_text_color(210, 214, 220)
            pdf.cell(0, 8, text=f"SOURCE: {source_title}")
            pdf.ln(5)
            pdf.cell(0, 8, text=f"PLAYLIST {playlist_no}: {name}")
            pdf.ln(6)
            pdf.set_font(main_font, size=8)
            if playlist_url:
                pdf.set_text_color(155, 163, 173)
                pdf.multi_cell(0, 5, text=f"Playlist URL: {playlist_url} · {len(grouped[key]):,} videos")
                pdf.ln(2)
            write_table(grouped[key])
    else:
        pdf.add_page()
        write_table(records)
    pdf.output(str(output_path))


def get_records_by_ids(ids: list[str] | None) -> list[dict[str, Any]]:
    with state_lock:
        records = list(state["records"])
    if not ids:
        return records
    wanted = {str(x) for x in ids}
    return [r for r in records if str(r.get("_index")) in wanted]


@app.get("/")
def index() -> Any:
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/styles.css")
def styles() -> Any:
    return send_from_directory(FRONTEND_DIR, "styles.css")


@app.get("/app.js")
def app_js() -> Any:
    return send_from_directory(FRONTEND_DIR, "app.js")


@app.get("/assets/<path:filename>")
def assets(filename: str) -> Any:
    return send_from_directory(ASSET_DIR, filename)


@app.get("/api/health")
def api_health() -> Any:
    return jsonify({"ok": True, "name": "YouScraper"})


@app.get("/api/status")
def api_status() -> Any:
    with state_lock:
        safe = {k: v for k, v in state.items() if k not in {"records"}}
        safe["record_count"] = len(state["records"])
        safe["errors"] = state["errors"][-20:]
    return jsonify(safe)


@app.get("/api/results")
def api_results() -> Any:
    with state_lock:
        return jsonify({
            "records": state["records"],
            "selected_fields": state["selected_fields"],
            "status": state["status"],
            "mode": state["mode"],
            "playlists": state["playlists"],
            "sources": state.get("sources") or [],
        })


@app.get("/api/events")
def api_events() -> Response:
    q: queue.Queue = queue.Queue(maxsize=200)
    with state_lock:
        subscribers.add(q)
        current = {
            "type": "status", "status": state["status"], "message": state["message"],
            "mode": state["mode"], "total": state["total"], "processed": state["processed"],
            "succeeded": state["succeeded"], "failed": state["failed"], "current_title": state["current_title"],
            "current_url": state["current_url"], "current_playlist": state["current_playlist"],
            "channel_name": state["channel_name"], "is_cancelled": state["is_cancelled"],
            "error_count": len(state["errors"]),
        }
    q.put_nowait(f"data: {json.dumps(current, ensure_ascii=False)}\n\n")

    def stream():
        try:
            while True:
                try:
                    yield q.get(timeout=20)
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            with state_lock:
                subscribers.discard(q)

    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/discover-sources")
def api_discover_sources() -> Any:
    payload = request.get_json(silent=True) or {}
    raw_sources = payload.get("sources") or []
    if not isinstance(raw_sources, list) or not raw_sources:
        return jsonify({"ok": False, "error": "Add at least one YouTube channel or playlist source."}), 400
    try:
        sources, playlists, source_errors = discover_multiple_sources(raw_sources)
    except Exception as exc:
        return jsonify({"ok": False, "error": humanize_error(str(exc))}), 400
    with state_lock:
        state["mode"] = "playlist"
        state["sources"] = sources
        state["playlists"] = playlists
        state["selected_playlist_ids"] = []
        state["source_url"] = sources[0]["url"] if sources else ""
        state["channel_url"] = sources[0].get("url", "") if sources else ""
        state["channel_name"] = sources[0].get("channel_name", "") if sources else ""
    return jsonify({"ok": True, "mode": "playlist", "sources": sources, "playlists": playlists, "channel_name": state["channel_name"], "warnings": source_errors})


@app.post("/api/discover-playlists")
def api_discover_playlists() -> Any:
    # Backward-compatible single-source endpoint used by older frontend builds.
    payload = request.get_json(silent=True) or {}
    try:
        mode, url = validate_source_url(payload.get("source_url", ""))
        playlists, meta = discover_playlists(url, mode)
        source_id = "source-1"
        source_title = clean_text(meta.get("channel") or (playlists[0].get("title") if playlists else "Source"))
        playlists = [dict(p, source_id=source_id, source_url=url, source_title=source_title) for p in playlists]
        source = {"source_id": source_id, "url": url, "kind": mode, "title": source_title, "channel_name": clean_text(meta.get("channel") or ""), "playlists": playlists}
    except Exception as exc:
        return jsonify({"ok": False, "error": humanize_error(str(exc))}), 400
    with state_lock:
        state["mode"] = "playlist"
        state["source_url"] = url
        state["channel_url"] = meta.get("channel_url") or url
        state["channel_name"] = meta.get("channel") or ""
        state["sources"] = [source]
        state["playlists"] = playlists
        state["selected_playlist_ids"] = [p["id"] for p in playlists[:1]] if mode == "playlist" else []
    return jsonify({"ok": True, "mode": "playlist", "sources": [source], "playlists": playlists, "channel_name": meta.get("channel") or ""})


@app.post("/api/extract")
def api_extract() -> Any:
    payload = request.get_json(silent=True) or {}
    raw_sources = payload.get("sources") or []
    if raw_sources:
        try:
            normalized_sources = []
            for src in raw_sources:
                kind, normalized = validate_source_url(src.get("url") or src.get("source_url") or "")
                normalized_sources.append({"source_id": str(src.get("source_id") or f"source-{len(normalized_sources)+1}"), "url": normalized, "kind": kind, "title": clean_text(src.get("title") or "")})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        source_kind = normalized_sources[0]["kind"]
        source_url = normalized_sources[0]["url"]
    else:
        try:
            source_kind, source_url = validate_source_url(payload.get("source_url", payload.get("channel_url", "")))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        normalized_sources = [{"source_id":"source-1","url":source_url,"kind":source_kind,"title":""}]
    requested_mode = str(payload.get("mode") or source_kind).lower()
    if requested_mode not in {"channel", "playlist"}:
        return jsonify({"ok": False, "error": "Unsupported extraction mode."}), 400
    if requested_mode == "playlist":
        mode = "playlist"
    elif source_kind == "channel":
        mode = "channel"
    else:
        return jsonify({"ok": False, "error": "A direct playlist URL can only be used with Playlist Organizer."}), 400
    selected = [x for x in payload.get("fields", []) if x in FIELDS]
    if not selected:
        return jsonify({"ok": False, "error": "Select at least one metadata field before extraction."}), 400
    playlist_objs: list[dict[str, Any]] = []
    if mode == "playlist":
        ids = [str(x) for x in payload.get("playlist_ids", [])]
        supplied = payload.get("playlists") or []
        supplied_map = {str(p.get("id")): dict(p) for p in supplied if isinstance(p, dict) and p.get("id")}
        with state_lock:
            available = {str(p["id"]): dict(p) for p in (state.get("playlists") or [])}
        merged = {**available, **supplied_map}
        if ids:
            playlist_objs = [merged[i] for i in ids if i in merged]
        if not playlist_objs and source_kind == "channel" and not raw_sources:
            try:
                discovered, _ = discover_playlists(source_url, "channel")
                wanted = set(ids)
                playlist_objs = [p for p in discovered if not wanted or str(p.get("id")) in wanted]
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Could not resolve the selected playlists: {humanize_error(str(exc))}"}), 400
        if not playlist_objs and source_kind == "playlist":
            parsed = urllib.parse.urlparse(source_url)
            pid = urllib.parse.parse_qs(parsed.query).get("list", [""])[0]
            title = clean_text(payload.get("playlist_title") or "Playlist")
            if pid:
                playlist_objs = [{"id": pid, "title": title, "url": f"https://www.youtube.com/playlist?list={urllib.parse.quote(pid, safe='')}", "thumbnail": "", "count": None}]
        if not playlist_objs or any(not clean_text(p.get("url")) for p in playlist_objs):
            return jsonify({"ok": False, "error": "Select at least one valid playlist before extraction."}), 400
    with state_lock:
        if state["status"] in {"discovering", "extracting"}:
            return jsonify({"ok": False, "error": "An extraction is already running."}), 409
        state["extract_id"] += 1
        extract_id = state["extract_id"]
        state["mode"] = mode
        state["source_url"] = source_url
        state["selected_fields"] = selected
        state["selected_playlist_ids"] = [p["id"] for p in playlist_objs]
        state["playlists"] = playlist_objs if mode == "playlist" else []
        if mode != "playlist":
            state["sources"] = []
        state["records"] = []
        state["errors"] = []
    cancel_event.clear()
    threading.Thread(target=extraction_worker, args=(source_url, selected, mode, playlist_objs, extract_id), daemon=True).start()
    return jsonify({"ok": True, "mode": mode})


@app.post("/api/cancel")
def api_cancel() -> Any:
    cancel_event.set()
    with state_lock:
        running = state["status"] in {"discovering", "extracting"}
    if running:
        set_state(message="Stopping extraction safely…")
    return jsonify({"ok": True})


@app.post("/api/export")
def api_export() -> Any:
    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("kind", "")).lower()
    columns = [x for x in payload.get("columns", []) if x in FIELDS]
    ids = payload.get("ids")
    records = get_records_by_ids(ids if isinstance(ids, list) else None)
    with state_lock:
        playlist_mode = state["mode"] == "playlist"
        selected_playlist_order = [str(p.get("id")) for p in state.get("playlists") or []] if playlist_mode else None
    if not columns:
        return jsonify({"ok": False, "error": "Select at least one export column."}), 400
    if not records:
        return jsonify({"ok": False, "error": "There are no records in the current export scope."}), 400
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    ext = {"pdf": "pdf", "csv": "csv", "txt": "txt"}.get(kind)
    if not ext:
        return jsonify({"ok": False, "error": "Unsupported export format."}), 400
    path = DOWNLOAD_DIR / f"youscraper_{stamp}.{ext}"
    try:
        export_records(records, columns, kind, path, playlist_mode=playlist_mode, playlist_order=selected_playlist_order)
    except Exception as exc:
        return jsonify({"ok": False, "error": humanize_error(str(exc))}), 500
    return send_file(path, as_attachment=True, download_name=path.name)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--port", type=int, default=PORT)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app.run(host=HOST, port=args.port, threaded=True, debug=False, use_reloader=False)
